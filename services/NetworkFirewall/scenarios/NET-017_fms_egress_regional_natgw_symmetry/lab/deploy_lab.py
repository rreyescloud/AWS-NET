"""
NET-017 Lab: Centralized egress inspection vs Regional NAT Gateway symmetry
Region: us-west-2 | Profile: lab

Reproduces the design blocker: TGW (appliance mode ON) -> VPC-endpoint Network Firewall
(2 AZs) -> Regional NAT Gateway. The RNAT has a single route table, so the return route for
the spoke can target only one firewall endpoint. Then applies both fixes:
  Option A - zonal NAT gateways per AZ (per-AZ return routes, NAT waiver eligible)
  Option B - Network Firewall with native TGW attachment, RNAT kept (outside Firewall Manager)

Usage:
  python deploy_lab.py deploy                           # Baseline (broken): RNAT RT 10.10.0.0/16 -> fw AZ1
  python deploy_lab.py test [N]                         # N HTTPS attempts per instance (default 20)
  python deploy_lab.py routes aggregate|per-subnet|tgw  # Rewrite the spoke return route(s) in the RNAT RT
  python deploy_lab.py appliance on|off                 # Toggle appliance mode on the egress attachment
  python deploy_lab.py rules allow|alert                # Domain rule group ALLOWLIST <-> ALERTLIST
  python deploy_lab.py fix-zonal                        # Option A
  python deploy_lab.py fix-tgw-fw                       # Option B
  python deploy_lab.py status
  python deploy_lab.py teardown

Prerequisites:
  pip install boto3   (>= 1.43 for TGW-attached firewall and regional NAT manual mode)
"""

import sys
import time
import json
import boto3
from botocore.exceptions import ClientError

PROFILE = "lab"
REGION = "us-west-2"
PREFIX = "net017-lab"
AZ1 = "us-west-2a"
AZ2 = "us-west-2b"
AZS = [AZ1, AZ2]

EGRESS_VPC_CIDR = "10.2.0.0/16"
EGRESS_TGW = {AZ1: "10.2.10.0/24", AZ2: "10.2.20.0/24"}
EGRESS_FW = {AZ1: "10.2.1.0/24", AZ2: "10.2.2.0/24"}
EGRESS_PUBLIC = {AZ1: "10.2.100.0/24", AZ2: "10.2.200.0/24"}   # Option A only

SPOKE_VPC_CIDR = "10.10.0.0/16"
SPOKE_PRIVATE = {AZ1: "10.10.1.0/24", AZ2: "10.10.2.0/24"}

HOME_NET = ["10.0.0.0/8"]
ALLOWED_DOMAIN = "checkip.amazonaws.com"   # returns the public IP -> tells us which NAT AZ handled the flow
BLOCKED_URL = "https://www.example.com"

LAB_TAG = {"Key": "Project", "Value": "NET-017"}
RESOURCES_FILE = "resources.json"
ROLE_NAME = "NET017-SSM-Role"
PROFILE_NAME = "NET017-SSM-Profile"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ec2 = session.client("ec2")
nfw = session.client("network-firewall")
iam = session.client("iam")
ssm = session.client("ssm")
logs = session.client("logs")


def tag(name):
    return [{"Key": "Name", "Value": f"{PREFIX}-{name}"}, LAB_TAG]


def spec(resource_type, name):
    return [{"ResourceType": resource_type, "Tags": tag(name)}]


def save_resources(resources):
    with open(RESOURCES_FILE, "w") as f:
        json.dump(resources, f, indent=2)


def load_resources():
    try:
        with open(RESOURCES_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def short(az):
    return "az1" if az == AZ1 else "az2"


def put_route(rt_id, cidr, **target):
    """Create the route, or replace it if it already exists."""
    try:
        ec2.create_route(RouteTableId=rt_id, DestinationCidrBlock=cidr, **target)
    except ClientError as e:
        if e.response["Error"]["Code"] != "RouteAlreadyExists":
            raise
        ec2.replace_route(RouteTableId=rt_id, DestinationCidrBlock=cidr, **target)


def drop_route(rt_id, cidr):
    try:
        ec2.delete_route(RouteTableId=rt_id, DestinationCidrBlock=cidr)
    except ClientError:
        pass


def put_tgw_route(rt_id, cidr, att_id):
    try:
        ec2.create_transit_gateway_route(TransitGatewayRouteTableId=rt_id, DestinationCidrBlock=cidr,
                                         TransitGatewayAttachmentId=att_id)
    except ClientError as e:
        if "RouteAlreadyExists" not in e.response["Error"]["Code"]:
            raise
        ec2.replace_transit_gateway_route(TransitGatewayRouteTableId=rt_id, DestinationCidrBlock=cidr,
                                          TransitGatewayAttachmentId=att_id)


def wait_until(check, label, delay=15):
    while True:
        state = check()
        if state in ("available", "READY"):
            return
        if state in ("failed", "FAILED", "ERROR"):
            raise Exception(f"{label} failed")
        print(f"    {label}: {state}...")
        time.sleep(delay)


def wait_tgw(tgw_id):
    wait_until(lambda: ec2.describe_transit_gateways(TransitGatewayIds=[tgw_id])["TransitGateways"][0]["State"], "TGW")


def wait_tgw_attachment(att_id):
    wait_until(lambda: ec2.describe_transit_gateway_attachments(
        TransitGatewayAttachmentIds=[att_id])["TransitGatewayAttachments"][0]["State"], f"attachment {att_id}", 10)


def wait_tgw_rt(rt_id):
    wait_until(lambda: ec2.describe_transit_gateway_route_tables(
        TransitGatewayRouteTableIds=[rt_id])["TransitGatewayRouteTables"][0]["State"], "TGW RT", 5)


def wait_firewall(fw_arn):
    wait_until(lambda: nfw.describe_firewall(FirewallArn=fw_arn)["FirewallStatus"]["Status"], "Firewall", 20)
    return nfw.describe_firewall(FirewallArn=fw_arn)


def wait_nat(nat_id):
    wait_until(lambda: ec2.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0]["State"], f"NAT {nat_id}")
    return ec2.describe_nat_gateways(NatGatewayIds=[nat_id])["NatGateways"][0]


