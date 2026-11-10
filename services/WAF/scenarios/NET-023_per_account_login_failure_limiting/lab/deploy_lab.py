#!/usr/bin/env python3
"""NET-023 — Per-account login failure limiting: why WAF can't count failures, and where the counter lives.

A fake OAuth token endpoint (API Gateway REST -> Lambda -> DynamoDB counter, fake IdP inside the
Lambda) behind a REGIONAL web ACL. The test sends a distributed attack (many fake source IPs via
X-Forwarded-For) and shows which layer stops it.

deploy [--atp]     DynamoDB table + TTL, IAM role, Lambda, REST API (/token, stage "lab"),
                   web ACL (rate-based per forwarded IP, optional ATP in Count), WAF logging,
                   association. --atp adds AWSManagedRulesATPRuleSet (extra cost).
status             Resources, web ACL rules, current counter items
test               Four phases: legit login, distributed attack, legit user locked, single-IP flood
logs [RUN]         WAF log summary for a run: action, terminating rule, ATP labels
teardown           Delete everything and verify

Usage:
    python deploy_lab.py deploy --profile <lab-profile>
    python deploy_lab.py test --profile <lab-profile>
    python deploy_lab.py teardown --profile <lab-profile>
"""

import argparse
import io
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

RESOURCE_FILE = Path(__file__).parent / "resources.json"   # gitignored

PREFIX = "NET-023"
REGION = "us-east-1"
STAGE = "lab"
TABLE = f"{PREFIX}-login-failures"
ROLE = f"{PREFIX}-token-lambda-role"
FUNCTION = f"{PREFIX}-token"
API = f"{PREFIX}-oauth-api"
ACL = f"{PREFIX}-token-acl"
LOG_GROUP = f"aws-waf-logs-{PREFIX}"          # WAF requires the aws-waf-logs- prefix
TAGS = {"Project": PREFIX}
RATE_LIMIT = 100                              # requests per forwarded IP per 5 minutes
FAIL_LIMIT = 5                                # failed logins per account per minute

# --------------------------------------------------------------------------- Lambda (fake IdP)
LAMBDA_CODE = r'''
import base64, hashlib, json, os, time, urllib.parse
import boto3

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE"])
LIMIT = int(os.environ["FAIL_LIMIT"])
USERS = {"alice": "Correct-Horse-1", "bob": "Battery-Staple-2"}   # fake IdP directory

def resp(code, body, extra=None):
    h = {"Content-Type": "application/json", "x-lab-source": "lambda"}
    h.update(extra or {})
    return {"statusCode": code, "headers": h, "body": json.dumps(body)}

def handler(event, context):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
    if form.get("grant_type") != "password":
        return resp(400, {"error": "unsupported_grant_type"})
    user = form.get("username", "").strip().lower()
    if not user:
        return resp(400, {"error": "invalid_request"})

    now = int(time.time())
    window = now // 60
    key = hashlib.sha256(user.encode()).hexdigest() + "#" + str(window)

    item = TABLE.get_item(Key={"pk": key}, ConsistentRead=True).get("Item", {})
    fails = int(item.get("fails", 0))
    if fails >= LIMIT:                                   # blocked: do not call the IdP
        return resp(429, {"error": "too_many_failed_attempts"},
                    {"Retry-After": str(60 - now % 60), "x-lab-fails": str(fails)})

    if USERS.get(user) == form.get("password"):          # fake IdP check
        return resp(200, {"access_token": "lab-" + hashlib.md5(str(now).encode()).hexdigest(),
                          "token_type": "Bearer", "expires_in": 3600})

    new = TABLE.update_item(
        Key={"pk": key},
        UpdateExpression="ADD fails :one SET #t = :ttl",
        ExpressionAttributeNames={"#t": "ttl"},
        ExpressionAttributeValues={":one": 1, ":ttl": now + 120},
        ReturnValues="UPDATED_NEW")["Attributes"]["fails"]
    return resp(400, {"error": "invalid_grant"}, {"x-lab-fails": str(new)})
'''


# --------------------------------------------------------------------------- helpers
def session(profile):
    return boto3.Session(profile_name=profile, region_name=REGION)


def load():
    return json.loads(RESOURCE_FILE.read_text()) if RESOURCE_FILE.exists() else {}


