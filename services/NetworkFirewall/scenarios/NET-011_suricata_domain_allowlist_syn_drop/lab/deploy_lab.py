#!/usr/bin/env python3
"""
NET-011 lab — centralized inspection replica of the "Suricata domain allowlist
never matches" failure.

Architecture (mirrors an LZA hub-and-spoke landing zone):

    spoke VPC 10.212.0.0/16                inspection VPC 10.208.0.0/16
    +---------------------+                +--------------------------------+
    | workload subnets    |                | tgw subnets  -> NFW endpoints  |
    |   EC2 + SSM VPCEs   |                | fw subnets   -> NAT gateways   |
    |   0.0.0.0/0 -> TGW  |=== TGW ======> | pub subnets  -> IGW            |
    +---------------------+  appliance     +--------------------------------+
                              mode

The firewall sits BEFORE the NAT gateways so the stateful engine sees the spoke
private IP as the source, exactly as in the customer's alert log.

Policy is deployed in the BROKEN state on purpose: STRICT_ORDER, the correct
`pass tls ... tls.sni; dotprefix` rule at priority 99, and a catch-all
`drop ip any any -> any any (flow:to_server;)` at priority 999 that matches the
TCP SYN and makes every application-layer rule unreachable.

Usage:
    python3 deploy_lab.py deploy
    python3 deploy_lab.py status
    python3 deploy_lab.py teardown

Cost: ~$1.02/hour (2 NFW endpoints, 2 TGW attachments, 2 NAT gateways,
1 t3.micro, 3 interface endpoints). Tear it down when done.
"""

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROFILE = "lab338"
REGION = "us-east-1"
AZS = ["us-east-1a", "us-east-1b"]
PREFIX = "net011"

INSPECTION_CIDR = "10.208.0.0/16"
SPOKE_CIDR = "10.212.0.0/16"

# inspection VPC subnets, per AZ index
FW_SUBNETS = ["10.208.1.0/24", "10.208.2.0/24"]
TGW_SUBNETS = ["10.208.11.0/24", "10.208.12.0/24"]
PUB_SUBNETS = ["10.208.21.0/24", "10.208.22.0/24"]
# spoke VPC subnets
WL_SUBNETS = ["10.212.2.0/24", "10.212.3.0/24"]

HOME_NET = "10.0.0.0/8"  # what the customer overrode it to
MANAGED_RG = (
    "arn:aws:network-firewall:us-east-1:aws-managed:"
    "stateful-rulegroup/AttackInfrastructureStrictOrder"
)

STATE_FILE = Path(__file__).with_name("resources.json")

# ---------------------------------------------------------------------------
# Suricata rule groups
# ---------------------------------------------------------------------------

# priority 10 — alert-only probes. sid:10003 is the control: an IP-level alert
# fires even on the SYN, which proves packets reach the engine while the two
# tls.sni probes stay silent because the buffer does not exist yet.
RULES_PROBES = f"""\
alert ip {SPOKE_CIDR} any -> any any (msg:"PROBE ip any packet"; sid:10003; rev:1;)
alert tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:"amazonaws"; nocase; msg:"PROBE sni homenet"; sid:10001; rev:1;)
alert tls {SPOKE_CIDR} any -> any any (tls.sni; content:"amazonaws"; nocase; msg:"PROBE sni hardcoded src"; sid:10002; rev:1;)
"""

# priority 99 — the allowlist. sid:20090 is the round-1 bug kept as an alert so
# it can be shown never firing; it is placed before the pass because `pass` is
# terminating in strict order and would otherwise pre-empt it.
RULES_AMAZONAWS = """\
alert tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:".amazonaws.com"; startswith; nocase; endswith; msg:"ROUND1 BUG impossible anchors"; flow:to_server,established; sid:20090; rev:1;)
alert tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; content:".amazonaws.com"; endswith; msg:"MATCH amazonaws sni"; flow:to_server,established; sid:20091; rev:1;)
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; content:".amazonaws.com"; endswith; msg:"Allow AWS service endpoints"; flow:to_server,established; sid:20000; rev:1;)
"""

# priority 999 — verbatim from the customer, missing `established`
RULES_DEFAULTDENY = """\
drop ip any any -> any any (sid:30000;msg:"Default drop"; flow:to_server;)
"""

