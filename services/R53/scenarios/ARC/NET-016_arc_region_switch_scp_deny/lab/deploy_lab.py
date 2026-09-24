#!/usr/bin/env python3
"""NET-016 — ARC Region Switch blocked by network-perimeter SCP.

Subcommands:
    deploy      Create all infrastructure (API GWs, ARC cluster, routing controls,
                health checks, Route 53 records, execution role, Region Switch plan,
                and the permission boundary that simulates the SCP)
    status      Show resource state
    test-deny   Execute the Region Switch plan and observe the deny
    fix         Add the ArnNotLike exemption to the permission boundary
    test-allow  Re-execute and observe success
    teardown    Delete everything

Usage:
    python deploy_lab.py deploy      [--profile PROFILE]
    python deploy_lab.py status      [--profile PROFILE]
    python deploy_lab.py test-deny   [--profile PROFILE]
    python deploy_lab.py fix         [--profile PROFILE]
    python deploy_lab.py test-allow  [--profile PROFILE]
    python deploy_lab.py teardown    [--profile PROFILE]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

RESOURCE_FILE = Path(__file__).parent / "resources.json"
SCP_FILE = Path(__file__).parent / "scp_policy.json"

REGION_A = "us-east-1"
REGION_B = "us-west-2"

IMDS_OPTS = {"HttpTokens": "required", "HttpEndpoint": "enabled", "HttpPutResponseHopLimit": 2}


def get_session(profile, region):
    return boto3.Session(profile_name=profile, region_name=region)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def wait_for(desc, check_fn, timeout=600, interval=15):
    elapsed = 0
    while elapsed < timeout:
        result = check_fn()
        if result:
            return result
        print(f"  waiting for {desc}... ({elapsed}s)")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Timed out waiting for {desc} after {timeout}s")


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

def deploy(profile):
    r = load()
    if r:
        print("Resources file exists. Run teardown first or delete resources.json.")
        sys.exit(1)
    r = {}

    sess_a = get_session(profile, REGION_A)
    sess_b = get_session(profile, REGION_B)
    sts = sess_a.client("sts")
    iam = sess_a.client("iam")
    apigw_a = sess_a.client("apigateway")
    apigw_b = sess_b.client("apigateway")
    r53 = sess_a.client("route53")
    r53rc_config = sess_a.client("route53-recovery-control-config")
    r53rc_data = sess_b.client("route53-recovery-cluster",
                                endpoint_url="https://route53-recovery-cluster.us-west-2.amazonaws.com")

    account_id = sts.get_caller_identity()["Account"]
    r["account_id"] = account_id
    print(f"Account: {account_id}")

    # ── 1. Mock API Gateways ───────────────────────────────────────────────
    print("Creating mock API Gateways...")
    for label, client, region in [("east", apigw_a, REGION_A), ("west", apigw_b, REGION_B)]:
        api = client.create_rest_api(
            name=f"NET-016-fraud-verify-{label}",
            description=f"Mock identity verification endpoint ({region})",
            endpointConfiguration={"types": ["REGIONAL"]},
            tags={"lab": "NET-016"},
        )
        root_id = client.get_resources(restApiId=api["id"])["items"][0]["id"]

        # Create /verify resource
        verify = client.create_resource(restApiId=api["id"], parentId=root_id, pathPart="verify")

        # GET /verify → mock integration
        client.put_method(restApiId=api["id"], resourceId=verify["id"],
                         httpMethod="GET", authorizationType="NONE")
        client.put_integration(
            restApiId=api["id"], resourceId=verify["id"], httpMethod="GET",
            type="MOCK", requestTemplates={"application/json": '{"statusCode": 200}'},
        )
        client.put_method_response(
            restApiId=api["id"], resourceId=verify["id"], httpMethod="GET",
            statusCode="200", responseModels={"application/json": "Empty"},
        )
        client.put_integration_response(
            restApiId=api["id"], resourceId=verify["id"], httpMethod="GET",
            statusCode="200",
            responseTemplates={"application/json": json.dumps(
                {"status": "ok", "region": region, "service": "identity-verify-mock"})},
        )

        # Deploy to stage
        client.create_deployment(restApiId=api["id"], stageName="live")
        url = f"https://{api['id']}.execute-api.{region}.amazonaws.com/live/verify"
        r[f"apigw_{label}"] = {"id": api["id"], "url": url, "region": region}
        print(f"  {label}: {url}")

    save(r)

    # ── 2. ARC Cluster ─────────────────────────────────────────────────────
    print("Creating ARC cluster (this takes ~5 minutes)...")
    cluster = r53rc_config.create_cluster(
        ClusterName="NET-016-lab-cluster",
        Tags={"lab": "NET-016"},
    )["Cluster"]
    r["cluster_arn"] = cluster["ClusterArn"]
    save(r)

    # Wait for cluster to be DEPLOYED
    def cluster_ready():
        c = r53rc_config.describe_cluster(ClusterArn=cluster["ClusterArn"])["Cluster"]
        return c["Status"] == "DEPLOYED"
    wait_for("cluster DEPLOYED", cluster_ready, timeout=600)
    print("  Cluster DEPLOYED")

    # Get cluster endpoints
    cluster_info = r53rc_config.describe_cluster(ClusterArn=cluster["ClusterArn"])["Cluster"]
    r["cluster_endpoints"] = cluster_info["ClusterEndpoints"]
    save(r)

    # ── 3. Control Panel + Routing Controls ────────────────────────────────
    print("Creating control panel and routing controls...")
    panel = r53rc_config.create_control_panel(
        ClusterArn=cluster["ClusterArn"],
        ControlPanelName="NET-016-lab-panel",
        Tags={"lab": "NET-016"},
    )["ControlPanel"]
    r["panel_arn"] = panel["ControlPanelArn"]

    rc_primary = r53rc_config.create_routing_control(
        ClusterArn=cluster["ClusterArn"],
        ControlPanelArn=panel["ControlPanelArn"],
        RoutingControlName="NET-016-primary",
    )["RoutingControl"]

    rc_secondary = r53rc_config.create_routing_control(
        ClusterArn=cluster["ClusterArn"],
        ControlPanelArn=panel["ControlPanelArn"],
        RoutingControlName="NET-016-secondary",
    )["RoutingControl"]

    r["rc_primary"] = rc_primary["RoutingControlArn"]
    r["rc_secondary"] = rc_secondary["RoutingControlArn"]
    save(r)

    # Set initial state: primary ON, secondary OFF
    # Use one of the cluster endpoints
    endpoint = cluster_info["ClusterEndpoints"][0]["Endpoint"]
    ep_url = endpoint if endpoint.startswith("https://") else f"https://{endpoint}"
    rc_client = sess_a.client("route53-recovery-cluster", endpoint_url=ep_url)
    rc_client.update_routing_control_states(UpdateRoutingControlStateEntries=[
        {"RoutingControlArn": rc_primary["RoutingControlArn"], "RoutingControlState": "On"},
        {"RoutingControlArn": rc_secondary["RoutingControlArn"], "RoutingControlState": "Off"},
    ])
    print("  Routing controls: primary=ON, secondary=OFF")

    # ── 4. Health Checks ───────────────────────────────────────────────────
    print("Creating health checks...")
    hc_primary = r53.create_health_check(
        CallerReference=f"net016-primary-{int(time.time())}",
        HealthCheckConfig={
            "Type": "RECOVERY_CONTROL",
            "RoutingControlArn": rc_primary["RoutingControlArn"],
        },
    )["HealthCheck"]

    hc_secondary = r53.create_health_check(
        CallerReference=f"net016-secondary-{int(time.time())}",
        HealthCheckConfig={
            "Type": "RECOVERY_CONTROL",
            "RoutingControlArn": rc_secondary["RoutingControlArn"],
        },
    )["HealthCheck"]

    r["hc_primary"] = hc_primary["Id"]
    r["hc_secondary"] = hc_secondary["Id"]
    save(r)

    # ── 5. Route 53 Hosted Zone + Failover Records ────────────────────────
    print("Creating hosted zone and failover records...")
    zone = r53.create_hosted_zone(
        Name="lab-fraud.example.com",
        CallerReference=f"net016-zone-{int(time.time())}",
        HostedZoneConfig={"Comment": "NET-016 ARC Region Switch lab", "PrivateZone": False},
    )["HostedZone"]
    zone_id = zone["Id"].split("/")[-1]
    r["zone_id"] = zone_id

    # Failover records pointing to API Gateway invoke URLs
    r53.change_resource_record_sets(HostedZoneId=zone_id, ChangeBatch={"Changes": [
        {"Action": "UPSERT", "ResourceRecordSet": {
            "Name": "api.lab-fraud.example.com",
            "Type": "CNAME",
            "SetIdentifier": "primary",
            "Failover": "PRIMARY",
            "TTL": 60,
            "ResourceRecords": [{"Value": f"{r['apigw_east']['id']}.execute-api.{REGION_A}.amazonaws.com"}],
            "HealthCheckId": hc_primary["Id"],
        }},
        {"Action": "UPSERT", "ResourceRecordSet": {
            "Name": "api.lab-fraud.example.com",
            "Type": "CNAME",
            "SetIdentifier": "secondary",
            "Failover": "SECONDARY",
            "TTL": 60,
            "ResourceRecords": [{"Value": f"{r['apigw_west']['id']}.execute-api.{REGION_B}.amazonaws.com"}],
            "HealthCheckId": hc_secondary["Id"],
        }},
    ]})
    save(r)

    # ── 6. Execution Role ──────────────────────────────────────────────────
    print("Creating execution role...")
    role_name = "NET-016-region-switch-execution"

    # Permission boundary (simulates the SCP deny)
    scp_doc = json.loads(SCP_FILE.read_text())
    boundary_name = "NET-016-network-perimeter-boundary"

    try:
        boundary = iam.create_policy(
            PolicyName=boundary_name,
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [
                    # Allow everything (ceiling)
                    {"Sid": "AllowAll", "Effect": "Allow", "Action": "*", "Resource": "*"},
                    # Deny routing-control unless known network (mirrors the SCP)
                    scp_doc["Statement"][0],
                ]
            }),
            Description="Simulates network-perimeter SCP for NET-016 lab",
            Tags=[{"Key": "lab", "Value": "NET-016"}],
        )
        boundary_arn = boundary["Policy"]["Arn"]
    except ClientError as e:
        if "EntityAlreadyExists" in str(e):
            boundary_arn = f"arn:aws:iam::{account_id}:policy/{boundary_name}"
        else:
            raise

    r["boundary_arn"] = boundary_arn
    r["boundary_name"] = boundary_name

    # Create execution role
    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "arc-region-switch.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
            PermissionsBoundary=boundary_arn,
            Tags=[{"Key": "lab", "Value": "NET-016"}],
        )
    except ClientError as e:
        if "EntityAlreadyExists" not in str(e):
            raise

    # Attach permissions
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="region-switch-permissions",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "RoutingControlOps",
                    "Effect": "Allow",
                    "Action": [
                        "route53-recovery-cluster:GetRoutingControlState",
                        "route53-recovery-cluster:UpdateRoutingControlState",
                        "route53-recovery-cluster:UpdateRoutingControlStates",
                    ],
                    "Resource": [rc_primary["RoutingControlArn"], rc_secondary["RoutingControlArn"]],
                },
                {
                    "Sid": "RoutingControlDiscovery",
                    "Effect": "Allow",
                    "Action": [
                        "route53-recovery-control-config:Describe*",
                        "route53-recovery-control-config:List*",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "ArcOps",
                    "Effect": "Allow",
                    "Action": ["arc-region-switch:*"],
                    "Resource": "*",
                },
                {
                    "Sid": "PreValidation",
                    "Effect": "Allow",
                    "Action": ["iam:SimulatePrincipalPolicy"],
                    "Resource": f"arn:aws:iam::{account_id}:role/{role_name}",
                },
            ],
        }),
    )

    r["role_name"] = role_name
    r["role_arn"] = f"arn:aws:iam::{account_id}:role/{role_name}"
    save(r)
    print(f"  Role: {role_name} (with permission boundary)")

    # ── 7. Region Switch Plan ──────────────────────────────────────────────
    print("Creating Region Switch plan...")
    time.sleep(10)  # IAM propagation

    try:
        plan = sess_a.client("arc-region-switch").create_region_switch_plan(
            RegionSwitchPlanName="NET-016-lab-plan",
            ExecutionRoleArn=r["role_arn"],
            Steps=[
                {
                    "StepName": "flip-routing-controls",
                    "StepType": "ROUTING_CONTROL",
                    "RoutingControlUpdates": [
                        {"RoutingControlArn": rc_primary["RoutingControlArn"],
                         "RoutingControlState": "Off"},
                        {"RoutingControlArn": rc_secondary["RoutingControlArn"],
                         "RoutingControlState": "On"},
                    ],
                },
            ],
            Tags={"lab": "NET-016"},
        )
        r["plan_arn"] = plan["RegionSwitchPlanArn"]
        r["plan_name"] = "NET-016-lab-plan"
        print(f"  Plan: {plan['RegionSwitchPlanArn']}")
    except Exception as e:
        print(f"  ⚠️  Region Switch plan creation: {e}")
        print("  Plan will need to be created manually or API may not be available yet.")
        r["plan_arn"] = "MANUAL"
        r["plan_name"] = "NET-016-lab-plan"

    save(r)

    print(f"""