def save(state):
    RESOURCE_FILE.write_text(json.dumps(state, indent=2))


def step(n, text):
    print(f"\n[{n}] {text}")


def retry(fn, errors, tries=12, wait=5):
    for i in range(tries):
        try:
            return fn()
        except ClientError as e:
            if e.response["Error"]["Code"] not in errors or i == tries - 1:
                raise
            time.sleep(wait)


# --------------------------------------------------------------------------- deploy
def deploy(profile, atp):
    s = session(profile)
    acct = s.client("sts").get_caller_identity()["Account"]
    state = load()
    ddb, iam, lam = s.client("dynamodb"), s.client("iam"), s.client("lambda")
    apigw, waf, logs = s.client("apigateway"), s.client("wafv2"), s.client("logs")

    step(1, f"DynamoDB table {TABLE} (on-demand) with TTL on 'ttl'")
    try:
        ddb.create_table(TableName=TABLE, BillingMode="PAY_PER_REQUEST",
                         AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                         KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                         Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceInUseException":
            raise
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    try:
        ddb.update_time_to_live(TableName=TABLE, TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"})
    except ClientError as e:
        if "already enabled" not in str(e).lower():
            raise
    table_arn = ddb.describe_table(TableName=TABLE)["Table"]["TableArn"]
    state["table"] = TABLE

    step(2, f"IAM role {ROLE}: Lambda basic execution + GetItem/UpdateItem on the table only")
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        role_arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                                   Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])["Role"]["Arn"]
        time.sleep(10)                                   # IAM propagation
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
    iam.attach_role_policy(RoleName=ROLE,
                           PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
    iam.put_role_policy(RoleName=ROLE, PolicyName="counter-table", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow",
        "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem"], "Resource": table_arn}]}))
    state["role"] = ROLE

    step(3, f"Lambda {FUNCTION} (python3.12): fake OAuth token endpoint + per-account counter")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.py", LAMBDA_CODE)
    env = {"Variables": {"TABLE": TABLE, "FAIL_LIMIT": str(FAIL_LIMIT)}}
    try:
        fn_arn = retry(lambda: lam.create_function(
            FunctionName=FUNCTION, Runtime="python3.12", Role=role_arn, Handler="index.handler",
            Code={"ZipFile": buf.getvalue()}, Timeout=10, MemorySize=256, Environment=env,
            Tags=TAGS)["FunctionArn"], {"InvalidParameterValueException"})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=buf.getvalue())
        fn_arn = lam.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION)
    state["function"] = FUNCTION

    step(4, f"REST API {API}: POST /token -> Lambda proxy, stage '{STAGE}' (HTTP APIs can't use WAF)")
    api_id = state.get("api_id")
    if not api_id:
        api_id = apigw.create_rest_api(name=API, endpointConfiguration={"types": ["REGIONAL"]}, tags=TAGS)["id"]
        root = [r for r in apigw.get_resources(restApiId=api_id)["items"] if r["path"] == "/"][0]["id"]
        res = apigw.create_resource(restApiId=api_id, parentId=root, pathPart="token")["id"]
        apigw.put_method(restApiId=api_id, resourceId=res, httpMethod="POST", authorizationType="NONE")
        apigw.put_integration(restApiId=api_id, resourceId=res, httpMethod="POST", type="AWS_PROXY",
                              integrationHttpMethod="POST",
                              uri=f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{fn_arn}/invocations")
        lam.add_permission(FunctionName=FUNCTION, StatementId="apigw-invoke", Action="lambda:InvokeFunction",
                           Principal="apigateway.amazonaws.com",
                           SourceArn=f"arn:aws:execute-api:{REGION}:{acct}:{api_id}/*/POST/token")
        apigw.create_deployment(restApiId=api_id, stageName=STAGE)
    state["api_id"] = api_id
    state["url"] = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{STAGE}/token"
    stage_arn = f"arn:aws:apigateway:{REGION}::/restapis/{api_id}/stages/{STAGE}"
    save(state)

    step(5, f"Web ACL {ACL} (REGIONAL, default Allow)")
    rules = [{
        "Name": "RateLimitPerForwardedIP", "Priority": 1,
        "Statement": {"RateBasedStatement": {
            "Limit": RATE_LIMIT, "EvaluationWindowSec": 300, "AggregateKeyType": "FORWARDED_IP",
            "ForwardedIPConfig": {"HeaderName": "X-Forwarded-For", "FallbackBehavior": "MATCH"},
            "ScopeDownStatement": {"AndStatement": {"Statements": [
                {"ByteMatchStatement": {"SearchString": b"/token", "FieldToMatch": {"UriPath": {}},
                                        "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
                                        "PositionalConstraint": "ENDS_WITH"}},
                {"ByteMatchStatement": {"SearchString": b"grant_type=password",
                                        "FieldToMatch": {"Body": {"OversizeHandling": "CONTINUE"}},
                                        "TextTransformations": [{"Priority": 0, "Type": "URL_DECODE"}],
                                        "PositionalConstraint": "CONTAINS"}}]}}}},
        "Action": {"Block": {"CustomResponse": {"ResponseCode": 429, "CustomResponseBodyKey": "waf429",
                   "ResponseHeaders": [{"Name": "lab-source", "Value": "waf"}]}}},
        "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True,
                             "MetricName": "RateLimitPerForwardedIP"}}]
    if atp:
        rules.append({
            "Name": "ATP", "Priority": 2, "OverrideAction": {"Count": {}},   # observe labels only
            "Statement": {"ManagedRuleGroupStatement": {
                "VendorName": "AWS", "Name": "AWSManagedRulesATPRuleSet",
                "ManagedRuleGroupConfigs": [{"AWSManagedRulesATPRuleSet": {
                    "LoginPath": f"/{STAGE}/token",
                    "RequestInspection": {"PayloadType": "FORM_ENCODED",
                                          "UsernameField": {"Identifier": "username"},
                                          "PasswordField": {"Identifier": "password"}}}}]}},
            "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True,
                                 "MetricName": "ATP"}})
    acl_args = dict(Scope="REGIONAL", DefaultAction={"Allow": {}}, Rules=rules,
                    CustomResponseBodies={"waf429": {"ContentType": "APPLICATION_JSON",
                                          "Content": '{"error":"rate_limited","source":"waf"}'}},
                    VisibilityConfig={"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True,
                                      "MetricName": ACL})
    if state.get("acl_id"):
        cur = waf.get_web_acl(Name=ACL, Scope="REGIONAL", Id=state["acl_id"])
        waf.update_web_acl(Name=ACL, Id=state["acl_id"], LockToken=cur["LockToken"], **acl_args)
        acl_arn = cur["WebACL"]["ARN"]
    else:
        out = waf.create_web_acl(Name=ACL, Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()], **acl_args)["Summary"]
        state["acl_id"], acl_arn = out["Id"], out["ARN"]
    state["acl_arn"], state["atp"] = acl_arn, atp
    save(state)

    step(6, f"WAF logging -> CloudWatch Logs {LOG_GROUP} (7-day retention)")
    try:
        logs.create_log_group(logGroupName=LOG_GROUP, tags=TAGS)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=7)
    lg_arn = f"arn:aws:logs:{REGION}:{acct}:log-group:{LOG_GROUP}"
    retry(lambda: waf.put_logging_configuration(LoggingConfiguration={
        "ResourceArn": acl_arn, "LogDestinationConfigs": [lg_arn]}), {"WAFUnavailableEntityException"})
    state["log_group"] = LOG_GROUP

    step(7, "Associate the web ACL with the API stage")
    # A new REST stage can take a few minutes before WAF can see it (observed > 2 min).
    retry(lambda: waf.associate_web_acl(WebACLArn=acl_arn, ResourceArn=stage_arn),
          {"WAFUnavailableEntityException"}, tries=36, wait=10)
    state["stage_arn"] = stage_arn
    save(state)
    print(f"\nDone. Endpoint: {state['url']}\nNext: python deploy_lab.py test --profile {profile}")


