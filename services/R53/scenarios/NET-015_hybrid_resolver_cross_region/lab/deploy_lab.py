#!/usr/bin/env python3
"""NET-015 — Hybrid DNS Resolution: Cross-Region Resolver Forwarding Chain.

deploy   — Create all infrastructure (3 VPCs, 2 peerings, 3 resolver endpoints,
           forwarding rules, PHZs, query logging, EC2 instances)
status   — Show current state of all resources
test     — Run DNS queries from each VPC and validate results
teardown — Delete everything in dependency-safe order

Usage:
    python deploy_lab.py deploy   [--profile PROFILE]
    python deploy_lab.py status   [--profile PROFILE]
    python deploy_lab.py test     [--profile PROFILE]
    python deploy_lab.py teardown [--profile PROFILE]
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

# ── Network layout ──────────────────────────────────────────────────────────

REGION_A = "us-east-1"  # production + on-prem simulation
REGION_B = "us-west-2"  # DR

ONPREM_CIDR = "192.168.0.0/16"
ONPREM_SUB1 = "192.168.1.0/24"   # on-prem DNS server lives here

VPCA_CIDR = "10.0.0.0/16"
VPCA_SUB1 = "10.0.1.0/24"        # resolver endpoints AZ1
VPCA_SUB2 = "10.0.2.0/24"        # resolver endpoints AZ2
VPCA_SUB3 = "10.0.10.0/24"       # workload / test EC2

VPCB_CIDR = "10.1.0.0/16"
VPCB_SUB1 = "10.1.1.0/24"        # resolver endpoints AZ1
VPCB_SUB2 = "10.1.2.0/24"        # resolver endpoints AZ2
VPCB_SUB3 = "10.1.10.0/24"       # workload / test EC2

DNSMASQ_CONF = Path(__file__).parent / "dnsmasq.conf"

# AMI: Amazon Linux 2023 (resolved at deploy time per region)
AMI_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"


def get_session(profile, region):
    return boto3.Session(profile_name=profile, region_name=region)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def get_ami(session):
    ssm = session.client("ssm")
    return ssm.get_parameter(Name=AMI_SSM_PARAM)["Parameter"]["Value"]


def wait_for(desc, check_fn, timeout=300, interval=10):
    """Poll check_fn() until it returns truthy or timeout."""
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
        print("Resources file already exists. Run teardown first or delete resources.json.")
        sys.exit(1)

    r = {}
    sess_a = get_session(profile, REGION_A)
    sess_b = get_session(profile, REGION_B)
    ec2_a = sess_a.client("ec2")
    ec2_b = sess_b.client("ec2")
    r53r_a = sess_a.client("route53resolver")
    r53r_b = sess_b.client("route53resolver")
    r53 = sess_a.client("route53")
    logs_a = sess_a.client("logs")
    logs_b = sess_b.client("logs")
    sts = sess_a.client("sts")

    account_id = sts.get_caller_identity()["Account"]
    r["account_id"] = account_id
    print(f"Account: {account_id}")

    # ── 1. Get AZs ─────────────────────────────────────────────────────────
    azs_a = [az["ZoneName"] for az in ec2_a.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )["AvailabilityZones"][:2]]
    azs_b = [az["ZoneName"] for az in ec2_b.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )["AvailabilityZones"][:2]]

    # ── 2. VPCs ─────────────────────────────────────────────────────────────
    print("Creating VPCs...")
    vpc_onprem = ec2_a.create_vpc(CidrBlock=ONPREM_CIDR, TagSpecifications=[
        {"ResourceType": "vpc", "Tags": [{"Key": "Name", "Value": "NET-015-onprem"}]}
    ])["Vpc"]["VpcId"]
    ec2_a.modify_vpc_attribute(VpcId=vpc_onprem, EnableDnsSupport={"Value": True})
    ec2_a.modify_vpc_attribute(VpcId=vpc_onprem, EnableDnsHostnames={"Value": True})

    vpc_a = ec2_a.create_vpc(CidrBlock=VPCA_CIDR, TagSpecifications=[
        {"ResourceType": "vpc", "Tags": [{"Key": "Name", "Value": "NET-015-production"}]}
    ])["Vpc"]["VpcId"]
    ec2_a.modify_vpc_attribute(VpcId=vpc_a, EnableDnsSupport={"Value": True})
    ec2_a.modify_vpc_attribute(VpcId=vpc_a, EnableDnsHostnames={"Value": True})

    vpc_b = ec2_b.create_vpc(CidrBlock=VPCB_CIDR, TagSpecifications=[
        {"ResourceType": "vpc", "Tags": [{"Key": "Name", "Value": "NET-015-dr"}]}
    ])["Vpc"]["VpcId"]
    ec2_b.modify_vpc_attribute(VpcId=vpc_b, EnableDnsSupport={"Value": True})
    ec2_b.modify_vpc_attribute(VpcId=vpc_b, EnableDnsHostnames={"Value": True})

    r["vpc_onprem"] = vpc_onprem
    r["vpc_a"] = vpc_a
    r["vpc_b"] = vpc_b
    save(r)

    # ── 3. Subnets ──────────────────────────────────────────────────────────
    print("Creating subnets...")
    sub_onprem = ec2_a.create_subnet(VpcId=vpc_onprem, CidrBlock=ONPREM_SUB1,
        AvailabilityZone=azs_a[0], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-onprem-sub1"}]}
        ])["Subnet"]["SubnetId"]

    sub_a1 = ec2_a.create_subnet(VpcId=vpc_a, CidrBlock=VPCA_SUB1,
        AvailabilityZone=azs_a[0], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-prod-resolver-az1"}]}
        ])["Subnet"]["SubnetId"]
    sub_a2 = ec2_a.create_subnet(VpcId=vpc_a, CidrBlock=VPCA_SUB2,
        AvailabilityZone=azs_a[1], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-prod-resolver-az2"}]}
        ])["Subnet"]["SubnetId"]
    sub_a3 = ec2_a.create_subnet(VpcId=vpc_a, CidrBlock=VPCA_SUB3,
        AvailabilityZone=azs_a[0], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-prod-workload"}]}
        ])["Subnet"]["SubnetId"]

    sub_b1 = ec2_b.create_subnet(VpcId=vpc_b, CidrBlock=VPCB_SUB1,
        AvailabilityZone=azs_b[0], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-dr-resolver-az1"}]}
        ])["Subnet"]["SubnetId"]
    sub_b2 = ec2_b.create_subnet(VpcId=vpc_b, CidrBlock=VPCB_SUB2,
        AvailabilityZone=azs_b[1], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-dr-resolver-az2"}]}
        ])["Subnet"]["SubnetId"]
    sub_b3 = ec2_b.create_subnet(VpcId=vpc_b, CidrBlock=VPCB_SUB3,
        AvailabilityZone=azs_b[0], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": "NET-015-dr-workload"}]}
        ])["Subnet"]["SubnetId"]

    r["subnets"] = {
        "onprem": sub_onprem,
        "a1": sub_a1, "a2": sub_a2, "a3": sub_a3,
        "b1": sub_b1, "b2": sub_b2, "b3": sub_b3,
    }
    save(r)

    # ── 4. Internet Gateway for on-prem VPC (SSM needs internet) ───────────
    print("Creating IGW for on-prem VPC (SSM connectivity)...")
    igw = ec2_a.create_internet_gateway(TagSpecifications=[
        {"ResourceType": "internet-gateway", "Tags": [{"Key": "Name", "Value": "NET-015-onprem-igw"}]}
    ])["InternetGateway"]["InternetGatewayId"]
    ec2_a.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc_onprem)

    # Route table for on-prem subnet
    rtb_onprem = ec2_a.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_onprem]}]
    )["RouteTables"][0]["RouteTableId"]
    ec2_a.create_route(RouteTableId=rtb_onprem, DestinationCidrBlock="0.0.0.0/0",
                       GatewayId=igw)
    r["igw_onprem"] = igw
    save(r)

    # ── 5. VPC Peerings ────────────────────────────────────────────────────
    print("Creating VPC peerings...")

    # On-prem ↔ VPC-A (intra-region)
    peer_onprem_a = ec2_a.create_vpc_peering_connection(
        VpcId=vpc_onprem, PeerVpcId=vpc_a, PeerRegion=REGION_A,
        TagSpecifications=[{"ResourceType": "vpc-peering-connection",
                           "Tags": [{"Key": "Name", "Value": "NET-015-onprem-to-prod"}]}]
    )["VpcPeeringConnection"]["VpcPeeringConnectionId"]
    ec2_a.accept_vpc_peering_connection(VpcPeeringConnectionId=peer_onprem_a)

    # VPC-A ↔ VPC-B (cross-region)
    peer_a_b = ec2_a.create_vpc_peering_connection(
        VpcId=vpc_a, PeerVpcId=vpc_b, PeerRegion=REGION_B,
        TagSpecifications=[{"ResourceType": "vpc-peering-connection",
                           "Tags": [{"Key": "Name", "Value": "NET-015-prod-to-dr"}]}]
    )["VpcPeeringConnection"]["VpcPeeringConnectionId"]
    # Cross-region peering must be accepted from the peer region
    time.sleep(5)  # propagation
    ec2_b.accept_vpc_peering_connection(VpcPeeringConnectionId=peer_a_b)

    r["peering_onprem_a"] = peer_onprem_a
    r["peering_a_b"] = peer_a_b
    save(r)

    # ── 6. Route tables ────────────────────────────────────────────────────
    print("Configuring routes...")

    # On-prem → VPC-A
    ec2_a.create_route(RouteTableId=rtb_onprem, DestinationCidrBlock=VPCA_CIDR,
                       VpcPeeringConnectionId=peer_onprem_a)

    # VPC-A route tables — all subnets need routes to on-prem and to VPC-B
    rtb_a = ec2_a.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_a]}]
    )["RouteTables"][0]["RouteTableId"]
    ec2_a.create_route(RouteTableId=rtb_a, DestinationCidrBlock=ONPREM_CIDR,
                       VpcPeeringConnectionId=peer_onprem_a)
    ec2_a.create_route(RouteTableId=rtb_a, DestinationCidrBlock=VPCB_CIDR,
                       VpcPeeringConnectionId=peer_a_b)

    # VPC-B route table — needs route to VPC-A
    rtb_b = ec2_b.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_b]}]
    )["RouteTables"][0]["RouteTableId"]
    ec2_b.create_route(RouteTableId=rtb_b, DestinationCidrBlock=VPCA_CIDR,
                       VpcPeeringConnectionId=peer_a_b)

    r["rtb_onprem"] = rtb_onprem
    r["rtb_a"] = rtb_a
    r["rtb_b"] = rtb_b
    save(r)

    # ── 7. Security groups ─────────────────────────────────────────────────
    print("Creating security groups...")

    # On-prem DNS server SG — DELIBERATELY UDP-ONLY for Exercise 3
    sg_onprem = ec2_a.create_security_group(
        GroupName="NET-015-onprem-dns", Description="On-prem DNS server",
        VpcId=vpc_onprem, TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [{"Key": "Name", "Value": "NET-015-onprem-dns"}]}
        ])["GroupId"]
    ec2_a.authorize_security_group_ingress(GroupId=sg_onprem, IpPermissions=[
        {"IpProtocol": "udp", "FromPort": 53, "ToPort": 53,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "DNS UDP from anywhere"}]},
        # TCP 53 intentionally MISSING — Exercise 3
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS for SSM"}]},
    ])

    # VPC-A resolver SG — also UDP-ONLY for consistency
    sg_resolver_a = ec2_a.create_security_group(
        GroupName="NET-015-resolver-a", Description="Resolver endpoints production",
        VpcId=vpc_a, TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [{"Key": "Name", "Value": "NET-015-resolver-a"}]}
        ])["GroupId"]
    ec2_a.authorize_security_group_ingress(GroupId=sg_resolver_a, IpPermissions=[
        {"IpProtocol": "udp", "FromPort": 53, "ToPort": 53,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "DNS UDP"}]},
        # TCP 53 intentionally MISSING
    ])
    ec2_a.authorize_security_group_egress(GroupId=sg_resolver_a, IpPermissions=[
        {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ])

    # VPC-B resolver SG
    sg_resolver_b = ec2_b.create_security_group(
        GroupName="NET-015-resolver-b", Description="Resolver endpoints DR",
        VpcId=vpc_b, TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [{"Key": "Name", "Value": "NET-015-resolver-b"}]}
        ])["GroupId"]
    ec2_b.authorize_security_group_ingress(GroupId=sg_resolver_b, IpPermissions=[
        {"IpProtocol": "udp", "FromPort": 53, "ToPort": 53,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "DNS UDP"}]},
        # TCP 53 intentionally MISSING
    ])
    ec2_b.authorize_security_group_egress(GroupId=sg_resolver_b, IpPermissions=[
        {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ])

    # Workload SGs (for test EC2s — SSM only, no inbound needed)
    sg_workload_a = ec2_a.create_security_group(
        GroupName="NET-015-workload-a", Description="Workload production",
        VpcId=vpc_a, TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [{"Key": "Name", "Value": "NET-015-workload-a"}]}
        ])["GroupId"]
    sg_workload_b = ec2_b.create_security_group(
        GroupName="NET-015-workload-b", Description="Workload DR",
        VpcId=vpc_b, TagSpecifications=[
            {"ResourceType": "security-group", "Tags": [{"Key": "Name", "Value": "NET-015-workload-b"}]}
        ])["GroupId"]

    r["security_groups"] = {
        "onprem": sg_onprem,
        "resolver_a": sg_resolver_a,
        "resolver_b": sg_resolver_b,
        "workload_a": sg_workload_a,
        "workload_b": sg_workload_b,
    }
    save(r)

    # ── 8. SSM role for EC2 instances ──────────────────────────────────────
    print("Creating IAM role for SSM...")
    iam = sess_a.client("iam")
    role_name = "NET-015-ssm-role"
    instance_profile_name = "NET-015-ssm-profile"

    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                               "Action": "sts:AssumeRole"}]
            }),
            Tags=[{"Key": "lab", "Value": "NET-015"}],
        )
        iam.attach_role_policy(RoleName=role_name,
                               PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    except ClientError as e:
        if "EntityAlreadyExists" not in str(e):
            raise

    try:
        iam.create_instance_profile(InstanceProfileName=instance_profile_name)
        iam.add_role_to_instance_profile(InstanceProfileName=instance_profile_name, RoleName=role_name)
        time.sleep(10)  # IAM propagation
    except ClientError as e:
        if "EntityAlreadyExists" not in str(e):
            raise

    r["iam_role"] = role_name
    r["instance_profile"] = instance_profile_name
    save(r)

    # ── 9. EC2 instances ───────────────────────────────────────────────────
    print("Launching EC2 instances...")
    ami_a = get_ami(sess_a)
    ami_b = get_ami(sess_b)

    # dnsmasq user data
    dnsmasq_conf_content = DNSMASQ_CONF.read_text()
    user_data_dns = f"""#!/bin/bash