RULE_GROUPS = [
    ("010Probes", 30, RULES_PROBES, True),
    ("099Amazonaws", 30, RULES_AMAZONAWS, True),
    ("999DefaultDeny", 10, RULES_DEFAULTDENY, False),
]

# ---------------------------------------------------------------------------


def session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def log(msg):
    print(f"  {msg}", flush=True)


def name_tags(name, extra=None):
    tags = [{"Key": "Name", "Value": name}, {"Key": "Lab", "Value": PREFIX}]
    if extra:
        tags.extend({"Key": k, "Value": v} for k, v in extra.items())
    return tags


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def wait_for(fn, desc, timeout=900, interval=15):
    """Poll fn() until it returns truthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {desc}")


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


def deploy():
    sess = session()
    ec2 = sess.client("ec2")
    nfw = sess.client("network-firewall")
    iam = sess.client("iam")
    logs = sess.client("logs")
    st = load_state()

    print("\n[1/9] VPCs and subnets")
    st.setdefault("subnets", {})

    if "inspection_vpc" not in st:
        vpc = ec2.create_vpc(
            CidrBlock=INSPECTION_CIDR, TagSpecifications=[
                {"ResourceType": "vpc", "Tags": name_tags(f"{PREFIX}-inspection-vpc")}]
        )["Vpc"]
        st["inspection_vpc"] = vpc["VpcId"]
        ec2.modify_vpc_attribute(VpcId=vpc["VpcId"], EnableDnsHostnames={"Value": True})
        log(f"inspection VPC {vpc['VpcId']} {INSPECTION_CIDR}")
    if "spoke_vpc" not in st:
        vpc = ec2.create_vpc(
            CidrBlock=SPOKE_CIDR, TagSpecifications=[
                {"ResourceType": "vpc", "Tags": name_tags(f"{PREFIX}-spoke-vpc")}]
        )["Vpc"]
        st["spoke_vpc"] = vpc["VpcId"]
        ec2.modify_vpc_attribute(VpcId=vpc["VpcId"], EnableDnsHostnames={"Value": True})
        log(f"spoke VPC {vpc['VpcId']} {SPOKE_CIDR}")
    save_state(st)

    def subnet(key, vpc_id, cidr, az, name):
        if key in st["subnets"]:
            return st["subnets"][key]
        sn = ec2.create_subnet(
            VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=az,
            TagSpecifications=[{"ResourceType": "subnet", "Tags": name_tags(name)}],
        )["Subnet"]["SubnetId"]
        st["subnets"][key] = sn
        log(f"{name} {sn} {cidr} {az}")
        return sn

    for i, az in enumerate(AZS):
        suffix = az[-1]
        subnet(f"fw-{suffix}", st["inspection_vpc"], FW_SUBNETS[i], az, f"{PREFIX}-fw-{suffix}")
        subnet(f"tgw-{suffix}", st["inspection_vpc"], TGW_SUBNETS[i], az, f"{PREFIX}-tgw-{suffix}")
        subnet(f"pub-{suffix}", st["inspection_vpc"], PUB_SUBNETS[i], az, f"{PREFIX}-pub-{suffix}")
        subnet(f"wl-{suffix}", st["spoke_vpc"], WL_SUBNETS[i], az, f"{PREFIX}-wl-{suffix}")
    save_state(st)

    print("\n[2/9] IGW and NAT gateways")
    if "igw" not in st:
        igw = ec2.create_internet_gateway(
            TagSpecifications=[{"ResourceType": "internet-gateway",
                                "Tags": name_tags(f"{PREFIX}-igw")}]
        )["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=st["inspection_vpc"])
        st["igw"] = igw
        log(f"IGW {igw}")
        save_state(st)

    st.setdefault("nat", {})
    st.setdefault("eip", {})
    for az in AZS:
        suffix = az[-1]
        if suffix in st["nat"]:
            continue
        if suffix not in st["eip"]:
            st["eip"][suffix] = ec2.allocate_address(
                Domain="vpc",
                TagSpecifications=[{"ResourceType": "elastic-ip",
                                    "Tags": name_tags(f"{PREFIX}-eip-{suffix}")}],
            )["AllocationId"]
            save_state(st)
        nat = ec2.create_nat_gateway(
            SubnetId=st["subnets"][f"pub-{suffix}"], AllocationId=st["eip"][suffix],
            TagSpecifications=[{"ResourceType": "natgateway",
                                "Tags": name_tags(f"{PREFIX}-nat-{suffix}")}],
        )["NatGateway"]["NatGatewayId"]
        st["nat"][suffix] = nat
        log(f"NAT {nat} in pub-{suffix}")
        save_state(st)

    ec2.get_waiter("nat_gateway_available").wait(
        NatGatewayIds=list(st["nat"].values()),
        WaiterConfig={"Delay": 15, "MaxAttempts": 40},
    )
    log("NAT gateways available")

    print("\n[3/9] Transit Gateway")
    if "tgw" not in st:
        tgw = ec2.create_transit_gateway(
            Description=f"{PREFIX} centralized inspection lab",
            Options={
                "DefaultRouteTableAssociation": "disable",
                "DefaultRouteTablePropagation": "disable",
                "DnsSupport": "enable",
                "VpnEcmpSupport": "disable",
                "AutoAcceptSharedAttachments": "disable",
            },
            TagSpecifications=[{"ResourceType": "transit-gateway",
                                "Tags": name_tags(f"{PREFIX}-tgw")}],
        )["TransitGateway"]["TransitGatewayId"]
        st["tgw"] = tgw
        log(f"TGW {tgw}")
        save_state(st)

    wait_for(
        lambda: ec2.describe_transit_gateways(TransitGatewayIds=[st["tgw"]])
        ["TransitGateways"][0]["State"] == "available",
        "TGW available",
    )
    log("TGW available")

    # inspection attachment gets appliance mode so a flow stays pinned to one
    # firewall endpoint in both directions
    if "attach_inspection" not in st:
        att = ec2.create_transit_gateway_vpc_attachment(
            TransitGatewayId=st["tgw"], VpcId=st["inspection_vpc"],
            SubnetIds=[st["subnets"][f"tgw-{az[-1]}"] for az in AZS],
            Options={"ApplianceModeSupport": "enable", "DnsSupport": "enable"},
            TagSpecifications=[{"ResourceType": "transit-gateway-attachment",
                                "Tags": name_tags(f"{PREFIX}-att-inspection")}],
        )["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"]
        st["attach_inspection"] = att
        log(f"inspection attachment {att} (appliance mode enabled)")
        save_state(st)

    if "attach_spoke" not in st:
        att = ec2.create_transit_gateway_vpc_attachment(
            TransitGatewayId=st["tgw"], VpcId=st["spoke_vpc"],
            SubnetIds=[st["subnets"][f"wl-{az[-1]}"] for az in AZS],
            Options={"DnsSupport": "enable"},
            TagSpecifications=[{"ResourceType": "transit-gateway-attachment",
                                "Tags": name_tags(f"{PREFIX}-att-spoke")}],
        )["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"]
        st["attach_spoke"] = att
        log(f"spoke attachment {att}")
        save_state(st)

    def att_available():
        res = ec2.describe_transit_gateway_vpc_attachments(
            TransitGatewayAttachmentIds=[st["attach_inspection"], st["attach_spoke"]]
        )["TransitGatewayVpcAttachments"]
        return all(a["State"] == "available" for a in res)

    wait_for(att_available, "TGW attachments available")
    log("TGW attachments available")

    print("\n[4/9] TGW route tables")
    for key, label in (("tgwrt_spoke", "spoke"), ("tgwrt_inspection", "inspection")):
        if key not in st:
            rt = ec2.create_transit_gateway_route_table(
                TransitGatewayId=st["tgw"],
                TagSpecifications=[{"ResourceType": "transit-gateway-route-table",
                                    "Tags": name_tags(f"{PREFIX}-tgwrt-{label}")}],
            )["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"]
            st[key] = rt
            log(f"TGW route table {label} {rt}")
            save_state(st)

    def tgwrt_available():
        res = ec2.describe_transit_gateway_route_tables(
            TransitGatewayRouteTableIds=[st["tgwrt_spoke"], st["tgwrt_inspection"]]
        )["TransitGatewayRouteTables"]
        return all(r["State"] == "available" for r in res)

    wait_for(tgwrt_available, "TGW route tables available")

    # spoke RT: default route to the inspection VPC  ->  forces egress through NFW
    # inspection RT: static route back to the spoke  ->  return path
    for rt, att, cidr in (
        (st["tgwrt_spoke"], st["attach_inspection"], "0.0.0.0/0"),
        (st["tgwrt_inspection"], st["attach_spoke"], SPOKE_CIDR),
    ):
        try:
            ec2.associate_transit_gateway_route_table(
                TransitGatewayRouteTableId=rt,
                TransitGatewayAttachmentId=(
                    st["attach_spoke"] if rt == st["tgwrt_spoke"] else st["attach_inspection"]
                ),
            )
        except ClientError as e:
            if "already" not in str(e).lower() and "Duplicate" not in str(e):
                raise
        try:
            ec2.create_transit_gateway_route(
                TransitGatewayRouteTableId=rt, DestinationCidrBlock=cidr,
                TransitGatewayAttachmentId=att,
            )
            log(f"TGW route {cidr} -> {att} in {rt}")
        except ClientError as e:
            if "already exists" not in str(e).lower():
                raise

    print("\n[5/9] Network Firewall rule groups and policy")
    st.setdefault("rule_groups", {})
    for name, capacity, rules, uses_homenet in RULE_GROUPS:
        full = f"{PREFIX}-{name}"
        if full in st["rule_groups"]:
            continue
        kwargs = {
            "RuleGroupName": full,
            "Type": "STATEFUL",
            "Capacity": capacity,
            "RuleGroup": {
                "RulesSource": {"RulesString": rules},
                "StatefulRuleOptions": {"RuleOrder": "STRICT_ORDER"},
            },
            "Tags": [{"Key": "Lab", "Value": PREFIX}],
        }
        if uses_homenet:
            kwargs["RuleGroup"]["RuleVariables"] = {
                "IPSets": {"HOME_NET": {"Definition": [HOME_NET]}}
            }
        arn = nfw.create_rule_group(**kwargs)["RuleGroupResponse"]["RuleGroupArn"]
        st["rule_groups"][full] = arn
        log(f"rule group {full}")
        save_state(st)

    if "policy_arn" not in st:
        refs = [
            {"ResourceArn": st["rule_groups"][f"{PREFIX}-010Probes"], "Priority": 10},
            {"ResourceArn": st["rule_groups"][f"{PREFIX}-099Amazonaws"], "Priority": 99},
            {"ResourceArn": MANAGED_RG, "Priority": 100},
            {"ResourceArn": st["rule_groups"][f"{PREFIX}-999DefaultDeny"], "Priority": 999},
        ]
        arn = nfw.create_firewall_policy(
            FirewallPolicyName=f"{PREFIX}-policy",
            FirewallPolicy={
                "StatelessDefaultActions": ["aws:forward_to_sfe"],
                "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"],
                "StatefulDefaultActions": ["aws:drop_established"],
                "StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER"},
                "StatefulRuleGroupReferences": refs,
            },
            Tags=[{"Key": "Lab", "Value": PREFIX}],
        )["FirewallPolicyResponse"]["FirewallPolicyArn"]
        st["policy_arn"] = arn
        log(f"policy {PREFIX}-policy (STRICT_ORDER, aws:drop_established)")
        save_state(st)

    print("\n[6/9] Firewall")
    if "firewall_arn" not in st:
        arn = nfw.create_firewall(
            FirewallName=f"{PREFIX}-fw",
            FirewallPolicyArn=st["policy_arn"],
            VpcId=st["inspection_vpc"],
            SubnetMappings=[{"SubnetId": st["subnets"][f"fw-{az[-1]}"]} for az in AZS],
            DeleteProtection=False,
            SubnetChangeProtection=False,
            FirewallPolicyChangeProtection=False,
            Tags=[{"Key": "Lab", "Value": PREFIX}],
        )["Firewall"]["FirewallArn"]
        st["firewall_arn"] = arn
        log(f"firewall {PREFIX}-fw creating")
        save_state(st)

    def fw_ready():
        r = nfw.describe_firewall(FirewallName=f"{PREFIX}-fw")
        status = r["FirewallStatus"]
        if status["Status"] != "READY":
            return None
        sync = status.get("SyncStates", {})
        if len(sync) < len(AZS):
            return None
        eps = {}
        for az, s in sync.items():
            att = s.get("Attachment", {})
            if att.get("Status") != "READY":
                return None
            eps[az] = att["EndpointId"]
        return eps

    endpoints = wait_for(fw_ready, "firewall READY")
    st["endpoints"] = endpoints
    save_state(st)
    for az, ep in endpoints.items():
        log(f"endpoint {ep} in {az}")

    print("\n[7/9] Logging")
    for kind in ("alert", "flow"):
        group = f"/aws/network-firewall/{PREFIX}/{kind}"
        try:
            logs.create_log_group(logGroupName=group)
            logs.put_retention_policy(logGroupName=group, retentionInDays=1)
            log(f"log group {group}")
        except ClientError as e:
            if "ResourceAlreadyExists" not in str(e):
                raise
    st["log_groups"] = {
        k: f"/aws/network-firewall/{PREFIX}/{k}" for k in ("alert", "flow")
    }
    # UpdateLoggingConfiguration accepts only one destination change per call,
    # so build the list up incrementally
    configs = []
    for log_type in ("ALERT", "FLOW"):
        configs.append({
            "LogType": log_type,
            "LogDestinationType": "CloudWatchLogs",
            "LogDestination": {"logGroup": st["log_groups"][log_type.lower()]},
        })
        nfw.update_logging_configuration(
            FirewallName=f"{PREFIX}-fw",
            LoggingConfiguration={"LogDestinationConfigs": list(configs)},
        )
        log(f"{log_type} logging enabled")
    save_state(st)

    print("\n[8/9] VPC route tables")
    st.setdefault("route_tables", {})

    def route_table(key, vpc_id, name, subnet_ids, routes):
        """routes: list of (cidr, kind, target)"""
        if key not in st["route_tables"]:
            rt = ec2.create_route_table(
                VpcId=vpc_id,
                TagSpecifications=[{"ResourceType": "route-table", "Tags": name_tags(name)}],
            )["RouteTable"]["RouteTableId"]
            st["route_tables"][key] = rt
            save_state(st)
        rt = st["route_tables"][key]
        for cidr, kind, target in routes:
            arg = {
                "igw": "GatewayId", "nat": "NatGatewayId",
                "vpce": "VpcEndpointId", "tgw": "TransitGatewayId",
            }[kind]
            try:
                ec2.create_route(RouteTableId=rt, DestinationCidrBlock=cidr, **{arg: target})
            except ClientError as e:
                if "RouteAlreadyExists" not in str(e):
                    raise
        for sn in subnet_ids:
            assoc = ec2.describe_route_tables(
                Filters=[{"Name": "association.subnet-id", "Values": [sn]}]
            )["RouteTables"]
            if not any(r["RouteTableId"] == rt for r in assoc):
                ec2.associate_route_table(RouteTableId=rt, SubnetId=sn)
        log(f"{name} {rt} ({len(routes)} routes)")

    for az in AZS:
        suffix = az[-1]
        ep = endpoints[az]
        # traffic arriving from TGW is inspected first
        route_table(f"tgw-{suffix}", st["inspection_vpc"], f"{PREFIX}-rtb-tgw-{suffix}",
                    [st["subnets"][f"tgw-{suffix}"]],
                    [("0.0.0.0/0", "vpce", ep)])
        # post-inspection: out via NAT, back to the spoke via TGW
        route_table(f"fw-{suffix}", st["inspection_vpc"], f"{PREFIX}-rtb-fw-{suffix}",
                    [st["subnets"][f"fw-{suffix}"]],
                    [("0.0.0.0/0", "nat", st["nat"][suffix]),
                     (SPOKE_CIDR, "tgw", st["tgw"])])
        # return path from the internet must re-enter the firewall
        route_table(f"pub-{suffix}", st["inspection_vpc"], f"{PREFIX}-rtb-pub-{suffix}",
                    [st["subnets"][f"pub-{suffix}"]],
                    [("0.0.0.0/0", "igw", st["igw"]),
                     (SPOKE_CIDR, "vpce", ep)])

    route_table("wl", st["spoke_vpc"], f"{PREFIX}-rtb-wl",
                [st["subnets"][f"wl-{az[-1]}"] for az in AZS],
                [("0.0.0.0/0", "tgw", st["tgw"])])

    print("\n[9/9] EC2 test host and SSM endpoints")
    role = f"{PREFIX}-ssm-role"
    if "iam_role" not in st:
        try:
            iam.create_role(
                RoleName=role,
                AssumeRolePolicyDocument=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{"Effect": "Allow",
                                   "Principal": {"Service": "ec2.amazonaws.com"},
                                   "Action": "sts:AssumeRole"}],
                }),
            )
        except ClientError as e:
            if "EntityAlreadyExists" not in str(e):
                raise
        iam.attach_role_policy(
            RoleName=role,
            PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        )
        try:
            iam.create_instance_profile(InstanceProfileName=role)
            iam.add_role_to_instance_profile(InstanceProfileName=role, RoleName=role)
        except ClientError as e:
            if "EntityAlreadyExists" not in str(e):
                raise
        st["iam_role"] = role
        log(f"IAM role and instance profile {role}")
        save_state(st)
        time.sleep(12)  # instance profile propagation

    if "sg_endpoint" not in st:
        sg = ec2.create_security_group(
            GroupName=f"{PREFIX}-vpce-sg", Description="SSM interface endpoints",
            VpcId=st["spoke_vpc"],
            TagSpecifications=[{"ResourceType": "security-group",
                                "Tags": name_tags(f"{PREFIX}-vpce-sg")}],
        )["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=sg, IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
                "IpRanges": [{"CidrIp": SPOKE_CIDR}],
            }],
        )
        st["sg_endpoint"] = sg
        log(f"endpoint SG {sg}")
        save_state(st)

    if "sg_instance" not in st:
        sg = ec2.create_security_group(
            GroupName=f"{PREFIX}-host-sg", Description="lab test host",
            VpcId=st["spoke_vpc"],
            TagSpecifications=[{"ResourceType": "security-group",
                                "Tags": name_tags(f"{PREFIX}-host-sg")}],
        )["GroupId"]
        st["sg_instance"] = sg
        log(f"host SG {sg}")
        save_state(st)

    # SSM reaches the instance over PrivateLink so it never crosses the firewall,
    # which is what keeps the host manageable while egress is fully denied
    st.setdefault("vpc_endpoints", {})
    for svc in ("ssm", "ssmmessages", "ec2messages"):
        if svc in st["vpc_endpoints"]:
            continue
        vpce = ec2.create_vpc_endpoint(
            VpcId=st["spoke_vpc"], VpcEndpointType="Interface",
            ServiceName=f"com.amazonaws.{REGION}.{svc}",
            SubnetIds=[st["subnets"][f"wl-{AZS[0][-1]}"]],
            SecurityGroupIds=[st["sg_endpoint"]], PrivateDnsEnabled=True,
            TagSpecifications=[{"ResourceType": "vpc-endpoint",
                                "Tags": name_tags(f"{PREFIX}-vpce-{svc}")}],
        )["VpcEndpoint"]["VpcEndpointId"]
        st["vpc_endpoints"][svc] = vpce
        log(f"interface endpoint {svc} {vpce}")
        save_state(st)

    if "instance" not in st:
        ami = sess.client("ssm").get_parameter(
            Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
        )["Parameter"]["Value"]
        inst = ec2.run_instances(
            ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1,
            SubnetId=st["subnets"][f"wl-{AZS[0][-1]}"],
            SecurityGroupIds=[st["sg_instance"]],
            IamInstanceProfile={"Name": st["iam_role"]},
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            TagSpecifications=[{"ResourceType": "instance",
                                "Tags": name_tags(f"{PREFIX}-host")}],
        )["Instances"][0]["InstanceId"]
        st["instance"] = inst
        log(f"instance {inst}")
        save_state(st)

    ec2.get_waiter("instance_running").wait(InstanceIds=[st["instance"]])
    priv = ec2.describe_instances(InstanceIds=[st["instance"]])[
        "Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    st["instance_private_ip"] = priv
    save_state(st)

    print(f"\nDeployed. Test host {st['instance']} at {priv}")
    print(f"State written to {STATE_FILE}")
    print("\nWait ~2 min for the SSM agent to register, then:")
    print(f"  aws ssm start-session --target {st['instance']} "
          f"--profile {PROFILE} --region {REGION}")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status():
    sess = session()
    nfw = sess.client("network-firewall")
    ec2 = sess.client("ec2")
    st = load_state()
    if not st:
        print("no state file — nothing deployed")
        return

    fw = nfw.describe_firewall(FirewallName=f"{PREFIX}-fw")
    print(f"firewall     {fw['FirewallStatus']['Status']} / "
          f"{fw['FirewallStatus']['ConfigurationSyncStateSummary']}")
    pol = nfw.describe_firewall_policy(FirewallPolicyName=f"{PREFIX}-policy")["FirewallPolicy"]
    print(f"rule order   {pol['StatefulEngineOptions']['RuleOrder']}")
    print(f"default act  {pol['StatefulDefaultActions']}")
    print("rule groups:")
    for r in sorted(pol["StatefulRuleGroupReferences"], key=lambda x: x["Priority"]):
        print(f"  {r['Priority']:>4}  {r['ResourceArn'].split('/')[-1]}")
    if "instance" in st:
        state = ec2.describe_instances(InstanceIds=[st["instance"]])[
            "Reservations"][0]["Instances"][0]["State"]["Name"]
        print(f"test host    {st['instance']} {state} "
              f"{st.get('instance_private_ip', '')}")


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


def teardown():
    sess = session()
    ec2 = sess.client("ec2")
    nfw = sess.client("network-firewall")
    iam = sess.client("iam")
    logs = sess.client("logs")
    st = load_state()
    if not st:
        print("no state file — nothing to tear down")
        return

    def attempt(desc, fn):
        try:
            fn()
            log(f"deleted {desc}")
        except ClientError as e:
            log(f"skip {desc}: {str(e).split(':')[-1].strip()[:90]}")

    print("\n[1/8] EC2 and endpoints")
    if "instance" in st:
        attempt(f"instance {st['instance']}",
                lambda: ec2.terminate_instances(InstanceIds=[st["instance"]]))
        ec2.get_waiter("instance_terminated").wait(
            InstanceIds=[st["instance"]], WaiterConfig={"Delay": 15, "MaxAttempts": 40})
    if st.get("vpc_endpoints"):
        ids = list(st["vpc_endpoints"].values())
        attempt(f"{len(ids)} interface endpoints",
                lambda: ec2.delete_vpc_endpoints(VpcEndpointIds=ids))
        time.sleep(45)

    print("\n[2/8] Firewall")
    if "firewall_arn" in st:
        attempt(f"firewall {PREFIX}-fw",
                lambda: nfw.delete_firewall(FirewallName=f"{PREFIX}-fw"))

        def fw_gone():
            try:
                nfw.describe_firewall(FirewallName=f"{PREFIX}-fw")
                return False
            except ClientError:
                return True

        wait_for(fw_gone, "firewall deleted", timeout=600)
        log("firewall gone")

    print("\n[3/8] Policy and rule groups")
    if "policy_arn" in st:
        attempt(f"policy {PREFIX}-policy",
                lambda: nfw.delete_firewall_policy(FirewallPolicyName=f"{PREFIX}-policy"))
        time.sleep(20)
    for name in list(st.get("rule_groups", {})):
        attempt(f"rule group {name}",
                lambda n=name: nfw.delete_rule_group(RuleGroupName=n, Type="STATEFUL"))

    print("\n[4/8] TGW routes, attachments, route tables")
    for rt, cidr in ((st.get("tgwrt_spoke"), "0.0.0.0/0"),
                     (st.get("tgwrt_inspection"), SPOKE_CIDR)):
        if rt:
            attempt(f"TGW route {cidr}",
                    lambda r=rt, c=cidr: ec2.delete_transit_gateway_route(
                        TransitGatewayRouteTableId=r, DestinationCidrBlock=c))
    for key in ("attach_spoke", "attach_inspection"):
        if key in st:
            attempt(f"TGW attachment {st[key]}",
                    lambda k=key: ec2.delete_transit_gateway_vpc_attachment(
                        TransitGatewayAttachmentId=st[k]))
    if "attach_spoke" in st or "attach_inspection" in st:
        ids = [st[k] for k in ("attach_spoke", "attach_inspection") if k in st]

        def atts_gone():
            res = ec2.describe_transit_gateway_vpc_attachments(
                TransitGatewayAttachmentIds=ids)["TransitGatewayVpcAttachments"]
            return all(a["State"] in ("deleted", "deleting") for a in res)

        wait_for(atts_gone, "TGW attachments deleted", timeout=600)
        time.sleep(60)
    for key in ("tgwrt_spoke", "tgwrt_inspection"):
        if key in st:
            attempt(f"TGW route table {st[key]}",
                    lambda k=key: ec2.delete_transit_gateway_route_table(
                        TransitGatewayRouteTableId=st[k]))

    print("\n[5/8] NAT gateways and EIPs")
    for suffix, nat in st.get("nat", {}).items():
        attempt(f"NAT {nat}", lambda n=nat: ec2.delete_nat_gateway(NatGatewayId=n))
    if st.get("nat"):
        try:
            ec2.get_waiter("nat_gateway_deleted").wait(
                NatGatewayIds=list(st["nat"].values()),
                WaiterConfig={"Delay": 20, "MaxAttempts": 40})
        except Exception:
            pass
    for suffix, eip in st.get("eip", {}).items():
        attempt(f"EIP {eip}", lambda a=eip: ec2.release_address(AllocationId=a))

    print("\n[6/8] Transit Gateway")
    if "tgw" in st:
        attempt(f"TGW {st['tgw']}",
                lambda: ec2.delete_transit_gateway(TransitGatewayId=st["tgw"]))

    print("\n[7/8] VPC scaffolding")
    for key, rt in list(st.get("route_tables", {}).items()):
        for assoc in ec2.describe_route_tables(RouteTableIds=[rt])["RouteTables"][0][
                "Associations"]:
            if not assoc.get("Main"):
                attempt(f"assoc {assoc['RouteTableAssociationId']}",
                        lambda a=assoc: ec2.disassociate_route_table(
                            AssociationId=a["RouteTableAssociationId"]))
        attempt(f"route table {rt}", lambda r=rt: ec2.delete_route_table(RouteTableId=r))
    if "igw" in st:
        attempt(f"IGW detach {st['igw']}",
                lambda: ec2.detach_internet_gateway(
                    InternetGatewayId=st["igw"], VpcId=st["inspection_vpc"]))
        attempt(f"IGW {st['igw']}",
                lambda: ec2.delete_internet_gateway(InternetGatewayId=st["igw"]))
    for sg_key in ("sg_instance", "sg_endpoint"):
        if sg_key in st:
            attempt(f"SG {st[sg_key]}",
                    lambda k=sg_key: ec2.delete_security_group(GroupId=st[k]))
    for key, sn in list(st.get("subnets", {}).items()):
        attempt(f"subnet {sn}", lambda s=sn: ec2.delete_subnet(SubnetId=s))
    for key in ("spoke_vpc", "inspection_vpc"):
        if key in st:
            attempt(f"VPC {st[key]}", lambda k=key: ec2.delete_vpc(VpcId=st[k]))

    print("\n[8/8] IAM and logs")
    if "iam_role" in st:
        role = st["iam_role"]
        attempt("instance profile role detach",
                lambda: iam.remove_role_from_instance_profile(
                    InstanceProfileName=role, RoleName=role))
        attempt("instance profile",
                lambda: iam.delete_instance_profile(InstanceProfileName=role))
        attempt("role policy detach",
                lambda: iam.detach_role_policy(
                    RoleName=role,
                    PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"))
        attempt(f"role {role}", lambda: iam.delete_role(RoleName=role))
    for kind, group in st.get("log_groups", {}).items():
        attempt(f"log group {group}",
                lambda g=group: logs.delete_log_group(logGroupName=g))

    STATE_FILE.unlink(missing_ok=True)
    print("\nTeardown complete, state file removed.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"deploy": deploy, "teardown": teardown, "status": status}[cmd]()