# --------------------------------------------------------------------------- status
def status(profile):
    s, state = session(profile), load()
    if not state:
        print("No resources.json — nothing deployed.")
        return
    waf, ddb = s.client("wafv2"), s.client("dynamodb")
    print(json.dumps(state, indent=2))
    acl = waf.get_web_acl(Name=ACL, Scope="REGIONAL", Id=state["acl_id"])["WebACL"]
    print("\nWeb ACL rules:", [(r["Priority"], r["Name"]) for r in acl["Rules"]])
    print("Associated:", waf.get_web_acl_for_resource(ResourceArn=state["stage_arn"]).get("WebACL", {}).get("Name"))
    items = ddb.scan(TableName=TABLE, Limit=20).get("Items", [])
    print(f"\nCounter items ({len(items)}):")
    for i in items:
        print(" ", i["pk"]["S"][:16] + "…#" + i["pk"]["S"].split("#")[1], "fails=", i["fails"]["N"])


# --------------------------------------------------------------------------- test
def call(url, user, password, ip, run):
    body = urllib.parse.urlencode({"grant_type": "password", "username": user,
                                   "password": password, "client_id": "lab-client"}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": ip,
        "x-lab-run": run, "User-Agent": "net023-lab/1.0"})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        code, headers = r.status, r.headers
    except urllib.error.HTTPError as e:
        code, headers = e.code, e.headers
    # Behind API Gateway the WAF custom header arrives as-is ("lab-source"), with
    # x-amzn-ErrorType: ForbiddenException even though the status is the custom 429.
    waf_hit = headers.get("lab-source") == "waf" or headers.get("x-amzn-waf-lab-source") == "waf"
    src = "waf" if waf_hit else headers.get("x-lab-source", "?")
    return code, src, headers.get("x-lab-fails", "")


