#!/usr/bin/env python3
"""
Drive AWS Network Firewall *container associations* (container attributes
referencing) end-to-end.

Why a script instead of the AWS CLI: aws-cli v2 (2.34.49) does NOT yet ship the
`network-firewall ... container-association` commands, but boto3/botocore
(1.43.50) already has the operations. So we call the API directly.

Usage:
  nfw_container.py create  --cluster-arn ARN [--namespace NS] [--label k=v ...]
  nfw_container.py describe
  nfw_container.py wait-active
  nfw_container.py rulegroup           # create STATEFUL rule group referencing the association
  nfw_container.py delete-rulegroup
  nfw_container.py delete               # delete the container association
"""
import argparse, json, sys, time
import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ASSOC_NAME = "eks-payments-monitor"
RG_NAME = "container-ref-rules"

nfw = boto3.client("network-firewall", region_name=REGION)


def _p(obj):
    print(json.dumps(obj, default=str, indent=2))


def create(args):
    cfg = {"ClusterArn": args.cluster_arn}
    filters = []
    if args.namespace:
        # EKS namespace filter
        filters.append({"Key": "namespace", "Value": args.namespace})
    for kv in args.label or []:
        k, v = kv.split("=", 1)
        filters.append({"Key": k, "Value": v})
    if filters:
        cfg["AttributeFilters"] = filters
    print(f"Creating container association '{ASSOC_NAME}' (Type=EKS)")
    print("Monitoring config:")
    _p(cfg)
    try:
        resp = nfw.create_container_association(
            ContainerAssociationName=ASSOC_NAME,
            Type="EKS",
            Description="EKS pod-IP tracking for NFW container attributes lab",
            ContainerMonitoringConfigurations=[cfg],
        )
    except ClientError as e:
        print("ClientError:", e.response["Error"]["Code"], "-", e.response["Error"]["Message"])
        sys.exit(1)
    _p(resp)


def _describe():
    return nfw.describe_container_association(ContainerAssociationName=ASSOC_NAME)


def describe(args):
    _p(_describe())


def wait_active(args):
    for i in range(60):
        d = _describe()
        st = d.get("Status")
        cidr = d.get("ResolvedCidrCount")
        print(f"[{i:02d}] Status={st}  ResolvedCidrCount={cidr}")
        if st == "ACTIVE" and cidr and cidr > 0:
            print("ACTIVE with resolved pod IPs.")
            _p(d)
            return
        time.sleep(15)
    print("Timed out waiting for ACTIVE with resolved IPs.")


def rulegroup(args):
    d = _describe()
    arn = d["ContainerAssociationArn"]
    print(f"Creating STATEFUL rule group '{RG_NAME}' referencing:\n  {arn}")
    # IP set *references* are referenced with '@' (not '$', which is a plain
    # RuleVariables IPSet). The '@NAME' must match the IPSetReferences key.
    rule = (
        'alert tcp @CONTAINER_IPS any -> any any '
        '(msg:"traffic from tracked EKS pods"; sid:1000001; rev:1;)\n'
    )
    resp = nfw.create_rule_group(
        RuleGroupName=RG_NAME,
        Type="STATEFUL",
        Capacity=100,
        RuleGroup={
            "ReferenceSets": {
                "IPSetReferences": {
                    "CONTAINER_IPS": {"ReferenceArn": arn}
                }
            },
            "RulesSource": {"RulesString": rule},
        },
    )
    _p(resp)


def delete_rulegroup(args):
    _p(nfw.delete_rule_group(RuleGroupName=RG_NAME, Type="STATEFUL"))


def delete(args):
    _p(nfw.delete_container_association(ContainerAssociationName=ASSOC_NAME))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--cluster-arn", required=True)
    c.add_argument("--namespace"); c.add_argument("--label", action="append")
    sub.add_parser("describe")
    sub.add_parser("wait-active")
    sub.add_parser("rulegroup")
    sub.add_parser("delete-rulegroup")
    sub.add_parser("delete")
    args = ap.parse_args()
    {
        "create": create, "describe": describe, "wait-active": wait_active,
        "rulegroup": rulegroup, "delete-rulegroup": delete_rulegroup, "delete": delete,
    }[args.cmd](args)
