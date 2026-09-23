"""
NET-005 Lab: Regional NAT Gateway + Network Firewall + TGW Centralized Inspection
Region: us-west-2 | Profile: lab

Deploys full centralized egress architecture with Regional NAT Gateway and
Network Firewall to validate symmetric routing with per-AZ CIDR routes.

Usage:
  python deploy_lab.py deploy     # Create all infrastructure
  python deploy_lab.py status     # Show current state of all lab resources
  python deploy_lab.py test       # Run connectivity tests via SSM
  python deploy_lab.py teardown   # Delete everything

Prerequisites:
  pip install boto3
"""

import sys
import time
import json
import boto3
from botocore.exceptions import ClientError

PROFILE = "lab"
REGION = "us-west-2"
PREFIX = "net005-lab"
AZ1 = "us-west-2a"
AZ2 = "us-west-2b"

INSPECTION_VPC_CIDR = "10.100.0.0/16"
INSPECTION_FW_AZ1 = "10.100.1.0/24"
INSPECTION_FW_AZ2 = "10.100.2.0/24"
INSPECTION_TGW_AZ1 = "10.100.10.0/24"
INSPECTION_TGW_AZ2 = "10.100.20.0/24"

SPOKE_VPC_CIDR = "10.200.0.0/16"
SPOKE_PRIVATE_AZ1 = "10.200.1.0/24"
SPOKE_PRIVATE_AZ2 = "10.200.2.0/24"

LAB_TAG = {"Key": "Project", "Value": "NET-005"}
RESOURCES_FILE = "resources.json"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ec2 = session.client("ec2")
nfw = session.client("network-firewall")
iam = session.client("iam")
ssm = session.client("ssm")
logs = session.client("logs")


def tag(name):
    return [{"Key": "Name", "Value": f"{PREFIX}-{name}"}, LAB_TAG]


def save_resources(resources):
    with open(RESOURCES_FILE, "w") as f:
        json.dump(resources, f, indent=2)


