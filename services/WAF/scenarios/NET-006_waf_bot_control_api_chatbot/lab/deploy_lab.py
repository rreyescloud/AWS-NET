"""
NET-006 Lab: WAF Bot Control for Bedrock Chatbot
Region: us-east-1 (required for CloudFront Global WAF)
Profile: lab

Architecture:
  Frontend (S3+CloudFront) → Global WAF → CloudFront → API Gateway → Lambda → Bedrock Nova Lite

Usage:
  python deploy_lab.py deploy     # Create all infrastructure
  python deploy_lab.py status     # Show current state
  python deploy_lab.py test       # Run tests against the chatbot
  python deploy_lab.py teardown   # Delete everything
"""

import sys
import time
import json
import hashlib
import boto3
from botocore.exceptions import ClientError

PROFILE = "lab"
REGION = "us-east-1"
PREFIX = "net006-waf-chatbot"
MODEL_ID = "us.amazon.nova-lite-v1:0"
RESOURCES_FILE = "resources.json"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ec2 = session.client("ec2")
lam = session.client("lambda")
apigw = session.client("apigateway")
s3 = session.client("s3")
cf = session.client("cloudfront")
wafv2 = session.client("wafv2")
iam = session.client("iam")
logs = session.client("logs")

ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]


def save_resources(r):
    with open(RESOURCES_FILE, "w") as f:
        json.dump(r, f, indent=2)