def fake_ip(i):
    return f"203.0.113.{i % 254 + 1}" if i < 254 else f"198.51.100.{i % 254 + 1}"


def test(profile):
    state = load()
    url, run = state["url"], "run-" + uuid.uuid4().hex[:8]
    print(f"Run tag: {run}  (use: python deploy_lab.py logs {run})")

    print("\nPhase A — legitimate login (alice, correct password)")
    print("  ->", call(url, "alice", "Correct-Horse-1", "198.51.100.10", run))

    print(f"\nPhase B — distributed attack: 30 wrong passwords for 'bob' from 30 different IPs")
    for i in range(30):
        code, src, fails = call(url, "bob", f"guess-{i}", fake_ip(i), run)
        print(f"  #{i+1:02d} ip={fake_ip(i):<15} -> {code} from {src} fails={fails}")

    print("\nPhase C — the real bob logs in with the correct password, same minute")
    print("  ->", call(url, "bob", "Battery-Staple-2", "198.51.100.20", run), " (lockout-as-DoS risk)")

    phase_d(url, run)


def phase_d(url, run, ip=None):
    ip = ip or f"198.51.100.{random.randint(100, 250)}"
    print(f"\nPhase D — single-IP flood from {ip}: random usernames until WAF blocks (limit {RATE_LIMIT}/5 min)")
    t0, n = time.time(), 0
    while time.time() - t0 < 240:
        n += 1
        code, src, _ = call(url, f"user{random.randint(1, 10**6)}", "x", ip, run)
        if src == "waf":
            print(f"  WAF blocked after {n} requests, {int(time.time() - t0)} s  -> {code} from waf")
            break
        time.sleep(0.25)
    else:
        print(f"  No WAF block after {n} requests in 240 s (mitigation lag — run again)")


# --------------------------------------------------------------------------- logs
def show_logs(profile, run):
    logs = session(profile).client("logs")
    q = (f'fields action, terminatingRuleId, httpRequest.clientIp | filter @message like "{run}" '
         '| stats count() as n by action, terminatingRuleId')
    qid = logs.start_query(logGroupName=LOG_GROUP, startTime=int(time.time()) - 3600,
                           endTime=int(time.time()), queryString=q)["queryId"]
    while True:
        r = logs.get_query_results(queryId=qid)
        if r["status"] in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(2)
    for row in r["results"]:
        print({c["field"]: c["value"] for c in row})
    q2 = (f'filter @message like "{run}" and @message like "awswaf:managed:aws:atp" '
          '| parse @message /"name":"(?<label>awswaf:managed:aws:atp:[^"]+)"/ | stats count() by label')
    qid = logs.start_query(logGroupName=LOG_GROUP, startTime=int(time.time()) - 3600,
                           endTime=int(time.time()), queryString=q2)["queryId"]
    time.sleep(5)
    rows = logs.get_query_results(queryId=qid)["results"]
    print("\nATP labels:", [{c["field"]: c["value"] for c in row} for row in rows] or "none (ATP off or not yet indexed)")