def load_resources():
    try:
        with open(RESOURCES_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def wait_tgw(tgw_id):
    while True:
        state = ec2.describe_transit_gateways(TransitGatewayIds=[tgw_id])["TransitGateways"][0]["State"]
        if state == "available":
            return
        time.sleep(15)


def wait_tgw_attachment(att_id):
    while True:
        state = ec2.describe_transit_gateway_vpc_attachments(
            TransitGatewayAttachmentIds=[att_id]
        )["TransitGatewayVpcAttachments"][0]["State"]
        if state == "available":
            return
        if state == "failed":
            raise Exception(f"Attachment {att_id} failed")
        time.sleep(10)


def wait_tgw_rt(rt_id):
    while True:
        state = ec2.describe_transit_gateway_route_tables(
            TransitGatewayRouteTableIds=[rt_id]
        )["TransitGatewayRouteTables"][0]["State"]
        if state == "available":
            return
        time.sleep(5)


def wait_firewall(fw_arn):
    while True:
        resp = nfw.describe_firewall(FirewallArn=fw_arn)
        status = resp["FirewallStatus"]["Status"]
        if status == "READY":
            return resp
        print(f"    Firewall: {status}...")
        time.sleep(20)


def wait_nat(nat_id):
    while True:
        resp = ec2.describe_nat_gateways(NatGatewayIds=[nat_id])
        state = resp["NatGateways"][0]["State"]
        if state == "available":
            return resp["NatGateways"][0]
        if state == "failed":
            raise Exception(f"NAT GW failed: {resp['NatGateways'][0].get('FailureMessage')}")
        print(f"    RNAT: {state}...")
        time.sleep(15)


# ============================================================
# DEPLOY
# ============================================================
def deploy():
    r = {}
    print("=" * 60)
    print("NET-005 LAB DEPLOYMENT")
    print("=" * 60)

    # Step 1: VPCs
    print("\n[1/10] Creating VPCs...")
    vpc1 = ec2.create_vpc(CidrBlock=INSPECTION_VPC_CIDR,
                          TagSpecifications=[{"ResourceType": "vpc", "Tags": tag("inspection-vpc")}])
    r["inspection_vpc_id"] = vpc1["Vpc"]["VpcId"]
    ec2.modify_vpc_attribute(VpcId=r["inspection_vpc_id"], EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=r["inspection_vpc_id"], EnableDnsHostnames={"Value": True})

    vpc2 = ec2.create_vpc(CidrBlock=SPOKE_VPC_CIDR,
                          TagSpecifications=[{"ResourceType": "vpc", "Tags": tag("spoke-vpc")}])
    r["spoke_vpc_id"] = vpc2["Vpc"]["VpcId"]
    ec2.modify_vpc_attribute(VpcId=r["spoke_vpc_id"], EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=r["spoke_vpc_id"], EnableDnsHostnames={"Value": True})
    print(f"  Inspection VPC: {r['inspection_vpc_id']}")
    print(f"  Spoke VPC:      {r['spoke_vpc_id']}")

    # Step 2: Subnets
    print("\n[2/10] Creating Subnets...")
    r["fw_sub_az1"] = ec2.create_subnet(VpcId=r["inspection_vpc_id"], CidrBlock=INSPECTION_FW_AZ1,
                                         AvailabilityZone=AZ1, TagSpecifications=[{"ResourceType": "subnet", "Tags": tag("fw-subnet-az1")}])["Subnet"]["SubnetId"]
    r["fw_sub_az2"] = ec2.create_subnet(VpcId=r["inspection_vpc_id"], CidrBlock=INSPECTION_FW_AZ2,
                                         AvailabilityZone=AZ2, TagSpecifications=[{"ResourceType": "subnet", "Tags": tag("fw-subnet-az2")}])["Subnet"]["SubnetId"]
    r["tgw_sub_az1"] = ec2.create_subnet(VpcId=r["inspection_vpc_id"], CidrBlock=INSPECTION_TGW_AZ1,
                                          AvailabilityZone=AZ1, TagSpecifications=[{"ResourceType": "subnet", "Tags": tag("tgw-subnet-az1")}])["Subnet"]["SubnetId"]
    r["tgw_sub_az2"] = ec2.create_subnet(VpcId=r["inspection_vpc_id"], CidrBlock=INSPECTION_TGW_AZ2,
                                          AvailabilityZone=AZ2, TagSpecifications=[{"ResourceType": "subnet", "Tags": tag("tgw-subnet-az2")}])["Subnet"]["SubnetId"]
    r["spoke_sub_az1"] = ec2.create_subnet(VpcId=r["spoke_vpc_id"], CidrBlock=SPOKE_PRIVATE_AZ1,
                                            AvailabilityZone=AZ1, TagSpecifications=[{"ResourceType": "subnet", "Tags": tag("spoke-private-az1")}])["Subnet"]["SubnetId"]
    r["spoke_sub_az2"] = ec2.create_subnet(VpcId=r["spoke_vpc_id"], CidrBlock=SPOKE_PRIVATE_AZ2,
                                            AvailabilityZone=AZ2, TagSpecifications=[{"ResourceType": "subnet", "Tags": tag("spoke-private-az2")}])["Subnet"]["SubnetId"]
    print(f"  FW:    {r['fw_sub_az1']}, {r['fw_sub_az2']}")
    print(f"  TGW:   {r['tgw_sub_az1']}, {r['tgw_sub_az2']}")
    print(f"  Spoke: {r['spoke_sub_az1']}, {r['spoke_sub_az2']}")

    # Step 3: IGW
    print("\n[3/10] Creating Internet Gateway...")
    igw = ec2.create_internet_gateway(TagSpecifications=[{"ResourceType": "internet-gateway", "Tags": tag("igw")}])
    r["igw_id"] = igw["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=r["igw_id"], VpcId=r["inspection_vpc_id"])
    print(f"  IGW: {r['igw_id']}")

    # Step 4: TGW
    print("\n[4/10] Creating Transit Gateway (~2 min)...")
    tgw = ec2.create_transit_gateway(
        Description="NET-005 lab TGW",
        Options={"DefaultRouteTableAssociation": "disable", "DefaultRouteTablePropagation": "disable",
                 "AutoAcceptSharedAttachments": "enable"},
        TagSpecifications=[{"ResourceType": "transit-gateway", "Tags": tag("tgw")}]
    )
    r["tgw_id"] = tgw["TransitGateway"]["TransitGatewayId"]
    print(f"  TGW: {r['tgw_id']}")
    wait_tgw(r["tgw_id"])
    print("  TGW available.")

    # Step 5: TGW Attachments
    print("\n[5/10] Creating TGW Attachments...")
    att1 = ec2.create_transit_gateway_vpc_attachment(
        TransitGatewayId=r["tgw_id"], VpcId=r["inspection_vpc_id"],
        SubnetIds=[r["tgw_sub_az1"], r["tgw_sub_az2"]],
        Options={"ApplianceModeSupport": "disable"},
        TagSpecifications=[{"ResourceType": "transit-gateway-attachment", "Tags": tag("tgw-att-inspection")}]
    )
    r["inspection_att_id"] = att1["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"]

    att2 = ec2.create_transit_gateway_vpc_attachment(
        TransitGatewayId=r["tgw_id"], VpcId=r["spoke_vpc_id"],
        SubnetIds=[r["spoke_sub_az1"], r["spoke_sub_az2"]],
        TagSpecifications=[{"ResourceType": "transit-gateway-attachment", "Tags": tag("tgw-att-spoke")}]
    )
    r["spoke_att_id"] = att2["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"]
    print(f"  Inspection: {r['inspection_att_id']}")
    print(f"  Spoke:      {r['spoke_att_id']}")

    time.sleep(20)
    wait_tgw_attachment(r["inspection_att_id"])
    wait_tgw_attachment(r["spoke_att_id"])
    print("  Attachments available.")

    # Step 6: TGW Route Tables
    print("\n[6/10] Creating TGW Route Tables...")
    r["tgw_rt_spoke"] = ec2.create_transit_gateway_route_table(
        TransitGatewayId=r["tgw_id"],
        TagSpecifications=[{"ResourceType": "transit-gateway-route-table", "Tags": tag("tgw-rt-spoke")}]
    )["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"]

    r["tgw_rt_inspection"] = ec2.create_transit_gateway_route_table(
        TransitGatewayId=r["tgw_id"],
        TagSpecifications=[{"ResourceType": "transit-gateway-route-table", "Tags": tag("tgw-rt-inspection")}]
    )["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"]

    wait_tgw_rt(r["tgw_rt_spoke"])
    wait_tgw_rt(r["tgw_rt_inspection"])

    ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=r["tgw_rt_spoke"], TransitGatewayAttachmentId=r["spoke_att_id"])
    ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=r["tgw_rt_inspection"], TransitGatewayAttachmentId=r["inspection_att_id"])
    ec2.create_transit_gateway_route(TransitGatewayRouteTableId=r["tgw_rt_spoke"], DestinationCidrBlock="0.0.0.0/0", TransitGatewayAttachmentId=r["inspection_att_id"])
    ec2.enable_transit_gateway_route_table_propagation(TransitGatewayRouteTableId=r["tgw_rt_inspection"], TransitGatewayAttachmentId=r["spoke_att_id"])
    print(f"  Spoke RT:      {r['tgw_rt_spoke']} (0/0 → inspection)")
    print(f"  Inspection RT: {r['tgw_rt_inspection']} (propagated spoke)")

    # Step 7: Network Firewall
    print("\n[7/10] Creating Network Firewall (~5 min)...")
    rg = nfw.create_rule_group(
        RuleGroupName=f"{PREFIX}-allow-all", Type="STATEFUL", Capacity=100,
        RuleGroup={"RulesSource": {"StatefulRules": [{"Action": "PASS", "Header": {"Protocol": "IP", "Source": "ANY", "SourcePort": "ANY", "Direction": "ANY", "Destination": "ANY", "DestinationPort": "ANY"}, "RuleOptions": [{"Keyword": "sid", "Settings": ["1"]}]}]}},
        Tags=[{"Key": "Name", "Value": f"{PREFIX}-allow-all-rg"}]
    )
    r["rg_arn"] = rg["RuleGroupResponse"]["RuleGroupArn"]

    pol = nfw.create_firewall_policy(
        FirewallPolicyName=f"{PREFIX}-policy",
        FirewallPolicy={"StatelessDefaultActions": ["aws:forward_to_sfe"], "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"], "StatefulRuleGroupReferences": [{"ResourceArn": r["rg_arn"]}]},
        Tags=[{"Key": "Name", "Value": f"{PREFIX}-policy"}]
    )
    r["policy_arn"] = pol["FirewallPolicyResponse"]["FirewallPolicyArn"]

    fw = nfw.create_firewall(
        FirewallName=f"{PREFIX}-firewall", FirewallPolicyArn=r["policy_arn"],
        VpcId=r["inspection_vpc_id"],
        SubnetMappings=[{"SubnetId": r["fw_sub_az1"]}, {"SubnetId": r["fw_sub_az2"]}],
        DeleteProtection=False,
        Tags=[{"Key": "Name", "Value": f"{PREFIX}-firewall"}]
    )
    r["fw_arn"] = fw["Firewall"]["FirewallArn"]
    print(f"  Firewall: {r['fw_arn']}")

    fw_ready = wait_firewall(r["fw_arn"])
    for az, state in fw_ready["FirewallStatus"]["SyncStates"].items():
        ep = state["Attachment"]["EndpointId"]
        if az == AZ1:
            r["fw_endpoint_az1"] = ep
        elif az == AZ2:
            r["fw_endpoint_az2"] = ep
    print(f"  Endpoint AZ1: {r['fw_endpoint_az1']}")
    print(f"  Endpoint AZ2: {r['fw_endpoint_az2']}")

    # Step 8: Regional NAT Gateway
    print("\n[8/10] Creating Regional NAT Gateway...")
    rnat = ec2.create_nat_gateway(
        VpcId=r["inspection_vpc_id"], ConnectivityType="public",
        TagSpecifications=[{"ResourceType": "natgateway", "Tags": tag("rnat")}],
        AvailabilityMode="regional"
    )
    r["rnat_id"] = rnat["NatGateway"]["NatGatewayId"]
    print(f"  RNAT: {r['rnat_id']}")

    rnat_details = wait_nat(r["rnat_id"])
    r["rnat_rt_id"] = rnat_details.get("RouteTableId", "")
    print(f"  RNAT RT: {r['rnat_rt_id']}")

    # Step 9: Route Tables
    print("\n[9/10] Configuring Route Tables...")

    # TGW Subnet AZ1
    r["rt_tgw_az1"] = ec2.create_route_table(VpcId=r["inspection_vpc_id"],
                                              TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("rt-tgw-az1")}])["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=r["rt_tgw_az1"], DestinationCidrBlock="0.0.0.0/0", VpcEndpointId=r["fw_endpoint_az1"])
    ec2.associate_route_table(RouteTableId=r["rt_tgw_az1"], SubnetId=r["tgw_sub_az1"])

    # TGW Subnet AZ2
    r["rt_tgw_az2"] = ec2.create_route_table(VpcId=r["inspection_vpc_id"],
                                              TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("rt-tgw-az2")}])["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=r["rt_tgw_az2"], DestinationCidrBlock="0.0.0.0/0", VpcEndpointId=r["fw_endpoint_az2"])
    ec2.associate_route_table(RouteTableId=r["rt_tgw_az2"], SubnetId=r["tgw_sub_az2"])

    # FW Subnet
    r["rt_fw"] = ec2.create_route_table(VpcId=r["inspection_vpc_id"],
                                         TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("rt-fw")}])["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=r["rt_fw"], DestinationCidrBlock="0.0.0.0/0", NatGatewayId=r["rnat_id"])
    ec2.create_route(RouteTableId=r["rt_fw"], DestinationCidrBlock=SPOKE_VPC_CIDR, TransitGatewayId=r["tgw_id"])
    ec2.associate_route_table(RouteTableId=r["rt_fw"], SubnetId=r["fw_sub_az1"])
    ec2.associate_route_table(RouteTableId=r["rt_fw"], SubnetId=r["fw_sub_az2"])

    # RNAT RT: per-AZ routes for spoke subnets
    if r["rnat_rt_id"]:
        ec2.create_route(RouteTableId=r["rnat_rt_id"], DestinationCidrBlock=SPOKE_PRIVATE_AZ1, VpcEndpointId=r["fw_endpoint_az1"])
        ec2.create_route(RouteTableId=r["rnat_rt_id"], DestinationCidrBlock=SPOKE_PRIVATE_AZ2, VpcEndpointId=r["fw_endpoint_az2"])
        ec2.create_route(RouteTableId=r["rnat_rt_id"], DestinationCidrBlock=INSPECTION_TGW_AZ1, VpcEndpointId=r["fw_endpoint_az1"])
        ec2.create_route(RouteTableId=r["rnat_rt_id"], DestinationCidrBlock=INSPECTION_TGW_AZ2, VpcEndpointId=r["fw_endpoint_az2"])
        print(f"  RNAT RT: spoke + TGW subnet routes added per-AZ")

    # Spoke RT
    r["rt_spoke"] = ec2.create_route_table(VpcId=r["spoke_vpc_id"],
                                            TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("rt-spoke")}])["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=r["rt_spoke"], DestinationCidrBlock="0.0.0.0/0", TransitGatewayId=r["tgw_id"])
    ec2.associate_route_table(RouteTableId=r["rt_spoke"], SubnetId=r["spoke_sub_az1"])
    ec2.associate_route_table(RouteTableId=r["rt_spoke"], SubnetId=r["spoke_sub_az2"])
    print("  All route tables configured.")

    # Step 10: EC2 + SSM
    print("\n[10/10] Launching EC2 instances + SSM endpoints...")

    # SSM instance profile
    try:
        iam.create_role(RoleName="NET005-SSM-Role", AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}))
        iam.attach_role_policy(RoleName="NET005-SSM-Role", PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        iam.create_instance_profile(InstanceProfileName="NET005-SSM-Profile")
        iam.add_role_to_instance_profile(InstanceProfileName="NET005-SSM-Profile", RoleName="NET005-SSM-Role")
        time.sleep(10)
    except ClientError:
        pass
    r["instance_profile"] = "NET005-SSM-Profile"

    # Security group
    sg = ec2.create_security_group(GroupName=f"{PREFIX}-spoke-sg", Description="NET-005 spoke",
                                    VpcId=r["spoke_vpc_id"], TagSpecifications=[{"ResourceType": "security-group", "Tags": tag("spoke-sg")}])
    r["sg_id"] = sg["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=r["sg_id"],
                                          IpPermissions=[{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": SPOKE_VPC_CIDR}]}])

    # SSM VPC Endpoints
    for svc in ["ssm", "ssmmessages", "ec2messages"]:
        try:
            ec2.create_vpc_endpoint(VpcId=r["spoke_vpc_id"], ServiceName=f"com.amazonaws.{REGION}.{svc}",
                                    VpcEndpointType="Interface", SubnetIds=[r["spoke_sub_az1"], r["spoke_sub_az2"]],
                                    SecurityGroupIds=[r["sg_id"]], PrivateDnsEnabled=True,
                                    TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": tag(f"{svc}-endpoint")}])
        except ClientError as e:
            print(f"    {svc}: {e}")

    # AMI
    ami = sorted(ec2.describe_images(Owners=["amazon"], Filters=[{"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]}, {"Name": "state", "Values": ["available"]}])["Images"],
                 key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]

    # Launch instances
    r["ec2_az1"] = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MaxCount=1, MinCount=1,
                                      SubnetId=r["spoke_sub_az1"], SecurityGroupIds=[r["sg_id"]],
                                      IamInstanceProfile={"Name": r["instance_profile"]},
                                      TagSpecifications=[{"ResourceType": "instance", "Tags": tag("test-ec2-az1")}])["Instances"][0]["InstanceId"]
    r["ec2_az2"] = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MaxCount=1, MinCount=1,
                                      SubnetId=r["spoke_sub_az2"], SecurityGroupIds=[r["sg_id"]],
                                      IamInstanceProfile={"Name": r["instance_profile"]},
                                      TagSpecifications=[{"ResourceType": "instance", "Tags": tag("test-ec2-az2")}])["Instances"][0]["InstanceId"]
    print(f"  EC2 AZ1: {r['ec2_az1']}")
    print(f"  EC2 AZ2: {r['ec2_az2']}")

    # Enable NFW logging
    try:
        logs.create_log_group(logGroupName=f"/aws/network-firewall/{PREFIX}/flow")
        logs.create_log_group(logGroupName=f"/aws/network-firewall/{PREFIX}/alert")
    except ClientError:
        pass
    nfw.update_logging_configuration(FirewallArn=r["fw_arn"], LoggingConfiguration={"LogDestinationConfigs": [{"LogType": "FLOW", "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": f"/aws/network-firewall/{PREFIX}/flow"}}]})
    nfw.update_logging_configuration(FirewallArn=r["fw_arn"], LoggingConfiguration={"LogDestinationConfigs": [{"LogType": "FLOW", "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": f"/aws/network-firewall/{PREFIX}/flow"}}, {"LogType": "ALERT", "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": f"/aws/network-firewall/{PREFIX}/alert"}}]})
    print("  NFW logging enabled.")

    save_resources(r)
    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETE — resources saved to resources.json")
    print("=" * 60)
    print("\nWait ~2 min for SSM registration, then run: python deploy_lab.py test")


# ============================================================
# STATUS
# ============================================================
def status():
    r = load_resources()
    if not r:
        print("No resources.json found. Run 'deploy' first.")
        return

    print("=" * 60)
    print("NET-005 LAB STATUS")
    print("=" * 60)
    print(json.dumps(r, indent=2))

    # Check EC2 state
    instances = ec2.describe_instances(InstanceIds=[r["ec2_az1"], r["ec2_az2"]])
    for res in instances["Reservations"]:
        for inst in res["Instances"]:
            print(f"\n  {inst['InstanceId']}: {inst['State']['Name']} ({inst['Placement']['AvailabilityZone']}) - {inst.get('PrivateIpAddress', 'N/A')}")

    # Check SSM
    ssm_info = ssm.describe_instance_information()
    online = {i["InstanceId"] for i in ssm_info["InstanceInformationList"] if i["PingStatus"] == "Online"}
    print(f"\n  SSM Online: {online & {r['ec2_az1'], r['ec2_az2']}}")


# ============================================================
# TEST
# ============================================================
def test():
    r = load_resources()
    if not r:
        print("No resources.json found. Run 'deploy' first.")
        return

    print("=" * 60)
    print("NET-005 CONNECTIVITY TEST")
    print("=" * 60)

    cmd = ssm.send_command(
        InstanceIds=[r["ec2_az1"], r["ec2_az2"]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [
            'AZ=$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone)',
            'IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)',
            'echo "=== $AZ ($IP) ==="',
            'RESULT=$(curl -s -o /dev/null -w "%{http_code} %{time_total}s" --connect-timeout 5 https://www.google.com 2>&1)',
            'echo "  curl google.com: $RESULT"',
            'PUB=$(curl -s --connect-timeout 5 ifconfig.me 2>&1)',
            'echo "  Public IP (RNAT): $PUB"',
        ]}
    )
    cmd_id = cmd["Command"]["CommandId"]
    print(f"  Command: {cmd_id}")
    print("  Waiting for results...")

    time.sleep(20)
    for iid in [r["ec2_az1"], r["ec2_az2"]]:
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=iid)
            print(f"\n  [{iid}] {inv['Status']}:")
            print(f"  {inv['StandardOutputContent']}")
        except ClientError as e:
            print(f"\n  [{iid}] Error: {e}")


# ============================================================
# TEARDOWN
# ============================================================
def teardown():
    r = load_resources()
    if not r:
        print("No resources.json found.")
        return

    print("=" * 60)
    print("NET-005 LAB TEARDOWN")
    print("=" * 60)

    # EC2
    print("\n  Terminating EC2 instances...")
    try:
        ec2.terminate_instances(InstanceIds=[r.get("ec2_az1", ""), r.get("ec2_az2", "")])
        waiter = ec2.get_waiter("instance_terminated")
        waiter.wait(InstanceIds=[r["ec2_az1"], r["ec2_az2"]])
    except Exception as e:
        print(f"    {e}")

    # VPC Endpoints (SSM)
    print("  Deleting VPC Endpoints...")
    try:
        endpoints = ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [r["spoke_vpc_id"]]}])
        for ep in endpoints["VpcEndpoints"]:
            ec2.delete_vpc_endpoints(VpcEndpointIds=[ep["VpcEndpointId"]])
    except Exception as e:
        print(f"    {e}")

    # Security Group
    print("  Deleting Security Group...")
    try:
        ec2.delete_security_group(GroupId=r.get("sg_id", ""))
    except Exception as e:
        print(f"    {e}")

    # Network Firewall
    print("  Deleting Network Firewall...")
    try:
        nfw.update_logging_configuration(FirewallArn=r["fw_arn"], LoggingConfiguration={"LogDestinationConfigs": []})
        nfw.delete_firewall(FirewallArn=r["fw_arn"])
        print("    Waiting for firewall deletion (~5 min)...")
        while True:
            try:
                nfw.describe_firewall(FirewallArn=r["fw_arn"])
                time.sleep(15)
            except ClientError:
                break
    except Exception as e:
        print(f"    {e}")

    try:
        nfw.delete_firewall_policy(FirewallPolicyArn=r.get("policy_arn", ""))
        nfw.delete_rule_group(RuleGroupArn=r.get("rg_arn", ""))
    except Exception as e:
        print(f"    {e}")

    # NAT Gateway
    print("  Deleting Regional NAT Gateway...")
    try:
        ec2.delete_nat_gateway(NatGatewayId=r["rnat_id"])
        while True:
            state = ec2.describe_nat_gateways(NatGatewayIds=[r["rnat_id"]])["NatGateways"][0]["State"]
            if state == "deleted":
                break
            time.sleep(10)
    except Exception as e:
        print(f"    {e}")

    # TGW Attachments
    print("  Deleting TGW Attachments...")
    for att in [r.get("inspection_att_id"), r.get("spoke_att_id")]:
        try:
            ec2.delete_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=att)
        except Exception as e:
            print(f"    {e}")
    time.sleep(30)

    # TGW Route Tables
    print("  Deleting TGW Route Tables...")
    for rt in [r.get("tgw_rt_spoke"), r.get("tgw_rt_inspection")]:
        try:
            ec2.delete_transit_gateway_route_table(TransitGatewayRouteTableId=rt)
        except Exception as e:
            print(f"    {e}")

    # Transit Gateway
    print("  Deleting Transit Gateway...")
    try:
        ec2.delete_transit_gateway(TransitGatewayId=r["tgw_id"])
    except Exception as e:
        print(f"    {e}")

    # Route Tables
    print("  Deleting Route Tables...")
    for rt_key in ["rt_tgw_az1", "rt_tgw_az2", "rt_fw", "rt_spoke"]:
        try:
            rt_id = r.get(rt_key, "")
            assocs = ec2.describe_route_tables(RouteTableIds=[rt_id])["RouteTables"][0]["Associations"]
            for a in assocs:
                if not a.get("Main"):
                    ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
            ec2.delete_route_table(RouteTableId=rt_id)
        except Exception as e:
            print(f"    {rt_key}: {e}")

    # IGW
    print("  Deleting IGW...")
    try:
        ec2.detach_internet_gateway(InternetGatewayId=r["igw_id"], VpcId=r["inspection_vpc_id"])
        ec2.delete_internet_gateway(InternetGatewayId=r["igw_id"])
    except Exception as e:
        print(f"    {e}")

    # Subnets
    print("  Deleting Subnets...")
    for sub_key in ["fw_sub_az1", "fw_sub_az2", "tgw_sub_az1", "tgw_sub_az2", "spoke_sub_az1", "spoke_sub_az2"]:
        try:
            ec2.delete_subnet(SubnetId=r.get(sub_key, ""))
        except Exception as e:
            print(f"    {sub_key}: {e}")

    # VPCs
    print("  Deleting VPCs...")
    for vpc_key in ["inspection_vpc_id", "spoke_vpc_id"]:
        try:
            ec2.delete_vpc(VpcId=r.get(vpc_key, ""))
        except Exception as e:
            print(f"    {vpc_key}: {e}")

    # IAM
    print("  Deleting IAM resources...")
    try:
        iam.remove_role_from_instance_profile(InstanceProfileName="NET005-SSM-Profile", RoleName="NET005-SSM-Role")
        iam.delete_instance_profile(InstanceProfileName="NET005-SSM-Profile")
        iam.detach_role_policy(RoleName="NET005-SSM-Role", PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        iam.delete_role(RoleName="NET005-SSM-Role")
    except Exception as e:
        print(f"    {e}")

    # Log groups
    print("  Deleting log groups...")
    try:
        logs.delete_log_group(logGroupName=f"/aws/network-firewall/{PREFIX}/flow")
        logs.delete_log_group(logGroupName=f"/aws/network-firewall/{PREFIX}/alert")
    except Exception as e:
        print(f"    {e}")

    print("\n" + "=" * 60)
    print("TEARDOWN COMPLETE")
    print("=" * 60)


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python deploy_lab.py [deploy|status|test|teardown]")
        sys.exit(1)

    action = sys.argv[1].lower()
    if action == "deploy":
        deploy()
    elif action == "status":
        status()
    elif action == "test":
        test()
    elif action == "teardown":
        teardown()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)