def load_resources():
    try:
        with open(RESOURCES_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# Lambda function code
LAMBDA_CODE = '''
import json
import boto3

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL_ID = "us.amazon.nova-lite-v1:0"

def handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
        message = body.get("message", "Hello")

        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": message}]}],
            inferenceConfig={"maxTokens": 300}
        )

        answer = response["output"]["message"]["content"][0]["text"]
        tokens = response["usage"]

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "POST,OPTIONS"
            },
            "body": json.dumps({"response": answer, "tokens": tokens})
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e)})
        }
'''

# Frontend HTML
FRONTEND_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NET-006 WAF Chatbot Lab</title>
    <!-- WAF JS SDK will be injected here -->
    <script type="text/javascript" src="{WAF_SDK_URL}" defer></script>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; }}
        #chat {{ border: 1px solid #ddd; border-radius: 8px; padding: 20px; min-height: 300px; margin-bottom: 20px; overflow-y: auto; max-height: 500px; }}
        .msg {{ margin: 10px 0; padding: 10px; border-radius: 6px; }}
        .user {{ background: #e3f2fd; text-align: right; }}
        .bot {{ background: #f5f5f5; }}
        .error {{ background: #ffebee; color: #c62828; }}
        #input-area {{ display: flex; gap: 10px; }}
        input {{ flex: 1; padding: 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; }}
        button {{ padding: 12px 24px; background: #1976d2; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }}
        button:hover {{ background: #1565c0; }}
        .meta {{ font-size: 11px; color: #888; margin-top: 4px; }}
        h1 {{ color: #333; }}
        .toggle {{ margin: 10px 0; padding: 10px; background: #fff3e0; border-radius: 6px; font-size: 13px; }}
        .toggle label {{ cursor: pointer; }}
    </style>
</head>
<body>
    <h1>NET-006: WAF-Protected Chatbot</h1>
    <div class="toggle">
        <label><input type="checkbox" id="useWafSdk" checked> Use WAF JS SDK (AwsWafIntegration.fetch)</label>
        <span class="meta">Uncheck to use plain fetch() — should trigger Challenge errors when Bot Control is in Block mode</span>
    </div>
    <div id="chat"></div>
    <div id="input-area">
        <input type="text" id="msg" placeholder="Ask me anything..." onkeypress="if(event.key==='Enter')send()">
        <button onclick="send()">Send</button>
    </div>
    <script>
        const API_URL = "{API_URL}";
        const chat = document.getElementById("chat");

        function addMsg(text, cls) {{
            const div = document.createElement("div");
            div.className = "msg " + cls;
            div.innerHTML = text;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }}

        async function send() {{
            const input = document.getElementById("msg");
            const msg = input.value.trim();
            if (!msg) return;
            input.value = "";
            addMsg(msg, "user");

            const useSDK = document.getElementById("useWafSdk").checked;
            const fetchFn = useSDK && window.AwsWafIntegration ? AwsWafIntegration.fetch : fetch;
            const label = useSDK && window.AwsWafIntegration ? "SDK" : "plain fetch";

            try {{
                const resp = await fetchFn(API_URL, {{
                    method: "POST",
                    headers: {{"Content-Type": "application/json"}},
                    body: JSON.stringify({{message: msg}})
                }});

                if (!resp.ok) {{
                    const text = await resp.text();
                    addMsg(`Error ${{resp.status}} (${{label}}): <pre>${{text.substring(0,200)}}</pre>`, "error");
                    return;
                }}

                const data = await resp.json();
                addMsg(`${{data.response}}<div class="meta">Tokens: ${{data.tokens.totalTokens}} | via: ${{label}}</div>`, "bot");
            }} catch (e) {{
                addMsg(`Exception (${{label}}): ${{e.message}}`, "error");
            }}
        }}
    </script>
</body>
</html>
'''


def deploy():
    r = {}
    print("=" * 60)
    print("NET-006 LAB DEPLOYMENT — WAF + Bedrock Chatbot")
    print("=" * 60)

    # Step 1: IAM Role for Lambda
    print("\n[1/8] Creating IAM Role for Lambda...")
    trust_policy = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    try:
        role = iam.create_role(RoleName=f"{PREFIX}-lambda-role", AssumeRolePolicyDocument=trust_policy)
        r["lambda_role_arn"] = role["Role"]["Arn"]
        iam.attach_role_policy(RoleName=f"{PREFIX}-lambda-role", PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        iam.put_role_policy(RoleName=f"{PREFIX}-lambda-role", PolicyName="bedrock-invoke",
                           PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:Converse"], "Resource": "*"}]}))
        time.sleep(10)
    except ClientError as e:
        if "EntityAlreadyExists" in str(e):
            r["lambda_role_arn"] = f"arn:aws:iam::{ACCOUNT_ID}:role/{PREFIX}-lambda-role"
        else:
            raise
    print(f"  Role: {r['lambda_role_arn']}")

    # Step 2: Lambda Function
    print("\n[2/8] Creating Lambda Function...")
    import zipfile, io
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("lambda_function.py", LAMBDA_CODE)
    zip_buffer.seek(0)

    try:
        fn = lam.create_function(
            FunctionName=f"{PREFIX}-chatbot",
            Runtime="python3.12",
            Role=r["lambda_role_arn"],
            Handler="lambda_function.handler",
            Code={"ZipFile": zip_buffer.read()},
            Timeout=30,
            MemorySize=256
        )
        r["lambda_arn"] = fn["FunctionArn"]
    except ClientError as e:
        if "ResourceConflictException" in str(e):
            r["lambda_arn"] = f"arn:aws:lambda:us-east-1:{ACCOUNT_ID}:function:{PREFIX}-chatbot"
        else:
            raise
    print(f"  Lambda: {r['lambda_arn']}")

    # Step 3: API Gateway
    print("\n[3/8] Creating API Gateway...")
    api = apigw.create_rest_api(name=f"{PREFIX}-api", endpointConfiguration={"types": ["REGIONAL"]})
    r["api_id"] = api["id"]

    root_id = apigw.get_resources(restApiId=r["api_id"])["items"][0]["id"]
    chat_resource = apigw.create_resource(restApiId=r["api_id"], parentId=root_id, pathPart="chat")
    chat_id = chat_resource["id"]

    # POST method
    apigw.put_method(restApiId=r["api_id"], resourceId=chat_id, httpMethod="POST", authorizationType="NONE")
    apigw.put_integration(restApiId=r["api_id"], resourceId=chat_id, httpMethod="POST",
                          type="AWS_PROXY", integrationHttpMethod="POST",
                          uri=f"arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/{r['lambda_arn']}/invocations")

    # OPTIONS for CORS
    apigw.put_method(restApiId=r["api_id"], resourceId=chat_id, httpMethod="OPTIONS", authorizationType="NONE")
    apigw.put_integration(restApiId=r["api_id"], resourceId=chat_id, httpMethod="OPTIONS",
                          type="MOCK", requestTemplates={"application/json": '{"statusCode": 200}'})
    apigw.put_method_response(restApiId=r["api_id"], resourceId=chat_id, httpMethod="OPTIONS", statusCode="200",
                              responseParameters={"method.response.header.Access-Control-Allow-Headers": False,
                                                  "method.response.header.Access-Control-Allow-Methods": False,
                                                  "method.response.header.Access-Control-Allow-Origin": False})
    apigw.put_integration_response(restApiId=r["api_id"], resourceId=chat_id, httpMethod="OPTIONS", statusCode="200",
                                   responseParameters={"method.response.header.Access-Control-Allow-Headers": "'Content-Type'",
                                                       "method.response.header.Access-Control-Allow-Methods": "'POST,OPTIONS'",
                                                       "method.response.header.Access-Control-Allow-Origin": "'*'"})

    # Deploy
    apigw.create_deployment(restApiId=r["api_id"], stageName="prod")
    r["api_url"] = f"https://{r['api_id']}.execute-api.us-east-1.amazonaws.com/prod/chat"

    # Lambda permission for API GW
    try:
        lam.add_permission(FunctionName=f"{PREFIX}-chatbot", StatementId="apigw-invoke",
                           Action="lambda:InvokeFunction", Principal="apigateway.amazonaws.com",
                           SourceArn=f"arn:aws:execute-api:us-east-1:{ACCOUNT_ID}:{r['api_id']}/*")
    except ClientError:
        pass

    print(f"  API: {r['api_url']}")

    # Step 4: S3 Bucket for Frontend
    print("\n[4/8] Creating S3 Bucket for Frontend...")
    bucket_name = f"{PREFIX}-frontend-{ACCOUNT_ID}"
    try:
        s3.create_bucket(Bucket=bucket_name)
    except ClientError as e:
        if "BucketAlreadyOwnedByYou" not in str(e):
            raise
    r["bucket_name"] = bucket_name

    # Upload HTML (placeholder — will update with CloudFront URL + WAF SDK URL later)
    html = FRONTEND_HTML.replace("{API_URL}", r["api_url"]).replace("{WAF_SDK_URL}", "")
    s3.put_object(Bucket=bucket_name, Key="index.html", Body=html, ContentType="text/html")
    print(f"  Bucket: {bucket_name}")

    # Step 5: CloudFront Distribution
    print("\n[5/8] Creating CloudFront Distribution (~5 min)...")
    oai = cf.create_cloud_front_origin_access_identity(
        CloudFrontOriginAccessIdentityConfig={"CallerReference": f"{PREFIX}-oai", "Comment": f"{PREFIX} OAI"}
    )
    r["oai_id"] = oai["CloudFrontOriginAccessIdentity"]["Id"]

    # S3 bucket policy for OAI
    bucket_policy = json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::cloudfront:user/CloudFront Origin Access Identity {r['oai_id']}"}, "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket_name}/*"}]})
    s3.put_bucket_policy(Bucket=bucket_name, Policy=bucket_policy)

    dist_config = {
        "CallerReference": f"{PREFIX}-dist-{int(time.time())}",
        "Comment": f"{PREFIX} chatbot distribution",
        "DefaultCacheBehavior": {
            "TargetOriginId": "s3-frontend",
            "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}},
            "MinTTL": 0, "DefaultTTL": 86400, "MaxTTL": 31536000,
        },
        "Origins": {"Quantity": 1, "Items": [{
            "Id": "s3-frontend",
            "DomainName": f"{bucket_name}.s3.amazonaws.com",
            "S3OriginConfig": {"OriginAccessIdentity": f"origin-access-identity/cloudfront/{r['oai_id']}"}
        }]},
        "Enabled": True,
        "DefaultRootObject": "index.html",
    }
    dist = cf.create_distribution(DistributionConfig=dist_config)
    r["cf_distribution_id"] = dist["Distribution"]["Id"]
    r["cf_domain"] = dist["Distribution"]["DomainName"]
    print(f"  CloudFront: {r['cf_domain']} (ID: {r['cf_distribution_id']})")
    print("  Waiting for distribution to deploy...")

    # Step 6: Global WAF (CloudFront scope)
    print("\n[6/8] Creating Global WAF Web ACL...")
    waf_global = session.client("wafv2", region_name="us-east-1")

    webacl = waf_global.create_web_acl(
        Name=f"{PREFIX}-global-waf",
        Scope="CLOUDFRONT",
        DefaultAction={"Allow": {}},
        VisibilityConfig={"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": f"{PREFIX}-global"},
        Rules=[
            # Rule 1: IP Reputation
            {"Name": "AWSIPReputation", "Priority": 1,
             "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesAmazonIpReputationList"}},
             "OverrideAction": {"None": {}},
             "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "IPReputation"}},
            # Rule 2: Common Rule Set
            {"Name": "AWSCommonRules", "Priority": 2,
             "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesCommonRuleSet"}},
             "OverrideAction": {"None": {}},
             "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "CommonRules"}},
            # Rule 3: Known Bad Inputs
            {"Name": "AWSBadInputs", "Priority": 3,
             "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesKnownBadInputsRuleSet"}},
             "OverrideAction": {"None": {}},
             "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "BadInputs"}},
            # Rule 4: Rate Limit 200/IP
            {"Name": "RateLimit200", "Priority": 4,
             "Statement": {"RateBasedStatement": {"Limit": 200, "AggregateKeyType": "IP"}},
             "Action": {"Block": {}},
             "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "RateLimit"}},
            # Rule 5: Bot Control (COUNT mode — labels only)
            {"Name": "BotControl", "Priority": 5,
             "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": "AWSManagedRulesBotControlRuleSet",
                           "ManagedRuleGroupConfigs": [{"AWSManagedRulesBotControlRuleSet": {"InspectionLevel": "COMMON"}}]}},
             "OverrideAction": {"Count": {}},
             "VisibilityConfig": {"SampledRequestsEnabled": True, "CloudWatchMetricsEnabled": True, "MetricName": "BotControl"}},
        ]
    )
    r["webacl_arn"] = webacl["Summary"]["ARN"]
    r["webacl_id"] = webacl["Summary"]["Id"]
    print(f"  Web ACL: {r['webacl_arn']}")

    # Associate WAF with CloudFront (need to wait for distribution)
    print("  Note: Associate WAF with CloudFront after distribution deploys.")
    print(f"  Run: aws wafv2 associate-web-acl --web-acl-arn {r['webacl_arn']} --resource-arn arn:aws:cloudfront::{ACCOUNT_ID}:distribution/{r['cf_distribution_id']}")

    # Step 7: Enable WAF Logging
    print("\n[7/8] Setting up WAF Logging...")
    log_group = f"aws-waf-logs-{PREFIX}"
    try:
        logs.create_log_group(logGroupName=log_group)
    except ClientError:
        pass
    r["waf_log_group"] = log_group
    print(f"  Log group: {log_group}")

    # Step 8: Summary
    print("\n[8/8] Saving resources...")
    save_resources(r)

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"\n  API Gateway: {r['api_url']}")
    print(f"  CloudFront:  https://{r['cf_domain']}")
    print(f"  WAF Web ACL: {r['webacl_id']}")
    print(f"  S3 Frontend: {bucket_name}")
    print("\n  NEXT STEPS:")
    print("  1. Wait ~5 min for CloudFront distribution to deploy")
    print("  2. Associate WAF with CloudFront distribution")
    print("  3. Update frontend HTML with WAF SDK URL")
    print("  4. Test: https://{cf_domain}")


def status():
    r = load_resources()
    if not r:
        print("No resources. Run deploy first.")
        return
    print(json.dumps(r, indent=2))

    # Check CF distribution status
    if r.get("cf_distribution_id"):
        dist = cf.get_distribution(Id=r["cf_distribution_id"])
        print(f"\nCloudFront Status: {dist['Distribution']['Status']}")
        print(f"CloudFront Domain: {dist['Distribution']['DomainName']}")


def test():
    r = load_resources()
    if not r:
        print("No resources. Run deploy first.")
        return

    import urllib.request
    print("=" * 60)
    print("NET-006 TESTS")
    print("=" * 60)

    # Test 1: Direct API call
    print("\n[Test 1] Direct API Gateway call (no WAF)...")
    try:
        req = urllib.request.Request(r["api_url"], data=json.dumps({"message": "What is WAF?"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        print(f"  Status: {resp.status}")
        print(f"  Response: {data['response'][:100]}...")
        print(f"  Tokens: {data['tokens']}")
    except Exception as e:
        print(f"  Error: {e}")

    # Test 2: Via CloudFront (with WAF)
    print(f"\n[Test 2] Via CloudFront (WAF protected)...")
    cf_api_url = f"https://{r['cf_domain']}/chat"
    print(f"  Note: CloudFront frontend is at https://{r['cf_domain']}")
    print(f"  Test the chatbot in your browser to see WAF SDK in action.")


def teardown():
    r = load_resources()
    if not r:
        print("No resources.")
        return

    print("=" * 60)
    print("NET-006 TEARDOWN")
    print("=" * 60)

    # WAF
    if r.get("webacl_id"):
        print("  Deleting WAF Web ACL...")
        try:
            lock = wafv2.get_web_acl(Name=f"{PREFIX}-global-waf", Scope="CLOUDFRONT", Id=r["webacl_id"])
            wafv2.delete_web_acl(Name=f"{PREFIX}-global-waf", Scope="CLOUDFRONT", Id=r["webacl_id"], LockToken=lock["LockToken"])
        except Exception as e:
            print(f"    {e}")

    # CloudFront
    if r.get("cf_distribution_id"):
        print("  Disabling CloudFront distribution...")
        try:
            dist = cf.get_distribution(Id=r["cf_distribution_id"])
            etag = dist["ETag"]
            config = dist["Distribution"]["DistributionConfig"]
            config["Enabled"] = False
            cf.update_distribution(Id=r["cf_distribution_id"], DistributionConfig=config, IfMatch=etag)
            print("    Waiting for disable (~5 min)...")
            time.sleep(60)
            dist = cf.get_distribution(Id=r["cf_distribution_id"])
            cf.delete_distribution(Id=r["cf_distribution_id"], IfMatch=dist["ETag"])
        except Exception as e:
            print(f"    {e}")

    # OAI
    if r.get("oai_id"):
        try:
            oai = cf.get_cloud_front_origin_access_identity(Id=r["oai_id"])
            cf.delete_cloud_front_origin_access_identity(Id=r["oai_id"], IfMatch=oai["ETag"])
        except Exception as e:
            print(f"    OAI: {e}")

    # S3
    if r.get("bucket_name"):
        print("  Deleting S3 bucket...")
        try:
            objects = s3.list_objects_v2(Bucket=r["bucket_name"])
            for obj in objects.get("Contents", []):
                s3.delete_object(Bucket=r["bucket_name"], Key=obj["Key"])
            s3.delete_bucket_policy(Bucket=r["bucket_name"])
            s3.delete_bucket(Bucket=r["bucket_name"])
        except Exception as e:
            print(f"    {e}")

    # API Gateway
    if r.get("api_id"):
        print("  Deleting API Gateway...")
        try:
            apigw.delete_rest_api(restApiId=r["api_id"])
        except Exception as e:
            print(f"    {e}")

    # Lambda
    print("  Deleting Lambda...")
    try:
        lam.delete_function(FunctionName=f"{PREFIX}-chatbot")
    except Exception as e:
        print(f"    {e}")

    # IAM
    print("  Deleting IAM Role...")
    try:
        iam.delete_role_policy(RoleName=f"{PREFIX}-lambda-role", PolicyName="bedrock-invoke")
        iam.detach_role_policy(RoleName=f"{PREFIX}-lambda-role", PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        iam.delete_role(RoleName=f"{PREFIX}-lambda-role")
    except Exception as e:
        print(f"    {e}")

    # Logs
    if r.get("waf_log_group"):
        try:
            logs.delete_log_group(logGroupName=r["waf_log_group"])
        except Exception as e:
            print(f"    {e}")

    print("\nTEARDOWN COMPLETE")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python deploy_lab.py [deploy|status|test|teardown]")
        sys.exit(1)

    action = sys.argv[1].lower()
    {"deploy": deploy, "status": status, "test": test, "teardown": teardown}[action]()
