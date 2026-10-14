#!/usr/bin/env python3
"""NET-022 — Does the default Bot Control version label Claude crawlers like Version_6.1 does?

One S3 origin behind two CloudFront distributions, each with its own web ACL:
  default  Bot Control COMMON, no version (whatever AWS currently serves as default)
  v61      Bot Control COMMON, pinned to Version_6.1
Every Bot Control rule is overridden to Count, so nothing is blocked and the WAF log shows only
the labels each version adds. The same User-Agents hit both distributions; `labels` puts the two
results side by side.

deploy             Bucket + OAC, two web ACLs with logging, two distributions
status             Versions in use, distribution state, a quick 200 check
test               Send every lab User-Agent to both distributions (prints the run tag)
labels [RUN]       Read the WAF logs for a run and compare labels per User-Agent
teardown           Delete everything and verify

Usage:
    python deploy_lab.py deploy --profile lab338
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

RESOURCE_FILE = Path(__file__).parent / "resources.json"

PREFIX = "NET-022"
REGION = "us-east-1"                   # CLOUDFRONT-scope WAF resources live here
BOT_CONTROL = "AWSManagedRulesBotControlRuleSet"
LABEL_NS = "awswaf:managed:aws:bot-control:"
CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
TAGS = [{"Key": "Project", "Value": PREFIX}]

# variant key -> pinned version (None = no VersionName, i.e. the current default)
VARIANTS = {"default": None, "v61": "Version_6.1"}

USER_AGENTS = {
    "claudebot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "claude-searchbot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +Claude-SearchBot@anthropic.com)",
    "claude-user": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)",
    "gptbot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.1; +https://openai.com/gptbot)",
    "exampleaibot": "ExampleAIBot/1.0",
    "curl": "curl/8.7.1",
    "browser": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
}

INDEX_HTML = "<!doctype html><title>NET-022</title><h1>NET-022 marketing page</h1><p>Public content.</p>\n"


def get_session(profile):
    return boto3.Session(profile_name=profile, region_name=REGION)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def acl_name(variant):
    return f"{PREFIX}-acl-{variant}"


def log_group(variant):
    return f"aws-waf-logs-lab-{variant}"   # WAF requires the aws-waf-logs- prefix


def visibility(name):
    return {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": name.replace("-", "")}


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

def deploy_bucket(sess, r):
    s3 = sess.client("s3")
    bucket = f"net022-lab-{r['account']}"
    print(f"\n[S3] bucket {bucket} (private, Block Public Access on)")
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
            raise
    r["bucket"] = bucket
    save(r)
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": TAGS})
    s3.put_object(Bucket=bucket, Key="index.html", Body=INDEX_HTML.encode(), ContentType="text/html")
    print("  index.html uploaded")

    cf = sess.client("cloudfront")
    if not r.get("oac_id"):
        oac = cf.create_origin_access_control(OriginAccessControlConfig={
            "Name": f"{PREFIX}-oac", "Description": "NET-022 lab",
            "SigningProtocol": "sigv4", "SigningBehavior": "always", "OriginAccessControlOriginType": "s3"})
        r["oac_id"] = oac["OriginAccessControl"]["Id"]
        save(r)
    print(f"[CloudFront] origin access control {r['oac_id']} (CloudFront signs requests to S3)")


def bot_control_rule_names(wafv2, version):
    kw = {"VersionName": version} if version else {}
    d = wafv2.describe_managed_rule_group(VendorName="AWS", Name=BOT_CONTROL, Scope="CLOUDFRONT", **kw)
    return [x["Name"] for x in d["Rules"]], len(d.get("AvailableLabels", []))


def deploy_acls(sess, r):
    wafv2 = sess.client("wafv2")
    logs = sess.client("logs")
    versions = wafv2.list_available_managed_rule_group_versions(VendorName="AWS", Name=BOT_CONTROL, Scope="CLOUDFRONT")
    r["default_version_at_deploy"] = versions.get("CurrentDefaultVersion")
    print(f"\n[WAF] Bot Control current default version: {r['default_version_at_deploy']}")
    r.setdefault("acls", {})
    for variant, version in VARIANTS.items():
        name = acl_name(variant)
        if variant in r["acls"]:
            print(f"  {name}: already created")
            continue
        rules, nlabels = bot_control_rule_names(wafv2, version)
        statement = {"ManagedRuleGroupStatement": {
            "VendorName": "AWS", "Name": BOT_CONTROL,
            "ManagedRuleGroupConfigs": [{"AWSManagedRulesBotControlRuleSet": {"InspectionLevel": "COMMON"}}],
            # every rule to Count: the rules still add their labels, none terminates
            "RuleActionOverrides": [{"Name": n, "ActionToUse": {"Count": {}}} for n in rules]}}
        if version:
            statement["ManagedRuleGroupStatement"]["Version"] = version
        acl = wafv2.create_web_acl(
            Name=name, Scope="CLOUDFRONT", DefaultAction={"Allow": {}},
            Description=f"NET-022 Bot Control {version or 'default version'}, all rules Count",
            Rules=[{"Name": "BotControl", "Priority": 0, "Statement": statement,
                    "OverrideAction": {"None": {}}, "VisibilityConfig": visibility(f"{name}-bc")}],
            VisibilityConfig=visibility(name), Tags=TAGS)["Summary"]
        r["acls"][variant] = {"name": name, "id": acl["Id"], "arn": acl["ARN"], "version": version}
        save(r)
        print(f"  {name}: Bot Control COMMON {version or '(no version → ' + str(r['default_version_at_deploy']) + ')'}"
              f" · {len(rules)} rules overridden to Count · {nlabels} available labels")

        group = log_group(variant)
        try:
            logs.create_log_group(logGroupName=group, tags={"Project": PREFIX})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
        logs.put_retention_policy(logGroupName=group, retentionInDays=1)
        group_arn = f"arn:aws:logs:{REGION}:{r['account']}:log-group:{group}"
        for i in range(10):    # a just-created web ACL can take a moment to be visible to logging
            try:
                wafv2.put_logging_configuration(LoggingConfiguration={
                    "ResourceArn": acl["ARN"], "LogDestinationConfigs": [group_arn]})
                break
            except ClientError as e:
                if e.response["Error"]["Code"] != "WAFNonexistentItemException" or i == 9:
                    raise
                time.sleep(3)
        r["acls"][variant]["log_group"] = group
        save(r)
        print(f"    logging → CloudWatch Logs {group} (1-day retention)")


def deploy_distributions(sess, r):
    cf = sess.client("cloudfront")
    r.setdefault("distributions", {})
    print("\n[CloudFront] distributions (one per web ACL, same S3 origin)")
    for variant, acl in r["acls"].items():
        if variant in r["distributions"]:
            print(f"  {variant}: already created")
            continue
        cfg = {
            "CallerReference": f"{PREFIX}-{variant}-{int(time.time())}",
            "Comment": f"{PREFIX} {variant}",
            "Enabled": True,
            "DefaultRootObject": "index.html",
            "PriceClass": "PriceClass_100",
            "WebACLId": acl["arn"],
            "Origins": {"Quantity": 1, "Items": [{
                "Id": "s3", "DomainName": f"{r['bucket']}.s3.{REGION}.amazonaws.com",
                "S3OriginConfig": {"OriginAccessIdentity": ""}, "OriginAccessControlId": r["oac_id"]}]},
            "DefaultCacheBehavior": {"TargetOriginId": "s3", "ViewerProtocolPolicy": "redirect-to-https",
                                     "CachePolicyId": CACHING_OPTIMIZED, "Compress": True},
        }
        d = cf.create_distribution_with_tags(DistributionConfigWithTags={
            "DistributionConfig": cfg, "Tags": {"Items": TAGS}})["Distribution"]
        r["distributions"][variant] = {"id": d["Id"], "arn": d["ARN"], "domain": d["DomainName"]}
        save(r)
        print(f"  {variant}: {d['Id']} https://{d['DomainName']}  → web ACL {acl['name']}")

    # the bucket only answers CloudFront, and only these two distributions
    policy = {"Version": "2012-10-17", "Statement": [{
        "Sid": "AllowCloudFrontOAC", "Effect": "Allow",
        "Principal": {"Service": "cloudfront.amazonaws.com"},
        "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{r['bucket']}/*",
        "Condition": {"StringEquals": {"AWS:SourceArn": [d["arn"] for d in r["distributions"].values()]}}}]}
    sess.client("s3").put_bucket_policy(Bucket=r["bucket"], Policy=json.dumps(policy))
    print("  bucket policy: s3:GetObject only for these two distributions (AWS:SourceArn)")


def deploy(profile):
    sess = get_session(profile)
    r = load()
    r["account"] = sess.client("sts").get_caller_identity()["Account"]
    save(r)
    print(f"account {r['account']} · region {REGION} (CLOUDFRONT scope)")
    deploy_bucket(sess, r)
    deploy_acls(sess, r)
    deploy_distributions(sess, r)

    print("\nwaiting for both distributions to reach Deployed (usually 3–8 min)...")
    cf = sess.client("cloudfront")
    for variant, d in r["distributions"].items():
        cf.get_waiter("distribution_deployed").wait(Id=d["id"], WaiterConfig={"Delay": 20, "MaxAttempts": 45})
        print(f"  {variant}: Deployed")
    print("\nnext: python deploy_lab.py test --profile", profile)


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS / TEST / LABELS
# ═══════════════════════════════════════════════════════════════════════════════

def need(r):
    if not r.get("distributions"):
        raise SystemExit("nothing deployed (resources.json has no distributions)")


def fetch(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def status(profile, _=None):
    r = load()
    need(r)
    sess = get_session(profile)
    cf, wafv2 = sess.client("cloudfront"), sess.client("wafv2")
    print(f"Bot Control default version now: "
          f"{wafv2.list_available_managed_rule_group_versions(VendorName='AWS', Name=BOT_CONTROL, Scope='CLOUDFRONT').get('CurrentDefaultVersion')}"
          f" (at deploy: {r.get('default_version_at_deploy')})")
    for variant, d in r["distributions"].items():
        dist = cf.get_distribution(Id=d["id"])["Distribution"]
        code = fetch(f"https://{d['domain']}/", USER_AGENTS["browser"])
        print(f"  {variant}: {d['id']} {dist['Status']} · web ACL {r['acls'][variant]['name']}"
              f" ({r['acls'][variant]['version'] or 'default'}) · browser GET → {code}")


def test(profile, _=None):
    r = load()
    need(r)
    run = datetime.now(timezone.utc).strftime("%H%M%S")
    print(f"run {run}: every User-Agent → both distributions (all Bot Control rules in Count, so expect 200)")
    for key, ua in USER_AGENTS.items():
        codes = []
        for variant, d in r["distributions"].items():
            codes.append(f"{variant} {fetch(f'https://{d['domain']}/?run={run}&ua={key}', ua)}")
        print(f"  {key:<17} {' · '.join(codes)}")
    r.setdefault("runs", []).append({"run": run, "at": time.time()})
    save(r)
    print(f"\nWAF logs take ~1–5 min to arrive. next: python deploy_lab.py labels {run} --profile {profile}")


def short_label(name):
    return name[len(LABEL_NS):] if name.startswith(LABEL_NS) else name


def read_run(logs, group, run, since_ms):
    """ua key -> (labels, terminatingRuleId, counted rules) for the requests of one run"""
    out, token = {}, None
    while True:
        kw = {"logGroupName": group, "startTime": since_ms, "filterPattern": f'"run={run}"'}
        if token:
            kw["nextToken"] = token
        page = logs.filter_log_events(**kw)
        for ev in page["events"]:
            m = json.loads(ev["message"])
            args = dict(a.split("=", 1) for a in m["httpRequest"].get("args", "").split("&") if "=" in a)
            if args.get("run") != run:
                continue
            counted = [x["ruleId"] for g in m.get("ruleGroupList") or []
                       for x in (g.get("nonTerminatingMatchingRules") or [])]
            out[args.get("ua")] = (sorted(short_label(l["name"]) for l in m.get("labels") or []),
                                   m.get("terminatingRuleId"), counted, m.get("action"))
        token = page.get("nextToken")
        if not token:
            return out


def labels(profile, args):
    r = load()
    need(r)
    runs = r.get("runs") or []
    if not runs:
        raise SystemExit("no runs yet: python deploy_lab.py test first")
    run = args[0] if args else runs[-1]["run"]
    at = next((x["at"] for x in runs if x["run"] == run), time.time() - 3600)
    logs = get_session(profile).client("logs")
    since = int((at - 120) * 1000)

    results = {}
    deadline = time.time() + 600
    while True:
        results = {v: read_run(logs, r["acls"][v]["log_group"], run, since) for v in r["acls"]}
        if all(len(results[v]) == len(USER_AGENTS) for v in results) or time.time() > deadline:
            break
        print(f"  logs so far: " + ", ".join(f"{v} {len(results[v])}/{len(USER_AGENTS)}" for v in results)
              + " — waiting 30 s")
        time.sleep(30)

    versions = {v: r["acls"][v]["version"] or f"default ({r.get('default_version_at_deploy')})" for v in r["acls"]}
    print(f"\nrun {run} · labels without the {LABEL_NS} prefix")
    for key in USER_AGENTS:
        print(f"\n{key}")
        for v in r["acls"]:
            if key not in results[v]:
                print(f"  {versions[v]:<22} (no log entry yet)")
                continue
            lbls, term, counted, action = results[v][key]
            print(f"  {versions[v]:<22} action {action} · terminating {term}")
            print(f"  {'':<22} labels: {', '.join(lbls) or '(none)'}")
            if counted:
                print(f"  {'':<22} counted rules: {', '.join(counted)}")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

GONE = ("NoSuchDistribution", "NoSuchOriginAccessControl", "WAFNonexistentItemException",
        "ResourceNotFoundException", "NoSuchBucket")


def safe(label, fn, *a, **kw):
    try:
        fn(*a, **kw)
        print(f"  deleted {label}")
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in GONE:
            print(f"  {label}: already gone")
            return True
        print(f"  {label}: {code} {e.response['Error'].get('Message', '')[:160]}")
        return False


def disable_distribution(cf, dist_id):
    """Detach the web ACL and disable; returns True when there is nothing left to wait for"""
    try:
        res = cf.get_distribution_config(Id=dist_id)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchDistribution":
            return True
        raise
    cfg = res["DistributionConfig"]
    if cfg["Enabled"] or cfg.get("WebACLId"):
        cfg["Enabled"], cfg["WebACLId"] = False, ""
        cf.update_distribution(Id=dist_id, IfMatch=res["ETag"], DistributionConfig=cfg)
        print(f"  {dist_id}: web ACL detached, disabled")
    return False


def teardown(profile, _=None):
    r = load()
    if not r:
        raise SystemExit("resources.json not found: nothing recorded to tear down")
    sess = get_session(profile)
    cf, wafv2, logs, s3 = (sess.client(x) for x in ("cloudfront", "wafv2", "logs", "s3"))
    ok = True

    print("[CloudFront] disable distributions and detach the web ACLs")
    pending = [d["id"] for d in r.get("distributions", {}).values() if not disable_distribution(cf, d["id"])]
    for dist_id in pending:
        print(f"  waiting for {dist_id} to finish deploying the disabled config (3–10 min)...")
        try:
            cf.get_waiter("distribution_deployed").wait(Id=dist_id, WaiterConfig={"Delay": 20, "MaxAttempts": 60})
        except WaiterError:
            print(f"  {dist_id}: still not Deployed, run teardown again later")
            ok = False
            continue
        etag = cf.get_distribution_config(Id=dist_id)["ETag"]
        ok &= safe(f"distribution {dist_id}", cf.delete_distribution, Id=dist_id, IfMatch=etag)

    print("[WAF] logging, web ACLs")
    for acl in r.get("acls", {}).values():
        safe(f"logging config {acl['name']}", wafv2.delete_logging_configuration, ResourceArn=acl["arn"])
        try:
            lock = wafv2.get_web_acl(Name=acl["name"], Scope="CLOUDFRONT", Id=acl["id"])["LockToken"]
            ok &= safe(f"web ACL {acl['name']}", wafv2.delete_web_acl, Name=acl["name"], Scope="CLOUDFRONT",
                       Id=acl["id"], LockToken=lock)
        except ClientError as e:
            if e.response["Error"]["Code"] != "WAFNonexistentItemException":
                print(f"  web ACL {acl['name']}: {e.response['Error']['Code']}")
                ok = False
        if acl.get("log_group"):
            ok &= safe(f"log group {acl['log_group']}", logs.delete_log_group, logGroupName=acl["log_group"])

    print("[CloudFront] origin access control")
    if r.get("oac_id"):
        try:
            etag = cf.get_origin_access_control(Id=r["oac_id"])["ETag"]
            ok &= safe(f"OAC {r['oac_id']}", cf.delete_origin_access_control, Id=r["oac_id"], IfMatch=etag)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchOriginAccessControl":
                print(f"  OAC: {e.response['Error']['Code']}")
                ok = False

    print("[S3] bucket")
    if r.get("bucket"):
        try:
            objs = s3.list_object_versions(Bucket=r["bucket"])
            for o in objs.get("Versions", []) + objs.get("DeleteMarkers", []):
                s3.delete_object(Bucket=r["bucket"], Key=o["Key"], VersionId=o["VersionId"])
            ok &= safe(f"bucket {r['bucket']}", s3.delete_bucket, Bucket=r["bucket"])
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchBucket":
                print(f"  bucket: {e.response['Error']['Code']}")
                ok = False

    verify(sess, r, ok)


def verify(sess, r, ok):
    print("\n[verify]")
    left = []
    cf, wafv2, logs, s3 = (sess.client(x) for x in ("cloudfront", "wafv2", "logs", "s3"))
    for d in r.get("distributions", {}).values():
        try:
            cf.get_distribution(Id=d["id"])
            left.append(f"distribution {d['id']}")
        except ClientError:
            pass
    names = {a["Name"] for a in wafv2.list_web_acls(Scope="CLOUDFRONT")["WebACLs"]}
    left += [f"web ACL {a['name']}" for a in r.get("acls", {}).values() if a["name"] in names]
    for a in r.get("acls", {}).values():
        if logs.describe_log_groups(logGroupNamePrefix=a.get("log_group", "-"))["logGroups"]:
            left.append(f"log group {a['log_group']}")
    if r.get("oac_id"):
        try:
            cf.get_origin_access_control(Id=r["oac_id"])
            left.append(f"OAC {r['oac_id']}")
        except ClientError:
            pass
    if r.get("bucket"):
        try:
            s3.head_bucket(Bucket=r["bucket"])
            left.append(f"bucket {r['bucket']}")
        except ClientError:
            pass
    if left or not ok:
        print(f"  STILL PRESENT: {left or '(a delete failed above)'} — resources.json kept, run teardown again")
        raise SystemExit(1)
    print("  nothing left")
    RESOURCE_FILE.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    commands = {"deploy": deploy, "status": status, "test": test, "labels": labels, "teardown": teardown}
    parser = argparse.ArgumentParser(description="NET-022 Bot Control version label comparison lab")
    parser.add_argument("command", choices=list(commands))
    parser.add_argument("args", nargs="*")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    a = parser.parse_args()
    if not a.profile:
        raise SystemExit("--profile is required (or set AWS_PROFILE)")
    if a.command == "deploy":
        deploy(a.profile)
    else:
        commands[a.command](a.profile, a.args)


if __name__ == "__main__":
    main()