def enable_logging(fw_arn, name):
    for log_type in ("flow", "alert"):
        try:
            logs.create_log_group(logGroupName=f"/aws/network-firewall/{PREFIX}/{name}/{log_type}")
        except ClientError:
            pass
    # Logging destinations must be added one at a time
    flow = {"LogType": "FLOW", "LogDestinationType": "CloudWatchLogs",
            "LogDestination": {"logGroup": f"/aws/network-firewall/{PREFIX}/{name}/flow"}}
    alert = {"LogType": "ALERT", "LogDestinationType": "CloudWatchLogs",
             "LogDestination": {"logGroup": f"/aws/network-firewall/{PREFIX}/{name}/alert"}}
    nfw.update_logging_configuration(FirewallArn=fw_arn, LoggingConfiguration={"LogDestinationConfigs": [flow]})
    nfw.update_logging_configuration(FirewallArn=fw_arn, LoggingConfiguration={"LogDestinationConfigs": [flow, alert]})


def domain_rule_group(generated_type):
    return {
        "RuleVariables": {"IPSets": {"HOME_NET": {"Definition": HOME_NET}}},
        "RulesSource": {"RulesSourceList": {"Targets": [ALLOWED_DOMAIN], "TargetTypes": ["TLS_SNI", "HTTP_HOST"],
                                            "GeneratedRulesType": generated_type}},
        "StatefulRuleOptions": {"RuleOrder": "STRICT_ORDER"},
    }