# --------------------------------------------------------------------------- teardown
def teardown(profile):
    s, state = session(profile), load()
    waf, apigw, lam = s.client("wafv2"), s.client("apigateway"), s.client("lambda")
    iam, ddb, logs = s.client("iam"), s.client("dynamodb"), s.client("logs")

    def quiet(fn, *a, **k):
        try:
            fn(*a, **k)
        except ClientError as e:
            print("   skip:", e.response["Error"]["Code"])

    if state.get("stage_arn"):
        step(1, "Disassociate web ACL"); quiet(waf.disassociate_web_acl, ResourceArn=state["stage_arn"])
    if state.get("acl_arn"):
        step(2, "Delete logging config and web ACL")
        quiet(waf.delete_logging_configuration, ResourceArn=state["acl_arn"])
        time.sleep(5)
        try:
            lt = waf.get_web_acl(Name=ACL, Scope="REGIONAL", Id=state["acl_id"])["LockToken"]
            retry(lambda: waf.delete_web_acl(Name=ACL, Scope="REGIONAL", Id=state["acl_id"], LockToken=lt),
                  {"WAFAssociatedItemException"})
        except ClientError as e:
            print("   skip:", e.response["Error"]["Code"])
    if state.get("api_id"):
        step(3, "Delete REST API"); quiet(apigw.delete_rest_api, restApiId=state["api_id"])
    step(4, "Delete Lambda"); quiet(lam.delete_function, FunctionName=FUNCTION)
    step(5, "Delete IAM role")
    quiet(iam.delete_role_policy, RoleName=ROLE, PolicyName="counter-table")
    quiet(iam.detach_role_policy, RoleName=ROLE,
          PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
    quiet(iam.delete_role, RoleName=ROLE)
    step(6, "Delete DynamoDB table"); quiet(ddb.delete_table, TableName=TABLE)
    step(7, "Delete log groups")
    quiet(logs.delete_log_group, logGroupName=LOG_GROUP)
    quiet(logs.delete_log_group, logGroupName=f"/aws/lambda/{FUNCTION}")

    step(8, "Verify (every resource type, plus a tag search)")
    left = [n["Name"] for n in waf.list_web_acls(Scope="REGIONAL")["WebACLs"] if n["Name"] == ACL]
    left += [a["name"] for a in apigw.get_rest_apis()["items"] if a["name"] == API]
    for label, probe in (("lambda", lambda: lam.get_function(FunctionName=FUNCTION)),
                         ("role", lambda: iam.get_role(RoleName=ROLE)),
                         ("table", lambda: ddb.describe_table(TableName=TABLE))):
        try:
            probe()
            left.append(label)          # a table in DELETING state can show up for a few seconds
        except ClientError:
            pass
    left += [g["logGroupName"] for pre in (LOG_GROUP, f"/aws/lambda/{FUNCTION}")
             for g in logs.describe_log_groups(logGroupNamePrefix=pre)["logGroups"]]
    tagged = s.client("resourcegroupstaggingapi").get_resources(
        TagFilters=[{"Key": "Project", "Values": [PREFIX]}])["ResourceTagMappingList"]
    left += [t["ResourceARN"].split(":")[2] + " (tagged)" for t in tagged]
    print("   leftovers:", left or "none")
    if RESOURCE_FILE.exists() and not left:
        RESOURCE_FILE.unlink()


# --------------------------------------------------------------------------- main
if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["deploy", "status", "test", "logs", "teardown"])
    p.add_argument("run", nargs="?", help="run tag for 'logs'")
    p.add_argument("--profile", required=True)
    p.add_argument("--atp", action="store_true", help="add ATP in Count (extra cost)")
    a = p.parse_args()
    {"deploy": lambda: deploy(a.profile, a.atp), "status": lambda: status(a.profile),
     "test": lambda: test(a.profile), "logs": lambda: show_logs(a.profile, a.run),
     "teardown": lambda: teardown(a.profile)}[a.command]()
