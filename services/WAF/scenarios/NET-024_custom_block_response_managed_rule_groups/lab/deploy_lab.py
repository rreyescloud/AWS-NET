#!/usr/bin/env python3
"""NET-024 — Where does a WAF block response come from? Custom responses vs managed rule groups.

CloudFront (S3 origin via OAC) + CLOUDFRONT-scope web ACL with two AWS managed rule groups, one own
Block rule with a custom response and one own Allow rule. Priorities simulate the Firewall Manager
order: 10-29 "preProcess", 35-40 "account rules (retrofit)", 50+ "postProcess".

deploy                 S3 bucket + pages, OAC, web ACL (state "baseline"), WAF logging, distribution
status                 Current state, rules in priority order, distribution status, error responses
set-state STATE        baseline | fixA | fixB | fixB-post   (see lab/README.md)
probe NAME|all         ok | xss | badinput | own | partner-xss | missing
teardown               Disable + delete distribution, web ACL, logging, OAC, bucket — then verify

Usage:
    python deploy_lab.py deploy --profile <lab-profile>
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

RESOURCE_FILE = Path(__file__).parent / "resources.json"   # gitignored
PREFIX = "NET-024"
REGION = "us-east-1"                  # CLOUDFRONT-scope WAF + CloudFront API
ACL = f"{PREFIX}-portal-acl"
LOG_GROUP = f"aws-waf-logs-{PREFIX}"
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"   # AWS managed cache policy
TAGS = [{"Key": "Project", "Value": PREFIX}]
STATES = ("baseline", "fixA", "fixB", "fixB-post")

BLOCK_PAGE = ("<!doctype html><title>Blocked</title><h1>Request blocked</h1>"
              "<p>Source: WAF custom response (blockpage).</p>")
CF_403_PAGE = ("<!doctype html><title>Blocked</title><h1>Request blocked</h1>"
               "<p>Source: CloudFront custom error response (/errors/403.html).</p>")
INDEX = "<!doctype html><title>NET-024</title><h1>Developer portal (lab)</h1>"

PROBES = {
    "ok":          ("/", {}),
    "xss":         ("/?q=" + urllib.parse.quote("<script>alert(1)</script>"), {}),
    "badinput":    ("/?x=" + urllib.parse.quote("${jndi:ldap://example.com/a}"), {}),
    "own":         ("/blocked-own", {}),
    "partner-xss": ("/?q=" + urllib.parse.quote("<script>alert(1)</script>"), {"x-lab-partner": "trusted"}),
    "missing":     ("/missing.html", {}),
}


def session(profile):
    return boto3.Session(profile_name=profile, region_name=REGION)


def load():
    return json.loads(RESOURCE_FILE.read_text()) if RESOURCE_FILE.exists() else {}


def save(st):
    RESOURCE_FILE.write_text(json.dumps(st, indent=2))


def step(n, text):
    print(f"\n[{n}] {text}")


def vis(name):
    return {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": name}


def namespace_of(waf, group):
    """Derive the label namespace of a managed group from its AvailableLabels."""
    labels = [l["Name"] for l in waf.describe_managed_rule_group(
        VendorName="AWS", Name=group, Scope="CLOUDFRONT").get("AvailableLabels", [])]
    ns = Counter(l.rsplit(":", 1)[0] + ":" for l in labels).most_common(1)
    return ns[0][0] if ns else None


# --------------------------------------------------------------------------- web ACL states
def build_rules(state, ns):
    count = state.startswith("fixB")
    managed = lambda name, prio, metric: {
        "Name": metric, "Priority": prio,
        "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": name}},
        "OverrideAction": {"Count": {}} if count else {"None": {}},
        "VisibilityConfig": vis(metric)}
    rules = [
        managed("AWSManagedRulesCommonRuleSet", 10, "pre-AWS-CommonRuleSet"),
        managed("AWSManagedRulesKnownBadInputsRuleSet", 20, "pre-AWS-KnownBadInputs"),
        {"Name": "acct-OwnBlockPath", "Priority": 35,
         "Statement": {"ByteMatchStatement": {"SearchString": b"/blocked-own", "FieldToMatch": {"UriPath": {}},
                       "TextTransformations": [{"Priority": 0, "Type": "NONE"}], "PositionalConstraint": "STARTS_WITH"}},
         "Action": {"Block": {"CustomResponse": {"ResponseCode": 403, "CustomResponseBodyKey": "blockpage",
                    "ResponseHeaders": [{"Name": "lab-source", "Value": "own-rule"}]}}},
         "VisibilityConfig": vis("acct-OwnBlockPath")},
        {"Name": "acct-AllowPartner", "Priority": 40,
         "Statement": {"ByteMatchStatement": {"SearchString": b"trusted",
                       "FieldToMatch": {"SingleHeader": {"Name": "x-lab-partner"}},
                       "TextTransformations": [{"Priority": 0, "Type": "NONE"}], "PositionalConstraint": "EXACTLY"}},
         "Action": {"Allow": {}}, "VisibilityConfig": vis("acct-AllowPartner")},
    ]
    if count:
        prio, name = (30, "pre-BlockManagedMatches") if state == "fixB" else (50, "post-BlockManagedMatches")
        rules.append({
            "Name": name, "Priority": prio,
            "Statement": {"OrStatement": {"Statements": [
                {"LabelMatchStatement": {"Scope": "NAMESPACE", "Key": k}} for k in ns.values()]}},
            "Action": {"Block": {"CustomResponse": {"ResponseCode": 403, "CustomResponseBodyKey": "blockpage",
                       "ResponseHeaders": [{"Name": "lab-source", "Value": "label-block"}]}}},
            "VisibilityConfig": vis(name)})
    return sorted(rules, key=lambda r: r["Priority"])


def acl_args(state, ns):
    return dict(Scope="CLOUDFRONT", DefaultAction={"Allow": {}}, Rules=build_rules(state, ns),
                CustomResponseBodies={"blockpage": {"ContentType": "TEXT_HTML", "Content": BLOCK_PAGE}},
                VisibilityConfig=vis(ACL))


# --------------------------------------------------------------------------- deploy
def deploy(profile):
    s = session(profile)
    acct = s.client("sts").get_caller_identity()["Account"]
    st = load()
    s3, cf, waf, logs = s.client("s3"), s.client("cloudfront"), s.client("wafv2"), s.client("logs")

    bucket = st.get("bucket") or f"net-024-{acct}-{uuid.uuid4().hex[:6]}"
    step(1, f"S3 bucket (private, all public access blocked): index.html + errors/403.html")
    if "bucket" not in st:
        s3.create_bucket(Bucket=bucket)
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": TAGS})
    s3.put_object(Bucket=bucket, Key="index.html", Body=INDEX.encode(), ContentType="text/html")
    s3.put_object(Bucket=bucket, Key="errors/403.html", Body=CF_403_PAGE.encode(), ContentType="text/html")
    st["bucket"] = bucket
    save(st)

    step(2, "Origin Access Control (CloudFront signs requests to S3)")
    if "oac_id" not in st:
        st["oac_id"] = cf.create_origin_access_control(OriginAccessControlConfig={
            "Name": f"{PREFIX}-oac", "Description": PREFIX, "SigningProtocol": "sigv4",
            "SigningBehavior": "always", "OriginAccessControlOriginType": "s3"})["OriginAccessControl"]["Id"]
        save(st)

    step(3, "Label namespaces of the two managed groups (from DescribeManagedRuleGroup)")
    ns = {"crs": namespace_of(waf, "AWSManagedRulesCommonRuleSet"),
          "kbi": namespace_of(waf, "AWSManagedRulesKnownBadInputsRuleSet")}
    print("   ", ns)
    st["namespaces"] = ns

    step(4, f"Web ACL {ACL} (CLOUDFRONT scope), state 'baseline'")
    if "acl_id" not in st:
        out = waf.create_web_acl(Name=ACL, Tags=TAGS, **acl_args("baseline", ns))["Summary"]
        st["acl_id"], st["acl_arn"] = out["Id"], out["ARN"]
    st["state"] = "baseline"
    save(st)

    step(5, f"WAF logging -> CloudWatch Logs {LOG_GROUP} (7-day retention)")
    try:
        logs.create_log_group(logGroupName=LOG_GROUP, tags={"Project": PREFIX})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=7)
    waf.put_logging_configuration(LoggingConfiguration={
        "ResourceArn": st["acl_arn"], "LogDestinationConfigs": [f"arn:aws:logs:{REGION}:{acct}:log-group:{LOG_GROUP}"]})

    step(6, "CloudFront distribution: S3 origin via OAC, caching disabled, web ACL attached, NO custom error responses")
    if "dist_id" not in st:
        d = cf.create_distribution_with_tags(DistributionConfigWithTags={"Tags": {"Items": TAGS}, "DistributionConfig": {
            "CallerReference": f"{PREFIX}-{uuid.uuid4().hex[:8]}", "Comment": f"{PREFIX} lab", "Enabled": True,
            "DefaultRootObject": "index.html", "PriceClass": "PriceClass_100", "HttpVersion": "http2",
            "Origins": {"Quantity": 1, "Items": [{
                "Id": "s3", "DomainName": f"{bucket}.s3.{REGION}.amazonaws.com",
                "S3OriginConfig": {"OriginAccessIdentity": ""}, "OriginAccessControlId": st["oac_id"]}]},
            "DefaultCacheBehavior": {
                "TargetOriginId": "s3", "ViewerProtocolPolicy": "redirect-to-https", "Compress": True,
                "CachePolicyId": CACHING_DISABLED,
                "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                                   "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}}},
            "CustomErrorResponses": {"Quantity": 0},
            "WebACLId": st["acl_arn"]}})["Distribution"]
        st["dist_id"], st["domain"], st["dist_arn"] = d["Id"], d["DomainName"], d["ARN"]
        save(st)

    step(7, "Bucket policy: only this distribution may read (AWS:SourceArn)")
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({"Version": "2012-10-17", "Statement": [{
        "Sid": "AllowCloudFrontOAC", "Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"},
        "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*",
        "Condition": {"StringEquals": {"AWS:SourceArn": st["dist_arn"]}}}]}))

    step(8, "Wait for the distribution to deploy (5-15 min)")
    cf.get_waiter("distribution_deployed").wait(Id=st["dist_id"], WaiterConfig={"Delay": 30, "MaxAttempts": 60})
    print(f"\nDone. https://{st['domain']}/   state=baseline\nNo probes were sent.")


# --------------------------------------------------------------------------- state switching
def set_cf_error_page(cf, dist_id, enabled):
    cfg = cf.get_distribution_config(Id=dist_id)
    dc, etag = cfg["DistributionConfig"], cfg["ETag"]
    dc["CustomErrorResponses"] = ({"Quantity": 1, "Items": [{
        "ErrorCode": 403, "ResponsePagePath": "/errors/403.html", "ResponseCode": "403", "ErrorCachingMinTTL": 10}]}
        if enabled else {"Quantity": 0})
    cf.update_distribution(Id=dist_id, IfMatch=etag, DistributionConfig=dc)
    print("   CloudFront custom error response 403:", "ON" if enabled else "OFF", "(propagating)")
    cf.get_waiter("distribution_deployed").wait(Id=dist_id, WaiterConfig={"Delay": 20, "MaxAttempts": 60})


def set_state(profile, state):
    s, st = session(profile), load()
    waf, cf = s.client("wafv2"), s.client("cloudfront")
    acl_state = "baseline" if state == "fixA" else state
    cur = waf.get_web_acl(Name=ACL, Scope="CLOUDFRONT", Id=st["acl_id"])
    waf.update_web_acl(Name=ACL, Id=st["acl_id"], LockToken=cur["LockToken"], **acl_args(acl_state, st["namespaces"]))
    print(f"   web ACL rules set for '{acl_state}'")
    want_page = state == "fixA"
    have_page = cf.get_distribution_config(Id=st["dist_id"])["DistributionConfig"]["CustomErrorResponses"]["Quantity"] > 0
    if want_page != have_page:
        set_cf_error_page(cf, st["dist_id"], want_page)
    st["state"] = state
    save(st)
    print(f"State is now '{state}'. Give WAF ~1 minute to propagate before probing.")


# --------------------------------------------------------------------------- status / probe
def status(profile):
    s, st = session(profile), load()
    if not st:
        print("Nothing deployed.")
        return
    waf, cf = s.client("wafv2"), s.client("cloudfront")
    print(f"state: {st.get('state')}   url: https://{st['domain']}/")
    print("namespaces:", st.get("namespaces"))
    acl = waf.get_web_acl(Name=ACL, Scope="CLOUDFRONT", Id=st["acl_id"])["WebACL"]
    for r in sorted(acl["Rules"], key=lambda x: x["Priority"]):
        act = r.get("Action") or r.get("OverrideAction")
        print(f"  {r['Priority']:>3}  {r['Name']:<26} {list(act)[0]}")
    d = cf.get_distribution(Id=st["dist_id"])["Distribution"]
    print("distribution:", d["Status"], "| custom error responses:",
          d["DistributionConfig"]["CustomErrorResponses"]["Quantity"])


def probe(profile, name):
    st = load()
    names = list(PROBES) if name == "all" else [name]
    for n in names:
        path, hdr = PROBES[n]
        req = urllib.request.Request(f"https://{st['domain']}{path}", headers={"User-Agent": "net024-lab/1.0", **hdr})
        try:
            r = urllib.request.urlopen(req, timeout=10)
            code, h, body = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            code, h, body = e.code, e.headers, e.read()
        src = h.get("lab-source") or ("cloudfront-403-page" if b"CloudFront custom error" in body else
                                       "waf-default-403" if code == 403 and b"Request blocked" not in body else "-")
        print(f"  {n:<12} {code}  source={src:<20} x-cache={h.get('x-cache', '')}")


# --------------------------------------------------------------------------- teardown
def teardown(profile):
    s, st = session(profile), load()
    s3, cf, waf, logs = s.client("s3"), s.client("cloudfront"), s.client("wafv2"), s.client("logs")

    def quiet(fn, *a, **k):
        try:
            return fn(*a, **k)
        except ClientError as e:
            print("   skip:", e.response["Error"]["Code"])

    if st.get("dist_id"):
        step(1, "Disable the distribution and wait (5-15 min)")
        cfg = cf.get_distribution_config(Id=st["dist_id"])
        if cfg["DistributionConfig"]["Enabled"]:
            cfg["DistributionConfig"]["Enabled"] = False
            cfg["DistributionConfig"]["WebACLId"] = ""
            cf.update_distribution(Id=st["dist_id"], IfMatch=cfg["ETag"], DistributionConfig=cfg["DistributionConfig"])
        cf.get_waiter("distribution_deployed").wait(Id=st["dist_id"], WaiterConfig={"Delay": 30, "MaxAttempts": 60})
        step(2, "Delete the distribution")
        quiet(cf.delete_distribution, Id=st["dist_id"], IfMatch=cf.get_distribution_config(Id=st["dist_id"])["ETag"])
    if st.get("oac_id"):
        step(3, "Delete the OAC")
        quiet(lambda: cf.delete_origin_access_control(Id=st["oac_id"],
              IfMatch=cf.get_origin_access_control(Id=st["oac_id"])["ETag"]))
    if st.get("acl_arn"):
        step(4, "Delete WAF logging and the web ACL")
        quiet(waf.delete_logging_configuration, ResourceArn=st["acl_arn"])
        for _ in range(12):
            try:
                lt = waf.get_web_acl(Name=ACL, Scope="CLOUDFRONT", Id=st["acl_id"])["LockToken"]
                waf.delete_web_acl(Name=ACL, Scope="CLOUDFRONT", Id=st["acl_id"], LockToken=lt)
                break
            except ClientError as e:
                if e.response["Error"]["Code"] == "WAFNonexistentItemException":
                    break
                time.sleep(10)
    step(5, "Delete the log group")
    quiet(logs.delete_log_group, logGroupName=LOG_GROUP)
    if st.get("bucket"):
        step(6, "Empty and delete the bucket")
        for o in s3.list_objects_v2(Bucket=st["bucket"]).get("Contents", []):
            s3.delete_object(Bucket=st["bucket"], Key=o["Key"])
        quiet(s3.delete_bucket, Bucket=st["bucket"])

    step(7, "Verify (every resource type, plus a tag search)")
    left = [a["Name"] for a in waf.list_web_acls(Scope="CLOUDFRONT")["WebACLs"] if a["Name"] == ACL]
    left += [d["Id"] for d in cf.list_distributions().get("DistributionList", {}).get("Items", [])
             if d.get("Comment") == f"{PREFIX} lab"]
    left += [g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=LOG_GROUP)["logGroups"]]
    left += [b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith("net-024-")]
    tagged = s.client("resourcegroupstaggingapi").get_resources(
        TagFilters=[{"Key": "Project", "Values": [PREFIX]}])["ResourceTagMappingList"]
    left += [t["ResourceARN"].split(":")[2] + " (tagged)" for t in tagged]
    print("   leftovers:", left or "none")
    if not left and RESOURCE_FILE.exists():
        RESOURCE_FILE.unlink()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["deploy", "status", "set-state", "probe", "teardown"])
    p.add_argument("arg", nargs="?")
    p.add_argument("--profile", required=True)
    a = p.parse_args()
    if a.command == "set-state" and a.arg not in STATES:
        p.error(f"set-state needs one of {STATES}")
    if a.command == "probe" and a.arg not in list(PROBES) + ["all"]:
        p.error(f"probe needs one of {list(PROBES) + ['all']}")
    {"deploy": lambda: deploy(a.profile), "status": lambda: status(a.profile),
     "set-state": lambda: set_state(a.profile, a.arg), "probe": lambda: probe(a.profile, a.arg),
     "teardown": lambda: teardown(a.profile)}[a.command]()