yum install -y dnsmasq
cat > /etc/dnsmasq.conf << 'DNSCONF'
{dnsmasq_conf_content}
DNSCONF
systemctl enable dnsmasq
systemctl start dnsmasq
"""

    # On-prem DNS server
    ec2_onprem = ec2_a.run_instances(
        ImageId=ami_a, InstanceType="t3.micro", MinCount=1, MaxCount=1,
        SubnetId=sub_onprem,
        SecurityGroupIds=[sg_onprem],
        IamInstanceProfile={"Name": instance_profile_name},
        UserData=user_data_dns,
        TagSpecifications=[{"ResourceType": "instance",
                           "Tags": [{"Key": "Name", "Value": "NET-015-onprem-dns"},
                                    {"Key": "lab", "Value": "NET-015"}]}],
    )["Instances"][0]
    # Need to assign a public IP for SSM (or use VPC endpoints)
    # Allocate an EIP for the on-prem DNS server
    eip = ec2_a.allocate_address(Domain="vpc", TagSpecifications=[
        {"ResourceType": "elastic-ip", "Tags": [{"Key": "Name", "Value": "NET-015-onprem-dns-eip"}]}
    ])
    time.sleep(5)
    ec2_a.associate_address(AllocationId=eip["AllocationId"],
                            InstanceId=ec2_onprem["InstanceId"])

    # Production test instance (no public IP — needs VPC endpoint for SSM or NAT)
    ec2_prod = ec2_a.run_instances(
        ImageId=ami_a, InstanceType="t3.micro", MinCount=1, MaxCount=1,
        SubnetId=sub_a3,
        SecurityGroupIds=[sg_workload_a],
        IamInstanceProfile={"Name": instance_profile_name},
        TagSpecifications=[{"ResourceType": "instance",
                           "Tags": [{"Key": "Name", "Value": "NET-015-prod-test"},
                                    {"Key": "lab", "Value": "NET-015"}]}],
    )["Instances"][0]

    # DR test instance
    ec2_dr = ec2_b.run_instances(
        ImageId=ami_b, InstanceType="t3.micro", MinCount=1, MaxCount=1,
        SubnetId=sub_b3,
        SecurityGroupIds=[sg_workload_b],
        IamInstanceProfile={"Name": instance_profile_name},
        TagSpecifications=[{"ResourceType": "instance",
                           "Tags": [{"Key": "Name", "Value": "NET-015-dr-test"},
                                    {"Key": "lab", "Value": "NET-015"}]}],
    )["Instances"][0]

    r["instances"] = {
        "onprem_dns": ec2_onprem["InstanceId"],
        "prod_test": ec2_prod["InstanceId"],
        "dr_test": ec2_dr["InstanceId"],
    }
    r["eip_onprem"] = eip["AllocationId"]
    save(r)

    # Get on-prem DNS private IP
    time.sleep(5)
    onprem_info = ec2_a.describe_instances(InstanceIds=[ec2_onprem["InstanceId"]])
    onprem_dns_ip = onprem_info["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    r["onprem_dns_ip"] = onprem_dns_ip
    print(f"  On-prem DNS IP: {onprem_dns_ip}")
    save(r)

    # ── 10. SSM VPC endpoints for private instances ────────────────────────
    print("Creating SSM VPC endpoints for production VPC...")
    for svc in ["ssm", "ssmmessages", "ec2messages"]:
        ep = ec2_a.create_vpc_endpoint(
            VpcId=vpc_a, VpcEndpointType="Interface",
            ServiceName=f"com.amazonaws.{REGION_A}.{svc}",
            SubnetIds=[sub_a3], SecurityGroupIds=[sg_workload_a],
            PrivateDnsEnabled=True,
            TagSpecifications=[{"ResourceType": "vpc-endpoint",
                               "Tags": [{"Key": "Name", "Value": f"NET-015-prod-{svc}"}]}],
        )
        r.setdefault("vpc_endpoints_a", {})[svc] = ep["VpcEndpoint"]["VpcEndpointId"]

    print("Creating SSM VPC endpoints for DR VPC...")
    for svc in ["ssm", "ssmmessages", "ec2messages"]:
        ep = ec2_b.create_vpc_endpoint(
            VpcId=vpc_b, VpcEndpointType="Interface",
            ServiceName=f"com.amazonaws.{REGION_B}.{svc}",
            SubnetIds=[sub_b3], SecurityGroupIds=[sg_workload_b],
            PrivateDnsEnabled=True,
            TagSpecifications=[{"ResourceType": "vpc-endpoint",
                               "Tags": [{"Key": "Name", "Value": f"NET-015-dr-{svc}"}]}],
        )
        r.setdefault("vpc_endpoints_b", {})[svc] = ep["VpcEndpoint"]["VpcEndpointId"]
    save(r)

    # ── 11. Resolver endpoints ─────────────────────────────────────────────
    print("Creating resolver endpoints (this takes 1-2 minutes each)...")

    # VPC-A inbound endpoint (receives from VPC-B and on-prem)
    inbound_a = r53r_a.create_resolver_endpoint(
        CreatorRequestId="net015-inbound-a",
        Name="NET-015-prod-inbound",
        SecurityGroupIds=[sg_resolver_a],
        Direction="INBOUND",
        IpAddresses=[
            {"SubnetId": sub_a1, "Ip": "10.0.1.10"},
            {"SubnetId": sub_a2, "Ip": "10.0.2.10"},
        ],
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverEndpoint"]

    # VPC-A outbound endpoint (sends to on-prem)
    outbound_a = r53r_a.create_resolver_endpoint(
        CreatorRequestId="net015-outbound-a",
        Name="NET-015-prod-outbound",
        SecurityGroupIds=[sg_resolver_a],
        Direction="OUTBOUND",
        IpAddresses=[
            {"SubnetId": sub_a1, "Ip": "10.0.1.20"},
            {"SubnetId": sub_a2, "Ip": "10.0.2.20"},
        ],
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverEndpoint"]

    # VPC-B outbound endpoint (sends to VPC-A inbound)
    outbound_b = r53r_b.create_resolver_endpoint(
        CreatorRequestId="net015-outbound-b",
        Name="NET-015-dr-outbound",
        SecurityGroupIds=[sg_resolver_b],
        Direction="OUTBOUND",
        IpAddresses=[
            {"SubnetId": sub_b1, "Ip": "10.1.1.20"},
            {"SubnetId": sub_b2, "Ip": "10.1.2.20"},
        ],
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverEndpoint"]

    r["endpoints"] = {
        "inbound_a": inbound_a["Id"],
        "outbound_a": outbound_a["Id"],
        "outbound_b": outbound_b["Id"],
    }
    save(r)

    # Wait for endpoints to become OPERATIONAL
    for name, eid, client in [
        ("inbound-a", inbound_a["Id"], r53r_a),
        ("outbound-a", outbound_a["Id"], r53r_a),
        ("outbound-b", outbound_b["Id"], r53r_b),
    ]:
        def check(c=client, i=eid):
            resp = c.get_resolver_endpoint(ResolverEndpointId=i)
            return resp["ResolverEndpoint"]["Status"] == "OPERATIONAL"
        wait_for(f"endpoint {name} OPERATIONAL", check, timeout=300)
    print("  All endpoints OPERATIONAL")

    # ── 12. Forwarding rules ───────────────────────────────────────────────
    print("Creating forwarding rules...")

    # VPC-A: corp.example.com → on-prem DNS
    rule_a = r53r_a.create_resolver_rule(
        CreatorRequestId="net015-fwd-a",
        Name="NET-015-corp-to-onprem",
        RuleType="FORWARD",
        DomainName="corp.example.com",
        ResolverEndpointId=outbound_a["Id"],
        TargetIps=[{"Ip": onprem_dns_ip, "Port": 53}],
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverRule"]

    # Associate with VPC-A
    r53r_a.associate_resolver_rule(
        ResolverRuleId=rule_a["Id"], Name="NET-015-corp-vpca", VPCId=vpc_a)

    # VPC-B: corp.example.com → VPC-A inbound endpoint IPs
    rule_b = r53r_b.create_resolver_rule(
        CreatorRequestId="net015-fwd-b",
        Name="NET-015-corp-via-prod",
        RuleType="FORWARD",
        DomainName="corp.example.com",
        ResolverEndpointId=outbound_b["Id"],
        TargetIps=[
            {"Ip": "10.0.1.10", "Port": 53},
            {"Ip": "10.0.2.10", "Port": 53},
        ],
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverRule"]

    # Associate with VPC-B
    r53r_b.associate_resolver_rule(
        ResolverRuleId=rule_b["Id"], Name="NET-015-corp-vpcb", VPCId=vpc_b)

    r["rules"] = {"rule_a": rule_a["Id"], "rule_b": rule_b["Id"]}
    save(r)

    # ── 13. Private Hosted Zones ───────────────────────────────────────────
    print("Creating Private Hosted Zones...")

    phz_prod = r53.create_hosted_zone(
        Name="prod.internal",
        CallerReference=f"net015-prod-{int(time.time())}",
        VPC={"VPCRegion": REGION_A, "VPCId": vpc_a},
        HostedZoneConfig={"Comment": "NET-015 production PHZ", "PrivateZone": True},
    )["HostedZone"]

    phz_dr = r53.create_hosted_zone(
        Name="dr.internal",
        CallerReference=f"net015-dr-{int(time.time())}",
        VPC={"VPCRegion": REGION_B, "VPCId": vpc_b},
        HostedZoneConfig={"Comment": "NET-015 DR PHZ", "PrivateZone": True},
    )["HostedZone"]

    # Cross-region PHZ association: prod.internal → VPC-B
    r53.associate_vpc_with_hosted_zone(
        HostedZoneId=phz_prod["Id"].split("/")[-1],
        VPC={"VPCRegion": REGION_B, "VPCId": vpc_b},
    )

    r["phz"] = {
        "prod": phz_prod["Id"].split("/")[-1],
        "dr": phz_dr["Id"].split("/")[-1],
    }
    save(r)

    # Add records to PHZs
    print("Adding DNS records...")
    r53.change_resource_record_sets(
        HostedZoneId=r["phz"]["prod"],
        ChangeBatch={"Changes": [
            {"Action": "UPSERT", "ResourceRecordSet": {
                "Name": "app.prod.internal", "Type": "A", "TTL": 60,
                "ResourceRecords": [{"Value": "10.0.10.50"}]}},
            {"Action": "UPSERT", "ResourceRecordSet": {
                "Name": "db.prod.internal", "Type": "A", "TTL": 60,
                "ResourceRecords": [{"Value": "10.0.20.100"}]}},
            {"Action": "UPSERT", "ResourceRecordSet": {
                "Name": "cache.prod.internal", "Type": "A", "TTL": 60,
                "ResourceRecords": [{"Value": "10.0.10.51"}]}},
        ]},
    )

    r53.change_resource_record_sets(
        HostedZoneId=r["phz"]["dr"],
        ChangeBatch={"Changes": [
            {"Action": "UPSERT", "ResourceRecordSet": {
                "Name": "dr-app.dr.internal", "Type": "A", "TTL": 60,
                "ResourceRecords": [{"Value": "10.1.10.50"}]}},
        ]},
    )

    # ── 14. Query logging ──────────────────────────────────────────────────
    print("Enabling query logging...")

    # Create log groups
    for client, name in [(logs_a, "/aws/route53/net015-prod"), (logs_b, "/aws/route53/net015-dr")]:
        try:
            client.create_log_group(logGroupName=name, tags={"lab": "NET-015"})
        except ClientError as e:
            if "ResourceAlreadyExistsException" not in str(e):
                raise

    # Resource policies for log delivery
    for client, region, lg in [
        (logs_a, REGION_A, "/aws/route53/net015-prod"),
        (logs_b, REGION_B, "/aws/route53/net015-dr"),
    ]:
        try:
            client.put_resource_policy(
                policyName=f"net015-query-log-{region}",
                policyDocument=json.dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Sid": "Route53QueryLog",
                        "Effect": "Allow",
                        "Principal": {"Service": "route53.amazonaws.com"},
                        "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                        "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:{lg}:*",
                    }],
                }),
            )
        except ClientError:
            pass  # may already exist

    # Create query logging configs
    qlc_a = r53r_a.create_resolver_query_log_config(
        Name="NET-015-prod-querylog",
        DestinationArn=f"arn:aws:logs:{REGION_A}:{account_id}:log-group:/aws/route53/net015-prod",
        CreatorRequestId=f"net015-qlc-a-{int(time.time())}",
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverQueryLogConfig"]

    qlc_b = r53r_b.create_resolver_query_log_config(
        Name="NET-015-dr-querylog",
        DestinationArn=f"arn:aws:logs:{REGION_B}:{account_id}:log-group:/aws/route53/net015-dr",
        CreatorRequestId=f"net015-qlc-b-{int(time.time())}",
        Tags=[{"Key": "lab", "Value": "NET-015"}],
    )["ResolverQueryLogConfig"]

    # Associate with VPCs
    r53r_a.associate_resolver_query_log_config(
        ResolverQueryLogConfigId=qlc_a["Id"], ResourceId=vpc_a)
    r53r_b.associate_resolver_query_log_config(
        ResolverQueryLogConfigId=qlc_b["Id"], ResourceId=vpc_b)

    r["query_log_configs"] = {"prod": qlc_a["Id"], "dr": qlc_b["Id"]}
    save(r)

    print("\n✅ Deploy complete. Resources saved to resources.json")
    print(f"\nOn-prem DNS IP: {onprem_dns_ip}")
    print(f"VPC-A inbound:  10.0.1.10, 10.0.2.10")
    print(f"VPC-A outbound: 10.0.1.20, 10.0.2.20")
    print(f"VPC-B outbound: 10.1.1.20, 10.1.2.20")
    print(f"\nTest instances (use SSM Session Manager):")
    print(f"  Production: {ec2_prod['InstanceId']} ({REGION_A})")
    print(f"  DR:         {ec2_dr['InstanceId']} ({REGION_B})")
    print(f"\nWait ~2 minutes for dnsmasq to initialize, then run: python deploy_lab.py test")


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def status(profile):
    r = load()
    if not r:
        print("No resources.json found. Run deploy first.")
        return

    sess_a = get_session(profile, REGION_A)
    sess_b = get_session(profile, REGION_B)

    print("── Resolver Endpoints ──")
    for label, eid, region in [
        ("Inbound A", r["endpoints"]["inbound_a"], REGION_A),
        ("Outbound A", r["endpoints"]["outbound_a"], REGION_A),
        ("Outbound B", r["endpoints"]["outbound_b"], REGION_B),
    ]:
        sess = sess_a if region == REGION_A else sess_b
        client = sess.client("route53resolver")
        ep = client.get_resolver_endpoint(ResolverEndpointId=eid)["ResolverEndpoint"]
        print(f"  {label}: {ep['Status']} ({eid})")

    print("\n── Forwarding Rules ──")
    for label, rid, region in [
        ("VPC-A corp.example.com", r["rules"]["rule_a"], REGION_A),
        ("VPC-B corp.example.com", r["rules"]["rule_b"], REGION_B),
    ]:
        sess = sess_a if region == REGION_A else sess_b
        client = sess.client("route53resolver")
        rule = client.get_resolver_rule(ResolverRuleId=rid)["ResolverRule"]
        print(f"  {label}: {rule['Status']} → {[t['Ip'] for t in rule['TargetIps']]}")

    print("\n── EC2 Instances ──")
    for label, iid, region in [
        ("On-prem DNS", r["instances"]["onprem_dns"], REGION_A),
        ("Prod test", r["instances"]["prod_test"], REGION_A),
        ("DR test", r["instances"]["dr_test"], REGION_B),
    ]:
        sess = sess_a if region == REGION_A else sess_b
        ec2 = sess.client("ec2")
        inst = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        print(f"  {label}: {inst['State']['Name']} ({inst.get('PrivateIpAddress', '?')}) [{region}]")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEST
# ═══════════════════════════════════════════════════════════════════════════════

def test(profile):
    r = load()
    if not r:
        print("No resources.json found. Run deploy first.")
        return

    sess_a = get_session(profile, REGION_A)
    sess_b = get_session(profile, REGION_B)
    ssm_a = sess_a.client("ssm")
    ssm_b = sess_b.client("ssm")

    queries = [
        ("Production VPC", r["instances"]["prod_test"], ssm_a, [
            ("app.prod.internal", "10.0.10.50", "PHZ direct"),
            ("filesvr.corp.example.com", "192.168.10.50", "forwarding → on-prem"),
            ("dc1.corp.example.com", "192.168.1.10", "forwarding → on-prem"),
        ]),
        ("DR VPC", r["instances"]["dr_test"], ssm_b, [
            ("app.prod.internal", "10.0.10.50", "cross-region PHZ"),
            ("dr-app.dr.internal", "10.1.10.50", "local PHZ"),
            ("filesvr.corp.example.com", "192.168.10.50", "chain: DR→prod→on-prem"),
            ("dc1.corp.example.com", "192.168.1.10", "chain: DR→prod→on-prem"),
        ]),
    ]

    for vpc_name, instance_id, ssm, tests in queries:
        print(f"\n── {vpc_name} ({instance_id}) ──")
        for domain, expected, desc in tests:
            try:
                resp = ssm.send_command(
                    InstanceIds=[instance_id],
                    DocumentName="AWS-RunShellScript",
                    Parameters={"commands": [f"dig +short {domain} @169.254.169.253"]},
                )
                cmd_id = resp["Command"]["CommandId"]
                time.sleep(4)
                output = ssm.get_command_invocation(
                    CommandId=cmd_id, InstanceId=instance_id
                )
                result = output["StandardOutputContent"].strip()
                ok = expected in result
                mark = "✅" if ok else "❌"
                print(f"  {mark} {domain} → {result or '(empty)'} (expected {expected}, {desc})")
            except Exception as e:
                print(f"  ⚠️  {domain} — SSM error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def teardown(profile):
    r = load()
    if not r:
        print("No resources.json found.")
        return

    sess_a = get_session(profile, REGION_A)
    sess_b = get_session(profile, REGION_B)
    ec2_a = sess_a.client("ec2")
    ec2_b = sess_b.client("ec2")
    r53r_a = sess_a.client("route53resolver")
    r53r_b = sess_b.client("route53resolver")
    r53 = sess_a.client("route53")
    logs_a = sess_a.client("logs")
    logs_b = sess_b.client("logs")
    iam = sess_a.client("iam")

    def safe(fn, *a, **kw):
        try:
            fn(*a, **kw)
        except Exception as e:
            print(f"  (skip: {e})")

    # ── Query log associations & configs ───────────────────────────────────
    print("Removing query logging...")
    for client, cfg_id, vpc_id in [
        (r53r_a, r.get("query_log_configs", {}).get("prod"), r.get("vpc_a")),
        (r53r_b, r.get("query_log_configs", {}).get("dr"), r.get("vpc_b")),
    ]:
        if cfg_id and vpc_id:
            # Find and delete the association
            try:
                assocs = client.list_resolver_query_log_config_associations(
                    Filters=[{"Name": "ResolverQueryLogConfigId", "Values": [cfg_id]}]
                )["ResolverQueryLogConfigAssociations"]
                for a in assocs:
                    safe(client.disassociate_resolver_query_log_config,
                         ResolverQueryLogConfigId=cfg_id, ResourceId=a["ResourceId"])
                time.sleep(5)
            except Exception:
                pass
            safe(client.delete_resolver_query_log_config, ResolverQueryLogConfigId=cfg_id)

    # ── Forwarding rule associations & rules ───────────────────────────────
    print("Removing forwarding rules...")
    for client, rule_id in [
        (r53r_a, r.get("rules", {}).get("rule_a")),
        (r53r_b, r.get("rules", {}).get("rule_b")),
    ]:
        if rule_id:
            try:
                assocs = client.list_resolver_rule_associations(
                    Filters=[{"Name": "ResolverRuleId", "Values": [rule_id]}]
                )["ResolverRuleAssociations"]
                for a in assocs:
                    if a["Status"] != "DELETING":
                        safe(client.disassociate_resolver_rule,
                             ResolverRuleId=rule_id, VPCId=a["VPCId"])
                time.sleep(10)
            except Exception:
                pass
            safe(client.delete_resolver_rule, ResolverRuleId=rule_id)

    # ── Resolver endpoints ─────────────────────────────────────────────────
    print("Deleting resolver endpoints (takes 1-2 min)...")
    for client, eid in [
        (r53r_a, r.get("endpoints", {}).get("inbound_a")),
        (r53r_a, r.get("endpoints", {}).get("outbound_a")),
        (r53r_b, r.get("endpoints", {}).get("outbound_b")),
    ]:
        if eid:
            safe(client.delete_resolver_endpoint, ResolverEndpointId=eid)

    # Wait for endpoint deletion
    time.sleep(30)

    # ── PHZ disassociate & delete ──────────────────────────────────────────
    print("Removing Private Hosted Zones...")
    for phz_id in [r.get("phz", {}).get("prod"), r.get("phz", {}).get("dr")]:
        if not phz_id:
            continue
        # Delete records first
        try:
            rrsets = r53.list_resource_record_sets(HostedZoneId=phz_id)["ResourceRecordSets"]
            changes = [{"Action": "DELETE", "ResourceRecordSet": rr}
                      for rr in rrsets if rr["Type"] not in ("SOA", "NS")]
            if changes:
                r53.change_resource_record_sets(HostedZoneId=phz_id,
                                                ChangeBatch={"Changes": changes})
        except Exception:
            pass
        # Disassociate VPCs
        try:
            zone = r53.get_hosted_zone(Id=phz_id)
            for vpc in zone.get("VPCs", []):
                # Cannot disassociate the last VPC — delete handles it
                try:
                    r53.disassociate_vpc_from_hosted_zone(
                        HostedZoneId=phz_id, VPC=vpc)
                except Exception:
                    pass
        except Exception:
            pass
        safe(r53.delete_hosted_zone, Id=phz_id)

    # ── EC2 instances ──────────────────────────────────────────────────────
    print("Terminating EC2 instances...")
    for region, iid in [
        (REGION_A, r.get("instances", {}).get("onprem_dns")),
        (REGION_A, r.get("instances", {}).get("prod_test")),
        (REGION_B, r.get("instances", {}).get("dr_test")),
    ]:
        if iid:
            client = (sess_a if region == REGION_A else sess_b).client("ec2")
            safe(client.terminate_instances, InstanceIds=[iid])

    # Release EIP
    if r.get("eip_onprem"):
        time.sleep(5)
        safe(ec2_a.release_address, AllocationId=r["eip_onprem"])

    # ── VPC endpoints ──────────────────────────────────────────────────────
    print("Deleting VPC endpoints...")
    for region, endpoints in [
        (REGION_A, r.get("vpc_endpoints_a", {})),
        (REGION_B, r.get("vpc_endpoints_b", {})),
    ]:
        client = (sess_a if region == REGION_A else sess_b).client("ec2")
        for svc, eid in endpoints.items():
            safe(client.delete_vpc_endpoints, VpcEndpointIds=[eid])

    # Wait for instances to terminate
    print("Waiting for instances to terminate...")
    time.sleep(30)

    # ── VPC peerings ───────────────────────────────────────────────────────
    print("Deleting VPC peerings...")
    for pcx in [r.get("peering_onprem_a"), r.get("peering_a_b")]:
        if pcx:
            safe(ec2_a.delete_vpc_peering_connection, VpcPeeringConnectionId=pcx)

    # ── Security groups (non-default) ──────────────────────────────────────
    print("Deleting security groups...")
    time.sleep(10)  # let ENIs detach
    for region, sgs in [
        (REGION_A, [r.get("security_groups", {}).get(k) for k in
                    ["onprem", "resolver_a", "workload_a"]]),
        (REGION_B, [r.get("security_groups", {}).get(k) for k in
                    ["resolver_b", "workload_b"]]),
    ]:
        client = (sess_a if region == REGION_A else sess_b).client("ec2")
        for sg in sgs:
            if sg:
                safe(client.delete_security_group, GroupId=sg)

    # ── IGW ────────────────────────────────────────────────────────────────
    if r.get("igw_onprem"):
        safe(ec2_a.detach_internet_gateway, InternetGatewayId=r["igw_onprem"],
             VpcId=r["vpc_onprem"])
        safe(ec2_a.delete_internet_gateway, InternetGatewayId=r["igw_onprem"])

    # ── Subnets ────────────────────────────────────────────────────────────
    print("Deleting subnets...")
    for region, subs in [
        (REGION_A, [r.get("subnets", {}).get(k) for k in ["onprem", "a1", "a2", "a3"]]),
        (REGION_B, [r.get("subnets", {}).get(k) for k in ["b1", "b2", "b3"]]),
    ]:
        client = (sess_a if region == REGION_A else sess_b).client("ec2")
        for sub in subs:
            if sub:
                safe(client.delete_subnet, SubnetId=sub)

    # ── VPCs ───────────────────────────────────────────────────────────────
    print("Deleting VPCs...")
    for vpc_id in [r.get("vpc_onprem"), r.get("vpc_a")]:
        if vpc_id:
            safe(ec2_a.delete_vpc, VpcId=vpc_id)
    if r.get("vpc_b"):
        safe(ec2_b.delete_vpc, VpcId=r["vpc_b"])

    # ── IAM ────────────────────────────────────────────────────────────────
    print("Cleaning up IAM...")
    safe(iam.remove_role_from_instance_profile,
         InstanceProfileName=r.get("instance_profile", ""),
         RoleName=r.get("iam_role", ""))
    safe(iam.delete_instance_profile,
         InstanceProfileName=r.get("instance_profile", ""))
    safe(iam.detach_role_policy,
         RoleName=r.get("iam_role", ""),
         PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    safe(iam.delete_role, RoleName=r.get("iam_role", ""))

    # ── Log groups ─────────────────────────────────────────────────────────
    print("Deleting log groups...")
    safe(logs_a.delete_log_group, logGroupName="/aws/route53/net015-prod")
    safe(logs_b.delete_log_group, logGroupName="/aws/route53/net015-dr")

    # ── Done ───────────────────────────────────────────────────────────────
    RESOURCE_FILE.unlink(missing_ok=True)
    print("\n✅ Teardown complete.")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="NET-015 Hybrid Resolver Lab")
    parser.add_argument("command", choices=["deploy", "status", "test", "teardown"])
    parser.add_argument("--profile", default=None, help="AWS CLI profile")
    args = parser.parse_args()

    commands = {"deploy": deploy, "status": status, "test": test, "teardown": teardown}
    commands[args.command](args.profile)


if __name__ == "__main__":
    main()