✅ Deploy complete.

API Gateway East: {r['apigw_east']['url']}
API Gateway West: {r['apigw_west']['url']}
Cluster:          {r['cluster_arn']}
Routing Controls: primary={r['rc_primary']}, secondary={r['rc_secondary']}
Execution Role:   {r['role_arn']} (permission boundary attached)
Plan:             {r.get('plan_arn', 'MANUAL')}

The permission boundary on the execution role simulates the SCP.
Run: python deploy_lab.py test-deny   → observe the deny
Run: python deploy_lab.py fix         → add the exemption
Run: python deploy_lab.py test-allow  → observe success
""")


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def status(profile):
    r = load()
    if not r:
        print("No resources.json. Run deploy first.")
        return

    sess = get_session(profile, REGION_A)
    iam = sess.client("iam")
    r53rc = sess.client("route53-recovery-control-config")

    print("── API Gateways ──")
    print(f"  East: {r['apigw_east']['url']}")
    print(f"  West: {r['apigw_west']['url']}")

    print("\n── ARC Cluster ──")
    try:
        cluster = r53rc.describe_cluster(ClusterArn=r["cluster_arn"])["Cluster"]
        print(f"  Status: {cluster['Status']}")
    except Exception as e:
        print(f"  {e}")

    print("\n── Routing Controls ──")
    for label, arn in [("Primary", r["rc_primary"]), ("Secondary", r["rc_secondary"])]:
        try:
            # Use first cluster endpoint to check state
            ep = r["cluster_endpoints"][0]["Endpoint"]
            rc_client = sess.client("route53-recovery-cluster", endpoint_url=ep if ep.startswith("https://") else f"https://{ep}")
            state = rc_client.get_routing_control_state(RoutingControlArn=arn)
            print(f"  {label}: {state['RoutingControlState']}")
        except Exception as e:
            print(f"  {label}: {e}")

    print("\n── Execution Role ──")
    try:
        role = iam.get_role(RoleName=r["role_name"])["Role"]
        boundary = role.get("PermissionsBoundary", {}).get("PermissionsBoundaryArn", "none")
        print(f"  Role: {role['Arn']}")
        print(f"  Boundary: {boundary}")
    except Exception as e:
        print(f"  {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEST-DENY — Execute plan and observe the SCP deny
# ═══════════════════════════════════════════════════════════════════════════════

def test_deny(profile):
    r = load()
    if not r:
        print("No resources.json. Run deploy first.")
        return

    sess = get_session(profile, REGION_A)
    iam = sess.client("iam")

    print("── Step 1: Verify SimulatePrincipalPolicy passes (it will) ──")
    sim = iam.simulate_principal_policy(
        PolicySourceArn=r["role_arn"],
        ActionNames=["route53-recovery-cluster:UpdateRoutingControlStates"],
        ResourceArns=["*"],
    )
    for result in sim["EvaluationResults"]:
        print(f"  {result['EvalActionName']}: {result['EvalDecision']}")
    print("  → SimulatePrincipalPolicy does NOT evaluate permission boundaries the same way.")
    print("    In a real org, this is where SimulatePrincipalPolicy misses the SCP entirely.")

    print("\n── Step 2: Attempt routing control update via assumed role ──")
    print("  (Simulating what ARC Region Switch does internally)")

    # Assume the execution role
    sts = sess.client("sts")
    try:
        creds = sts.assume_role(
            RoleArn=r["role_arn"],
            RoleSessionName="RegionSwitchExecution-lab-test",
        )["Credentials"]

        # Use the assumed role's credentials
        rc_sess = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=REGION_A,
        )
        ep = r["cluster_endpoints"][0]["Endpoint"]
        rc_client = rc_sess.client("route53-recovery-cluster", endpoint_url=ep if ep.startswith("https://") else f"https://{ep}")

        rc_client.update_routing_control_states(UpdateRoutingControlStateEntries=[
            {"RoutingControlArn": r["rc_primary"], "RoutingControlState": "Off"},
            {"RoutingControlArn": r["rc_secondary"], "RoutingControlState": "On"},
        ])
        print("  ❌ UNEXPECTED: call succeeded — boundary may not be effective")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"]["Message"]
        print(f"  ✅ EXPECTED DENY: {error_code}")
        print(f"     {error_msg}")
        print("\n  This is exactly what happens when ARC Region Switch executes the plan.")
        print("  The permission boundary simulates the SCP: the role has the identity")
        print("  permission, but the boundary's deny condition fires because the call")
        print("  does not come from a known network or via an AWS service.")


# ═══════════════════════════════════════════════════════════════════════════════
#  FIX — Add ArnNotLike exemption
# ═══════════════════════════════════════════════════════════════════════════════

def fix(profile):
    r = load()
    if not r:
        print("No resources.json. Run deploy first.")
        return

    sess = get_session(profile, REGION_A)
    iam = sess.client("iam")

    print("── Adding ArnNotLike exemption to the permission boundary ──")

    scp_doc = json.loads(SCP_FILE.read_text())
    deny_statement = scp_doc["Statement"][0]

    # Add the exemption
    deny_statement["Condition"]["ArnNotLike"] = {
        "aws:PrincipalArn": f"arn:aws:iam::*:role/{r['role_name']}"
    }

    new_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "AllowAll", "Effect": "Allow", "Action": "*", "Resource": "*"},
            deny_statement,
        ],
    })

    # Create new version of the policy
    try:
        iam.create_policy_version(
            PolicyArn=r["boundary_arn"],
            PolicyDocument=new_policy,
            SetAsDefault=True,
        )
        print(f"  ✅ Exemption added for role {r['role_name']}")
        print(f"\n  Added to DenyRecoveryClusterUnlessKnownNetwork:")
        print(f'    "ArnNotLike": {{"aws:PrincipalArn": "arn:aws:iam::*:role/{r["role_name"]}"}}')
        print(f"\n  Run: python deploy_lab.py test-allow")
    except ClientError as e:
        if "LimitExceeded" in str(e):
            # Delete oldest non-default version first
            versions = iam.list_policy_versions(PolicyArn=r["boundary_arn"])["Versions"]
            for v in versions:
                if not v["IsDefaultVersion"]:
                    iam.delete_policy_version(PolicyArn=r["boundary_arn"], VersionId=v["VersionId"])
                    break
            iam.create_policy_version(
                PolicyArn=r["boundary_arn"],
                PolicyDocument=new_policy,
                SetAsDefault=True,
            )
            print(f"  ✅ Exemption added (replaced old policy version)")
        else:
            raise


# ═══════════════════════════════════════════════════════════════════════════════
#  TEST-ALLOW — Re-execute after fix
# ═══════════════════════════════════════════════════════════════════════════════

def test_allow(profile):
    r = load()
    if not r:
        print("No resources.json. Run deploy first.")
        return

    sess = get_session(profile, REGION_A)
    sts = sess.client("sts")

    print("── Attempting routing control update after fix ──")

    creds = sts.assume_role(
        RoleArn=r["role_arn"],
        RoleSessionName="RegionSwitchExecution-lab-fixed",
    )["Credentials"]

    rc_sess = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=REGION_A,
    )
    ep = r["cluster_endpoints"][0]["Endpoint"]
    rc_client = rc_sess.client("route53-recovery-cluster", endpoint_url=ep if ep.startswith("https://") else f"https://{ep}")

    try:
        rc_client.update_routing_control_states(UpdateRoutingControlStateEntries=[
            {"RoutingControlArn": r["rc_primary"], "RoutingControlState": "Off"},
            {"RoutingControlArn": r["rc_secondary"], "RoutingControlState": "On"},
        ])
        print("  ✅ SUCCESS — routing controls flipped")
        print("     primary=OFF, secondary=ON")
        print("\n  The ArnNotLike exemption allows the execution role to bypass the")
        print("  network-perimeter deny while keeping the guardrail active for everyone else.")

        # Verify state
        state_pri = rc_client.get_routing_control_state(RoutingControlArn=r["rc_primary"])
        state_sec = rc_client.get_routing_control_state(RoutingControlArn=r["rc_secondary"])
        print(f"\n  Primary:   {state_pri['RoutingControlState']}")
        print(f"  Secondary: {state_sec['RoutingControlState']}")

    except ClientError as e:
        print(f"  ❌ STILL DENIED: {e}")
        print("  Check that the fix was applied correctly.")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def teardown(profile):
    r = load()
    if not r:
        print("No resources.json.")
        return

    sess_a = get_session(profile, REGION_A)
    sess_b = get_session(profile, REGION_B)
    iam = sess_a.client("iam")
    r53 = sess_a.client("route53")
    r53rc = sess_a.client("route53-recovery-control-config")

    def safe(fn, *a, **kw):
        try:
            fn(*a, **kw)
        except Exception as e:
            print(f"  (skip: {e})")

    # ── Route 53 records ───────────────────────────────────────────────────
    print("Deleting Route 53 records...")
    if r.get("zone_id"):
        try:
            rrsets = r53.list_resource_record_sets(HostedZoneId=r["zone_id"])["ResourceRecordSets"]
            changes = [{"Action": "DELETE", "ResourceRecordSet": rr}
                      for rr in rrsets if rr["Type"] not in ("SOA", "NS")]
            if changes:
                r53.change_resource_record_sets(HostedZoneId=r["zone_id"],
                                                ChangeBatch={"Changes": changes})
        except Exception:
            pass
        safe(r53.delete_hosted_zone, Id=r["zone_id"])

    # ── Health checks ──────────────────────────────────────────────────────
    print("Deleting health checks...")
    for hc in [r.get("hc_primary"), r.get("hc_secondary")]:
        if hc:
            safe(r53.delete_health_check, HealthCheckId=hc)

    # ── Routing controls ───────────────────────────────────────────────────
    print("Deleting routing controls...")
    for rc in [r.get("rc_primary"), r.get("rc_secondary")]:
        if rc:
            safe(r53rc.delete_routing_control, RoutingControlArn=rc)

    # ── Control panel ──────────────────────────────────────────────────────
    if r.get("panel_arn"):
        print("Deleting control panel...")
        safe(r53rc.delete_control_panel, ControlPanelArn=r["panel_arn"])

    # ── Cluster ────────────────────────────────────────────────────────────
    if r.get("cluster_arn"):
        print("Deleting ARC cluster (takes ~5 min)...")
        safe(r53rc.delete_cluster, ClusterArn=r["cluster_arn"])

    # ── IAM ────────────────────────────────────────────────────────────────
    print("Cleaning up IAM...")
    if r.get("role_name"):
        safe(iam.delete_role_policy, RoleName=r["role_name"],
             PolicyName="region-switch-permissions")
        safe(iam.delete_role, RoleName=r["role_name"])

    if r.get("boundary_arn"):
        # Delete all non-default versions first
        try:
            versions = iam.list_policy_versions(PolicyArn=r["boundary_arn"])["Versions"]
            for v in versions:
                if not v["IsDefaultVersion"]:
                    iam.delete_policy_version(PolicyArn=r["boundary_arn"], VersionId=v["VersionId"])
        except Exception:
            pass
        safe(iam.delete_policy, PolicyArn=r["boundary_arn"])

    # ── API Gateways ───────────────────────────────────────────────────────
    print("Deleting API Gateways...")
    for label, client in [("east", sess_a.client("apigateway")), ("west", sess_b.client("apigateway"))]:
        if r.get(f"apigw_{label}"):
            safe(client.delete_rest_api, restApiId=r[f"apigw_{label}"]["id"])

    RESOURCE_FILE.unlink(missing_ok=True)
    print("\n✅ Teardown complete. ARC cluster deletion takes ~5 minutes in background.")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="NET-016 ARC Region Switch SCP Lab")
    parser.add_argument("command", choices=["deploy", "status", "test-deny", "fix", "test-allow", "teardown"])
    parser.add_argument("--profile", default=None)
    args = parser.parse_args()

    commands = {
        "deploy": deploy, "status": status,
        "test-deny": test_deny, "fix": fix, "test-allow": test_allow,
        "teardown": teardown,
    }
    commands[args.command](args.profile)


if __name__ == "__main__":
    main()