# ============================================================
# DEPLOY — baseline that reproduces the customer's design
# ============================================================
def deploy():
    r = {}
    print("=" * 60)
    print("NET-017 LAB DEPLOYMENT (baseline: VPC-endpoint firewall + Regional NAT)")
    print("=" * 60)

    # Step 1: VPCs
    print("\n[1/10] Creating VPCs...")
    r["egress_vpc_id"] = ec2.create_vpc(CidrBlock=EGRESS_VPC_CIDR, TagSpecifications=spec("vpc", "egress-vpc"))["Vpc"]["VpcId"]
    r["spoke_vpc_id"] = ec2.create_vpc(CidrBlock=SPOKE_VPC_CIDR, TagSpecifications=spec("vpc", "spoke-vpc"))["Vpc"]["VpcId"]
    for vpc in (r["egress_vpc_id"], r["spoke_vpc_id"]):
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsSupport={"Value": True})
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsHostnames={"Value": True})
    print(f"  Egress VPC: {r['egress_vpc_id']}")
    print(f"  Spoke VPC:  {r['spoke_vpc_id']}")

    # Step 2: Subnets (no public subnets — the RNAT doesn't need them)
    print("\n[2/10] Creating Subnets...")
    for az in AZS:
        s = short(az)
        r[f"tgw_sub_{s}"] = ec2.create_subnet(VpcId=r["egress_vpc_id"], CidrBlock=EGRESS_TGW[az], AvailabilityZone=az,
                                              TagSpecifications=spec("subnet", f"tgw-subnet-{s}"))["Subnet"]["SubnetId"]
        r[f"fw_sub_{s}"] = ec2.create_subnet(VpcId=r["egress_vpc_id"], CidrBlock=EGRESS_FW[az], AvailabilityZone=az,
                                             TagSpecifications=spec("subnet", f"fw-subnet-{s}"))["Subnet"]["SubnetId"]
        r[f"spoke_sub_{s}"] = ec2.create_subnet(VpcId=r["spoke_vpc_id"], CidrBlock=SPOKE_PRIVATE[az], AvailabilityZone=az,
                                                TagSpecifications=spec("subnet", f"spoke-private-{s}"))["Subnet"]["SubnetId"]
    print(f"  TGW:   {r['tgw_sub_az1']}, {r['tgw_sub_az2']}")
    print(f"  FW:    {r['fw_sub_az1']}, {r['fw_sub_az2']}")
    print(f"  Spoke: {r['spoke_sub_az1']}, {r['spoke_sub_az2']}")
    save_resources(r)

    # Step 3: IGW
    print("\n[3/10] Creating Internet Gateway...")
    r["igw_id"] = ec2.create_internet_gateway(TagSpecifications=spec("internet-gateway", "igw"))["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=r["igw_id"], VpcId=r["egress_vpc_id"])
    print(f"  IGW: {r['igw_id']}")

    # Step 4: TGW
    print("\n[4/10] Creating Transit Gateway (~2 min)...")
    r["tgw_id"] = ec2.create_transit_gateway(
        Description="NET-017 lab TGW",
        Options={"DefaultRouteTableAssociation": "disable", "DefaultRouteTablePropagation": "disable",
                 "AutoAcceptSharedAttachments": "enable"},
        TagSpecifications=spec("transit-gateway", "tgw"))["TransitGateway"]["TransitGatewayId"]
    save_resources(r)
    wait_tgw(r["tgw_id"])
    print(f"  TGW: {r['tgw_id']}")

    # Step 5: TGW attachments — appliance mode ON for egress, as in the case
    print("\n[5/10] Creating TGW Attachments (egress: appliance mode ON)...")
    r["egress_att_id"] = ec2.create_transit_gateway_vpc_attachment(
        TransitGatewayId=r["tgw_id"], VpcId=r["egress_vpc_id"], SubnetIds=[r["tgw_sub_az1"], r["tgw_sub_az2"]],
        Options={"ApplianceModeSupport": "enable"},
        TagSpecifications=spec("transit-gateway-attachment", "tgw-att-egress"))["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"]
    r["spoke_att_id"] = ec2.create_transit_gateway_vpc_attachment(
        TransitGatewayId=r["tgw_id"], VpcId=r["spoke_vpc_id"], SubnetIds=[r["spoke_sub_az1"], r["spoke_sub_az2"]],
        TagSpecifications=spec("transit-gateway-attachment", "tgw-att-spoke"))["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"]
    save_resources(r)
    time.sleep(20)
    wait_tgw_attachment(r["egress_att_id"])
    wait_tgw_attachment(r["spoke_att_id"])
    print(f"  Egress: {r['egress_att_id']}")
    print(f"  Spoke:  {r['spoke_att_id']}")

    # Step 6: TGW route tables
    print("\n[6/10] Creating TGW Route Tables...")
    r["tgw_rt_spoke"] = ec2.create_transit_gateway_route_table(
        TransitGatewayId=r["tgw_id"], TagSpecifications=spec("transit-gateway-route-table", "tgw-rt-spoke"))["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"]
    r["tgw_rt_egress"] = ec2.create_transit_gateway_route_table(
        TransitGatewayId=r["tgw_id"], TagSpecifications=spec("transit-gateway-route-table", "tgw-rt-egress"))["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"]
    save_resources(r)
    wait_tgw_rt(r["tgw_rt_spoke"])
    wait_tgw_rt(r["tgw_rt_egress"])
    ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=r["tgw_rt_spoke"], TransitGatewayAttachmentId=r["spoke_att_id"])
    ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=r["tgw_rt_egress"], TransitGatewayAttachmentId=r["egress_att_id"])
    put_tgw_route(r["tgw_rt_spoke"], "0.0.0.0/0", r["egress_att_id"])
    ec2.enable_transit_gateway_route_table_propagation(TransitGatewayRouteTableId=r["tgw_rt_egress"], TransitGatewayAttachmentId=r["spoke_att_id"])
    print(f"  Spoke RT:  {r['tgw_rt_spoke']} (0/0 -> egress)")
    print(f"  Egress RT: {r['tgw_rt_egress']} (propagated spoke)")

    # Step 7: Rule group + policy (strict order, domain ALLOWLIST, HOME_NET set) + VPC-endpoint firewall
    print("\n[7/10] Creating Network Firewall (~5 min)...")
    r["rg_arn"] = nfw.create_rule_group(
        RuleGroupName=f"{PREFIX}-domains", Type="STATEFUL", Capacity=100, RuleGroup=domain_rule_group("ALLOWLIST"),
        Tags=[{"Key": "Name", "Value": f"{PREFIX}-domains"}, LAB_TAG])["RuleGroupResponse"]["RuleGroupArn"]
    r["policy_arn"] = nfw.create_firewall_policy(
        FirewallPolicyName=f"{PREFIX}-policy",
        FirewallPolicy={
            "StatelessDefaultActions": ["aws:forward_to_sfe"],
            "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"],
            "StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER"},
            "StatefulDefaultActions": ["aws:drop_established", "aws:alert_established"],
            "StatefulRuleGroupReferences": [{"ResourceArn": r["rg_arn"], "Priority": 100}],
        },
        Tags=[{"Key": "Name", "Value": f"{PREFIX}-policy"}, LAB_TAG])["FirewallPolicyResponse"]["FirewallPolicyArn"]
    r["fw_arn"] = nfw.create_firewall(
        FirewallName=f"{PREFIX}-vpc-fw", FirewallPolicyArn=r["policy_arn"], VpcId=r["egress_vpc_id"],
        SubnetMappings=[{"SubnetId": r["fw_sub_az1"]}, {"SubnetId": r["fw_sub_az2"]}], DeleteProtection=False,
        Tags=[{"Key": "Name", "Value": f"{PREFIX}-vpc-fw"}, LAB_TAG])["Firewall"]["FirewallArn"]
    save_resources(r)
    fw_ready = wait_firewall(r["fw_arn"])
    for az, state in fw_ready["FirewallStatus"]["SyncStates"].items():
        r[f"fw_endpoint_{short(az)}"] = state["Attachment"]["EndpointId"]
    enable_logging(r["fw_arn"], "vpc-fw")
    print(f"  Endpoint AZ1: {r['fw_endpoint_az1']}")
    print(f"  Endpoint AZ2: {r['fw_endpoint_az2']}")

    # Step 8: Regional NAT Gateway, manual mode, one EIP per AZ (as in the case)
    print("\n[8/10] Creating Regional NAT Gateway (manual mode, 1 EIP per AZ)...")
    az_addresses = []
    r["eips"] = {}
    for az in AZS:
        eip = ec2.allocate_address(Domain="vpc", TagSpecifications=spec("elastic-ip", f"rnat-{short(az)}"))
        r["eips"][eip["PublicIp"]] = {"allocation_id": eip["AllocationId"], "label": f"RNAT {short(az).upper()}"}
        az_addresses.append({"AvailabilityZone": az, "AllocationIds": [eip["AllocationId"]]})
    save_resources(r)
    r["rnat_id"] = ec2.create_nat_gateway(
        VpcId=r["egress_vpc_id"], ConnectivityType="public", AvailabilityMode="regional",
        AvailabilityZoneAddresses=az_addresses, TagSpecifications=spec("natgateway", "rnat"))["NatGateway"]["NatGatewayId"]
    save_resources(r)
    r["rnat_rt_id"] = wait_nat(r["rnat_id"]).get("RouteTableId", "")
    print(f"  RNAT: {r['rnat_id']}  RT: {r['rnat_rt_id']}")

    # Step 9: VPC route tables
    print("\n[9/10] Configuring Route Tables...")
    for az in AZS:
        s = short(az)
        # TGW subnet (AZ x) -> firewall endpoint (AZ x)
        r[f"rt_tgw_{s}"] = ec2.create_route_table(VpcId=r["egress_vpc_id"], TagSpecifications=spec("route-table", f"rt-tgw-{s}"))["RouteTable"]["RouteTableId"]
        put_route(r[f"rt_tgw_{s}"], "0.0.0.0/0", VpcEndpointId=r[f"fw_endpoint_{s}"])
        ec2.associate_route_table(RouteTableId=r[f"rt_tgw_{s}"], SubnetId=r[f"tgw_sub_{s}"])
        # Firewall subnet (AZ x) -> RNAT; spoke -> TGW
        r[f"rt_fw_{s}"] = ec2.create_route_table(VpcId=r["egress_vpc_id"], TagSpecifications=spec("route-table", f"rt-fw-{s}"))["RouteTable"]["RouteTableId"]
        put_route(r[f"rt_fw_{s}"], "0.0.0.0/0", NatGatewayId=r["rnat_id"])
        put_route(r[f"rt_fw_{s}"], SPOKE_VPC_CIDR, TransitGatewayId=r["tgw_id"])
        ec2.associate_route_table(RouteTableId=r[f"rt_fw_{s}"], SubnetId=r[f"fw_sub_{s}"])
    save_resources(r)
    set_rnat_routes(r, "aggregate")

    r["rt_spoke"] = ec2.create_route_table(VpcId=r["spoke_vpc_id"], TagSpecifications=spec("route-table", "rt-spoke"))["RouteTable"]["RouteTableId"]
    put_route(r["rt_spoke"], "0.0.0.0/0", TransitGatewayId=r["tgw_id"])
    for az in AZS:
        ec2.associate_route_table(RouteTableId=r["rt_spoke"], SubnetId=r[f"spoke_sub_{short(az)}"])
    save_resources(r)
    print("  Route tables configured.")

    # Step 10: EC2 + SSM endpoints (SSM stays inside the spoke, off the firewall path)
    print("\n[10/10] Launching EC2 instances + SSM endpoints...")
    try:
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}))
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        iam.create_instance_profile(InstanceProfileName=PROFILE_NAME)
        iam.add_role_to_instance_profile(InstanceProfileName=PROFILE_NAME, RoleName=ROLE_NAME)
        time.sleep(10)
    except ClientError:
        pass

    r["sg_id"] = ec2.create_security_group(GroupName=f"{PREFIX}-spoke-sg", Description="NET-017 spoke", VpcId=r["spoke_vpc_id"],
                                           TagSpecifications=spec("security-group", "spoke-sg"))["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=r["sg_id"], IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": SPOKE_VPC_CIDR}]}])
    save_resources(r)

    for svc in ["ssm", "ssmmessages", "ec2messages"]:
        ec2.create_vpc_endpoint(VpcId=r["spoke_vpc_id"], ServiceName=f"com.amazonaws.{REGION}.{svc}", VpcEndpointType="Interface",
                                SubnetIds=[r["spoke_sub_az1"], r["spoke_sub_az2"]], SecurityGroupIds=[r["sg_id"]],
                                PrivateDnsEnabled=True, TagSpecifications=spec("vpc-endpoint", f"{svc}-endpoint"))

    ami = sorted(ec2.describe_images(Owners=["amazon"], Filters=[
        {"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]}, {"Name": "state", "Values": ["available"]}])["Images"],
        key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]
    for az in AZS:
        s = short(az)
        r[f"ec2_{s}"] = ec2.run_instances(
            ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1, SubnetId=r[f"spoke_sub_{s}"],
            SecurityGroupIds=[r["sg_id"]], IamInstanceProfile={"Name": PROFILE_NAME},
            TagSpecifications=spec("instance", f"test-ec2-{s}"))["Instances"][0]["InstanceId"]
    save_resources(r)
    print(f"  EC2 AZ1: {r['ec2_az1']}")
    print(f"  EC2 AZ2: {r['ec2_az2']}")

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE — resources saved to resources.json")
    print("=" * 60)
    print("\nBaseline is BROKEN on purpose: RNAT RT sends 10.10.0.0/16 -> firewall AZ1 only.")
    print("Wait ~3 min for SSM registration, then run: python deploy_lab.py test")


# ============================================================
# RNAT RETURN ROUTES
# ============================================================
def set_rnat_routes(r, mode):
    """aggregate: 10.10.0.0/16 -> fw AZ1 (the only thing a single RT can express for an aggregated spoke)
    per-subnet: 10.10.1.0/24 -> fw AZ1, 10.10.2.0/24 -> fw AZ2 (NET-005 fix)
    tgw: 10.10.0.0/16 -> TGW (return skips the firewall entirely)"""
    rt = r["rnat_rt_id"]
    for cidr in [SPOKE_VPC_CIDR] + list(SPOKE_PRIVATE.values()):
        drop_route(rt, cidr)
    if mode == "aggregate":
        put_route(rt, SPOKE_VPC_CIDR, VpcEndpointId=r["fw_endpoint_az1"])
    elif mode == "per-subnet":
        for az in AZS:
            put_route(rt, SPOKE_PRIVATE[az], VpcEndpointId=r[f"fw_endpoint_{short(az)}"])
    elif mode == "tgw":
        put_route(rt, SPOKE_VPC_CIDR, TransitGatewayId=r["tgw_id"])
    else:
        raise SystemExit(f"Unknown route mode: {mode}")
    print(f"  RNAT RT {rt}: return routes set to '{mode}'")


def routes(mode):
    set_rnat_routes(load_resources(), mode)


def appliance(mode):
    r = load_resources()
    value = {"on": "enable", "off": "disable"}[mode]
    ec2.modify_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=r["egress_att_id"], Options={"ApplianceModeSupport": value})
    print(f"  Appliance mode on {r['egress_att_id']}: {value}")


def rules(mode):
    r = load_resources()
    generated_type = {"allow": "ALLOWLIST", "alert": "ALERTLIST"}[mode]
    token = nfw.describe_rule_group(RuleGroupArn=r["rg_arn"])["UpdateToken"]
    nfw.update_rule_group(RuleGroupArn=r["rg_arn"], UpdateToken=token, RuleGroup=domain_rule_group(generated_type))
    print(f"  Rule group set to {generated_type}. Allow ~1 min for the firewall to sync, then run test.")


# ============================================================
# TEST
# ============================================================
def test(count=20):
    r = load_resources()
    if not r:
        print("No resources.json found. Run 'deploy' first.")
        return

    print("=" * 60)
    print(f"NET-017 CONNECTIVITY TEST — {count} new TCP flows per instance")
    print("=" * 60)

    instances = [r["ec2_az1"], r["ec2_az2"]]
    cmd_id = ssm.send_command(
        InstanceIds=instances, DocumentName="AWS-RunShellScript",
        Parameters={"commands": [
            f'for i in $(seq 1 {count}); do',
            f'  IP=$(curl -s --connect-timeout 3 -m 5 https://{ALLOWED_DOMAIN} 2>/dev/null | tr -d "[:space:]")',
            '  echo "RESULT ${IP:-FAIL}"',
            'done',
            f'CODE=$(curl -s -o /dev/null -w "%{{http_code}}" --connect-timeout 3 -m 5 {BLOCKED_URL})',
            'echo "BLOCKED_CHECK $CODE"',
        ]})["Command"]["CommandId"]
    print(f"  Command: {cmd_id} — waiting for results...")

    labels = {ip: meta["label"] for ip, meta in r.get("eips", {}).items()}
    labels.update({ip: meta["label"] for ip, meta in r.get("zonal_eips", {}).items()})
    for iid in instances:
        while True:
            time.sleep(10)
            try:
                inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
            except ClientError:
                continue
            if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
                break
        tally = {}
        blocked = "?"
        for line in inv["StandardOutputContent"].splitlines():
            if line.startswith("RESULT "):
                value = line.split(" ", 1)[1]
                key = "FAIL" if value == "FAIL" else labels.get(value, f"other ({value})")
                tally[key] = tally.get(key, 0) + 1
            elif line.startswith("BLOCKED_CHECK "):
                blocked = line.split(" ", 1)[1]
        name = "EC2 AZ1" if iid == r["ec2_az1"] else "EC2 AZ2"
        ok = sum(v for k, v in tally.items() if k != "FAIL")
        print(f"\n  [{name} {iid}] {inv['Status']} — {ok}/{count} succeeded")
        for key, value in sorted(tally.items()):
            print(f"    {key:<24} {value}")
        print(f"    {BLOCKED_URL} -> HTTP {blocked} (000 = dropped by firewall, expected)")


# ============================================================
# OPTION A — zonal NAT gateways, per-AZ return routes
# ============================================================
def fix_zonal():
    r = load_resources()
    print("=" * 60)
    print("OPTION A — replace Regional NAT with zonal NAT per AZ")
    print("=" * 60)
    r.setdefault("zonal_eips", {})
    for az in AZS:
        s = short(az)
        if f"nat_zonal_{s}" in r:
            continue
        r[f"pub_sub_{s}"] = ec2.create_subnet(VpcId=r["egress_vpc_id"], CidrBlock=EGRESS_PUBLIC[az], AvailabilityZone=az,
                                              TagSpecifications=spec("subnet", f"public-subnet-{s}"))["Subnet"]["SubnetId"]
        eip = ec2.allocate_address(Domain="vpc", TagSpecifications=spec("elastic-ip", f"nat-zonal-{s}"))
        r["zonal_eips"][eip["PublicIp"]] = {"allocation_id": eip["AllocationId"], "label": f"Zonal NAT {s.upper()}"}
        save_resources(r)
        r[f"nat_zonal_{s}"] = ec2.create_nat_gateway(SubnetId=r[f"pub_sub_{s}"], AllocationId=eip["AllocationId"],
                                                     TagSpecifications=spec("natgateway", f"nat-zonal-{s}"))["NatGateway"]["NatGatewayId"]
        save_resources(r)
    for az in AZS:
        wait_nat(r[f"nat_zonal_{short(az)}"])

    for az in AZS:
        s = short(az)
        # NAT subnet (AZ x): internet via IGW, spoke return via the firewall endpoint in the same AZ
        if f"rt_pub_{s}" not in r:
            r[f"rt_pub_{s}"] = ec2.create_route_table(VpcId=r["egress_vpc_id"], TagSpecifications=spec("route-table", f"rt-public-{s}"))["RouteTable"]["RouteTableId"]
            ec2.associate_route_table(RouteTableId=r[f"rt_pub_{s}"], SubnetId=r[f"pub_sub_{s}"])
        put_route(r[f"rt_pub_{s}"], "0.0.0.0/0", GatewayId=r["igw_id"])
        put_route(r[f"rt_pub_{s}"], SPOKE_VPC_CIDR, VpcEndpointId=r[f"fw_endpoint_{s}"])
        # Path back into the VPC-endpoint firewall (in case fix-tgw-fw ran before)
        put_route(r[f"rt_tgw_{s}"], "0.0.0.0/0", VpcEndpointId=r[f"fw_endpoint_{s}"])
        put_route(r[f"rt_fw_{s}"], "0.0.0.0/0", NatGatewayId=r[f"nat_zonal_{s}"])
        print(f"  {s.upper()}: TGW subnet -> {r[f'fw_endpoint_{s}']} -> {r[f'nat_zonal_{s}']} -> IGW; return 10.10.0.0/16 -> {r[f'fw_endpoint_{s}']}")
    save_resources(r)
    print("\nAppliance mode can stay ON. Run: python deploy_lab.py test")


# ============================================================
# OPTION B — TGW-attached firewall, Regional NAT kept
# ============================================================
def fix_tgw_fw():
    r = load_resources()
    print("=" * 60)
    print("OPTION B — Network Firewall with native TGW attachment (keeps Regional NAT)")
    print("=" * 60)

    if "tgw_fw_arn" not in r:
        print("\n  Creating TGW-attached firewall (~5 min)...")
        r["tgw_fw_arn"] = nfw.create_firewall(
            FirewallName=f"{PREFIX}-tgw-fw", FirewallPolicyArn=r["policy_arn"], TransitGatewayId=r["tgw_id"],
            AvailabilityZoneMappings=[{"AvailabilityZone": az} for az in AZS], DeleteProtection=False,
            Tags=[{"Key": "Name", "Value": f"{PREFIX}-tgw-fw"}, LAB_TAG])["Firewall"]["FirewallArn"]
        save_resources(r)

    while True:
        sync = nfw.describe_firewall(FirewallArn=r["tgw_fw_arn"])["FirewallStatus"].get("TransitGatewayAttachmentSyncState", {})
        state = sync.get("TransitGatewayAttachmentStatus")
        if sync.get("AttachmentId"):
            r["tgw_fw_att_id"] = sync["AttachmentId"]
            save_resources(r)
        if state == "PENDING_ACCEPTANCE":
            nfw.accept_network_firewall_transit_gateway_attachment(TransitGatewayAttachmentId=r["tgw_fw_att_id"])
        elif state == "READY":
            break
        elif state in ("FAILED", "ERROR", "REJECTED"):
            raise Exception(f"TGW firewall attachment {state}: {sync.get('StatusMessage')}")
        print(f"    TGW firewall attachment: {state}...")
        time.sleep(20)
    wait_firewall(r["tgw_fw_arn"])
    wait_tgw_attachment(r["tgw_fw_att_id"])
    enable_logging(r["tgw_fw_arn"], "tgw-fw")
    print(f"  Firewall attachment: {r['tgw_fw_att_id']} (appliance mode always on)")

    # TGW route tables: spoke -> fw attachment -> egress; egress return -> fw attachment
    if "tgw_rt_fw" not in r:
        r["tgw_rt_fw"] = ec2.create_transit_gateway_route_table(
            TransitGatewayId=r["tgw_id"], TagSpecifications=spec("transit-gateway-route-table", "tgw-rt-firewall"))["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"]
        save_resources(r)
        wait_tgw_rt(r["tgw_rt_fw"])
        ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=r["tgw_rt_fw"], TransitGatewayAttachmentId=r["tgw_fw_att_id"])
        ec2.enable_transit_gateway_route_table_propagation(TransitGatewayRouteTableId=r["tgw_rt_fw"], TransitGatewayAttachmentId=r["spoke_att_id"])
    put_tgw_route(r["tgw_rt_fw"], "0.0.0.0/0", r["egress_att_id"])
    put_tgw_route(r["tgw_rt_spoke"], "0.0.0.0/0", r["tgw_fw_att_id"])
    try:
        ec2.disable_transit_gateway_route_table_propagation(TransitGatewayRouteTableId=r["tgw_rt_egress"], TransitGatewayAttachmentId=r["spoke_att_id"])
        time.sleep(10)
    except ClientError:
        pass
    put_tgw_route(r["tgw_rt_egress"], SPOKE_VPC_CIDR, r["tgw_fw_att_id"])

    # Egress VPC: bypass the VPC-endpoint firewall, TGW subnets go straight to the RNAT, return to TGW
    for az in AZS:
        put_route(r[f"rt_tgw_{short(az)}"], "0.0.0.0/0", NatGatewayId=r["rnat_id"])
    set_rnat_routes(r, "tgw")
    save_resources(r)
    print("  TGW RTs: spoke 0/0 -> fw att | fw RT 0/0 -> egress att | egress RT 10.10.0.0/16 -> fw att")
    print("  Egress VPC: TGW subnets 0/0 -> RNAT | RNAT RT 10.10.0.0/16 -> TGW")
    print("\nRun: python deploy_lab.py test")


# ============================================================
# STATUS
# ============================================================
def status():
    r = load_resources()
    if not r:
        print("No resources.json found. Run 'deploy' first.")
        return
    print("=" * 60)
    print("NET-017 LAB STATUS")
    print("=" * 60)
    att = ec2.describe_transit_gateway_vpc_attachments(TransitGatewayAttachmentIds=[r["egress_att_id"]])["TransitGatewayVpcAttachments"][0]
    print(f"\n  Egress attachment appliance mode: {att['Options']['ApplianceModeSupport']}")
    rt = ec2.describe_route_tables(RouteTableIds=[r["rnat_rt_id"]])["RouteTables"][0]
    print(f"  RNAT route table {r['rnat_rt_id']}:")
    for route in rt["Routes"]:
        target = next((route[k] for k in ("VpcEndpointId", "GatewayId", "TransitGatewayId", "NatGatewayId") if k in route), "?")
        print(f"    {route.get('DestinationCidrBlock', '-'):<16} -> {target}")
    for az in AZS:
        s = short(az)
        fw_rt = ec2.describe_route_tables(RouteTableIds=[r[f"rt_fw_{s}"]])["RouteTables"][0]
        nat = next((x.get("NatGatewayId") for x in fw_rt["Routes"] if x.get("DestinationCidrBlock") == "0.0.0.0/0"), "?")
        print(f"  Firewall subnet {s.upper()} 0/0 -> {nat}")
    rg = nfw.describe_rule_group(RuleGroupArn=r["rg_arn"])["RuleGroup"]["RulesSource"]["RulesSourceList"]["GeneratedRulesType"]
    print(f"  Domain rule group: {rg}")
    print(f"  Option A applied: {'nat_zonal_az1' in r}  |  Option B applied: {'tgw_fw_att_id' in r}")


# ============================================================
# TEARDOWN
# ============================================================
def delete_firewall(fw_arn):
    try:
        nfw.update_logging_configuration(FirewallArn=fw_arn, LoggingConfiguration={"LogDestinationConfigs": []})
    except ClientError:
        pass
    try:
        nfw.delete_firewall(FirewallArn=fw_arn)
        print("    Waiting for firewall deletion (~5 min)...")
        while True:
            nfw.describe_firewall(FirewallArn=fw_arn)
            time.sleep(15)
    except ClientError:
        pass


def teardown():
    r = load_resources()
    if not r:
        print("No resources.json found.")
        return
    print("=" * 60)
    print("NET-017 LAB TEARDOWN")
    print("=" * 60)

    print("\n  Terminating EC2 instances...")
    ids = [r[k] for k in ("ec2_az1", "ec2_az2") if k in r]
    if ids:
        try:
            ec2.terminate_instances(InstanceIds=ids)
            ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
        except ClientError as e:
            print(f"    {e}")

    print("  Deleting SSM endpoints...")
    if "spoke_vpc_id" in r:
        eps = ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [r["spoke_vpc_id"]]}])["VpcEndpoints"]
        if eps:
            ec2.delete_vpc_endpoints(VpcEndpointIds=[e["VpcEndpointId"] for e in eps])
            while ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [r["spoke_vpc_id"]]},
                                                      {"Name": "vpc-endpoint-state", "Values": ["deleting", "available"]}])["VpcEndpoints"]:
                time.sleep(10)

    print("  Deleting firewalls...")
    for key in ("tgw_fw_arn", "fw_arn"):
        if key in r:
            delete_firewall(r[key])
    for fn, key in ((nfw.delete_firewall_policy, "policy_arn"), (nfw.delete_rule_group, "rg_arn")):
        try:
            if key == "policy_arn":
                fn(FirewallPolicyArn=r[key])
                time.sleep(15)
            else:
                fn(RuleGroupArn=r[key])
        except (ClientError, KeyError) as e:
            print(f"    {key}: {e}")

    print("  Deleting NAT gateways...")
    nats = [r[k] for k in ("rnat_id", "nat_zonal_az1", "nat_zonal_az2") if k in r]
    for nat in nats:
        try:
            ec2.delete_nat_gateway(NatGatewayId=nat)
        except ClientError as e:
            print(f"    {nat}: {e}")
    for nat in nats:
        while ec2.describe_nat_gateways(NatGatewayIds=[nat])["NatGateways"][0]["State"] != "deleted":
            time.sleep(10)

    print("  Releasing Elastic IPs...")
    for meta in list(r.get("eips", {}).values()) + list(r.get("zonal_eips", {}).values()):
        try:
            ec2.release_address(AllocationId=meta["allocation_id"])
        except ClientError as e:
            print(f"    {meta['allocation_id']}: {e}")

    print("  Deleting TGW attachments...")
    for key in ("egress_att_id", "spoke_att_id"):
        if key in r:
            try:
                ec2.delete_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=r[key])
            except ClientError as e:
                print(f"    {key}: {e}")
    atts = [r[k] for k in ("egress_att_id", "spoke_att_id", "tgw_fw_att_id") if k in r]
    while atts:
        try:
            found = ec2.describe_transit_gateway_attachments(TransitGatewayAttachmentIds=atts)["TransitGatewayAttachments"]
        except ClientError:
            break
        if all(a["State"] == "deleted" for a in found):
            break
        time.sleep(15)

    print("  Deleting TGW route tables and TGW...")
    for key in ("tgw_rt_spoke", "tgw_rt_egress", "tgw_rt_fw"):
        if key in r:
            try:
                ec2.delete_transit_gateway_route_table(TransitGatewayRouteTableId=r[key])
            except ClientError as e:
                print(f"    {key}: {e}")
    if "tgw_id" in r:
        try:
            ec2.delete_transit_gateway(TransitGatewayId=r["tgw_id"])
        except ClientError as e:
            print(f"    {e}")

    print("  Deleting security group...")
    try:
        ec2.delete_security_group(GroupId=r["sg_id"])
    except (ClientError, KeyError) as e:
        print(f"    {e}")

    print("  Deleting route tables...")
    for key in ("rt_tgw_az1", "rt_tgw_az2", "rt_fw_az1", "rt_fw_az2", "rt_pub_az1", "rt_pub_az2", "rt_spoke"):
        if key not in r:
            continue
        try:
            for a in ec2.describe_route_tables(RouteTableIds=[r[key]])["RouteTables"][0]["Associations"]:
                if not a.get("Main"):
                    ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
            ec2.delete_route_table(RouteTableId=r[key])
        except ClientError as e:
            print(f"    {key}: {e}")

    print("  Deleting IGW, subnets, VPCs...")
    if "igw_id" in r:
        try:
            ec2.detach_internet_gateway(InternetGatewayId=r["igw_id"], VpcId=r["egress_vpc_id"])
            ec2.delete_internet_gateway(InternetGatewayId=r["igw_id"])
        except ClientError as e:
            print(f"    {e}")
    for key in [k for k in r if k.endswith(("_sub_az1", "_sub_az2"))]:
        try:
            ec2.delete_subnet(SubnetId=r[key])
        except ClientError as e:
            print(f"    {key}: {e}")
    for key in ("egress_vpc_id", "spoke_vpc_id"):
        if key in r:
            try:
                ec2.delete_vpc(VpcId=r[key])
            except ClientError as e:
                print(f"    {key}: {e}")

    print("  Deleting IAM resources...")
    try:
        iam.remove_role_from_instance_profile(InstanceProfileName=PROFILE_NAME, RoleName=ROLE_NAME)
        iam.delete_instance_profile(InstanceProfileName=PROFILE_NAME)
        iam.detach_role_policy(RoleName=ROLE_NAME, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        iam.delete_role(RoleName=ROLE_NAME)
    except ClientError as e:
        print(f"    {e}")

    print("  Deleting log groups...")
    for name in ("vpc-fw", "tgw-fw"):
        for log_type in ("flow", "alert"):
            try:
                logs.delete_log_group(logGroupName=f"/aws/network-firewall/{PREFIX}/{name}/{log_type}")
            except ClientError:
                pass

    print("\n" + "=" * 60)
    print("TEARDOWN COMPLETE — verify in the console, then delete resources.json")
    print("=" * 60)


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1].lower()
    arg = sys.argv[2].lower() if len(sys.argv) > 2 else None
    if action == "deploy":
        deploy()
    elif action == "test":
        test(int(arg) if arg else 20)
    elif action == "routes" and arg:
        routes(arg)
    elif action == "appliance" and arg in ("on", "off"):
        appliance(arg)
    elif action == "rules" and arg in ("allow", "alert"):
        rules(arg)
    elif action == "fix-zonal":
        fix_zonal()
    elif action == "fix-tgw-fw":
        fix_tgw_fw()
    elif action == "status":
        status()
    elif action == "teardown":
        teardown()
    else:
        print(__doc__)
        sys.exit(1)
