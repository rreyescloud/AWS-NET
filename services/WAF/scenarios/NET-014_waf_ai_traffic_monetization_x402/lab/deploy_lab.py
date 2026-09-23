#!/usr/bin/env python3
"""
NET-010 — WAF AI Traffic Monetization (x402) lab: infrastructure helper.

Automates the CLI-reproducible parts of the lab: create a Web ACL with Bot
Control (Targeted + ML), associate it to an existing CloudFront distribution,
and enable logging. The `Monetize` action + MonetizationConfig itself is NOT
included here — that part must be set in the AWS WAF console, because this
environment's wafv2 API does not yet expose the monetization action (that is
the whole point of the scenario).

Usage:
    python deploy_lab.py --distribution-id <ID> [--region us-east-1]

Requires: boto3, credentials for the target account.
"""
import argparse
import json

import boto3

BOT_CONTROL_RULE = {
    "Name": "AWS-BotControl",
    "Priority": 0,
    "Statement": {
        "ManagedRuleGroupStatement": {
            "VendorName": "AWS",
            "Name": "AWSManagedRulesBotControlRuleSet",
            "ManagedRuleGroupConfigs": [
                {"AWSManagedRulesBotControlRuleSet": {
                    "InspectionLevel": "TARGETED", "EnableMachineLearning": True}}
            ],
        }
    },
    "OverrideAction": {"None": {}},
    "VisibilityConfig": {"SampledRequestsEnabled": True,
                         "CloudWatchMetricsEnabled": True,
                         "MetricName": "AWS-BotControl"},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--distribution-id", required=True)
    ap.add_argument("--region", default="us-east-1")  # CLOUDFRONT scope lives in us-east-1
    ap.add_argument("--name", default="waf-monetization-lab")
    args = ap.parse_args()

    wafv2 = boto3.client("wafv2", region_name=args.region)
    logs = boto3.client("logs", region_name=args.region)
    cf = boto3.client("cloudfront")

    # 1) Create the Web ACL with Bot Control Targeted.
    acl = wafv2.create_web_acl(
        Name=args.name, Scope="CLOUDFRONT", DefaultAction={"Allow": {}},
        Description="NET-010: Bot Control Targeted for AI traffic monetization lab",
        Rules=[BOT_CONTROL_RULE],
        VisibilityConfig={"SampledRequestsEnabled": True,
                          "CloudWatchMetricsEnabled": True, "MetricName": args.name},
    )
    acl_arn = acl["Summary"]["ARN"]
    print("Web ACL:", acl_arn)

    # 2) Associate to the CloudFront distribution.
    dist = cf.get_distribution_config(Id=args.distribution_id)
    cfg, etag = dist["DistributionConfig"], dist["ETag"]
    cfg["WebACLId"] = acl_arn
    cf.update_distribution(Id=args.distribution_id, DistributionConfig=cfg, IfMatch=etag)
    print("Associated to distribution:", args.distribution_id)

    # 3) Enable logging to CloudWatch (log group name MUST start with aws-waf-logs-).
    lg = f"aws-waf-logs-{args.name}"
    try:
        logs.create_log_group(logGroupName=lg)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    lg_arn = f"arn:aws:logs:{args.region}:{boto3.client('sts').get_caller_identity()['Account']}:log-group:{lg}"
    wafv2.put_logging_configuration(LoggingConfiguration={
        "ResourceArn": acl_arn, "LogDestinationConfigs": [lg_arn]})
    print("Logging ->", lg)

    print("\nNext (console-only): open the Web ACL in the AWS WAF console,")
    print("  - Configure monetization: Test mode, Base Sepolia, base price 0.001 USDC,")
    print("    payTo = your recipient testnet wallet address.")
    print("  - Add a rule matching label 'awswaf:managed:aws:bot-control:bot:category:ai'")
    print("    with the native Monetize action.")
    print("Then run x402_pay_client.py from a separate PAYER wallet.")


if __name__ == "__main__":
    main()
