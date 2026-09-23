"""
NET-004 Lab: Network Firewall TLS Inspection - Cross-Signed Certificate Test
Region: eu-west-1 | Profile: lab

Deploys infrastructure and reproduces the TLS Inspection cross-signed certificate
rejection issue documented in NET-004.

Usage:
  python deploy_lab.py deploy     # Create all infra + generate certs + test TLS config
  python deploy_lab.py test       # Only run cert tests (infra must exist)
  python deploy_lab.py status     # Show current state of all lab resources
  python deploy_lab.py teardown   # Delete everything

Prerequisites:
  pip install boto3 cryptography
"""

import sys
import os
import time
import base64
import subprocess
import tempfile
import boto3
from botocore.exceptions import ClientError

PROFILE = "lab"
REGION = "eu-west-1"
PREFIX = "net004-lab"
VPC_CIDR = "10.0.0.0/16"
FW_SUBNET_CIDR = "10.0.1.0/28"
PUB_SUBNET_CIDR = "10.0.2.0/24"
PRIV_SUBNET_CIDR = "10.0.3.0/24"
AZ = "eu-west-1a"
LAB_TAG = {"Key": "Lab", "Value": "NET-004"}

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ec2 = session.client("ec2")
nfw = session.client("network-firewall")
elbv2 = session.client("elbv2")
acm = session.client("acm")


def tag(name):
    return [{"Key": "Name", "Value": f"{PREFIX}-{name}"}, LAB_TAG]


def find_tagged(resource_type, name_suffix=None):
    filters = [{"Name": "tag:Lab", "Values": ["NET-004"]}]
    if name_suffix:
        filters.append({"Name": "tag:Name", "Values": [f"{PREFIX}-{name_suffix}"]})

    if resource_type == "vpc":
        items = ec2.describe_vpcs(Filters=filters).get("Vpcs", [])
        return items[0]["VpcId"] if items else None
    elif resource_type == "subnet":
        items = ec2.describe_subnets(Filters=filters).get("Subnets", [])
        return items[0]["SubnetId"] if items else None
    elif resource_type == "igw":
        items = ec2.describe_internet_gateways(Filters=filters).get("InternetGateways", [])
        return items[0]["InternetGatewayId"] if items else None
    elif resource_type == "sg":
        items = ec2.describe_security_groups(Filters=filters + [{"Name": "tag:Name", "Values": [f"{PREFIX}-{name_suffix}"]}]).get("SecurityGroups", [])
        return items[0]["GroupId"] if items else None
    elif resource_type == "instance":
        filters.append({"Name": "instance-state-name", "Values": ["running", "pending", "stopped"]})
        items = ec2.describe_instances(Filters=filters).get("Reservations", [])
        if items and items[0]["Instances"]:
            return items[0]["Instances"][0]["InstanceId"]
    return None


def wait_firewall_ready(fw_name, timeout=600):
    start = time.time()
    while time.time() - start < timeout:
        resp = nfw.describe_firewall(FirewallName=fw_name)
        status = resp["FirewallStatus"]["Status"]
        if status == "READY":
            return resp
        elapsed = int(time.time() - start)
        print(f"    Status: {status} ({elapsed}s)...", end="\r")
        time.sleep(20)
    print("\n  Timeout waiting for firewall")
    return None


def get_firewall_endpoint():
    try:
        resp = nfw.describe_firewall(FirewallName=f"{PREFIX}-firewall")
        for az_data in resp.get("FirewallStatus", {}).get("SyncStates", {}).values():
            vpce = az_data.get("Attachment", {}).get("EndpointId")
            if vpce:
                return vpce
    except ClientError:
        pass
    return None


def generate_certs_openssl():
    """Generate test certificates using openssl CLI."""
    certs_dir = os.path.join(os.path.dirname(__file__), "certs")
    os.makedirs(certs_dir, exist_ok=True)

    def run(cmd):
        subprocess.run(cmd, shell=True, cwd=certs_dir, capture_output=True, check=True)

    print("  Generating certificates with openssl...")

    # Root CA (self-signed)
    run('openssl genrsa -out root-ca.key 2048')
    run('openssl req -x509 -new -nodes -key root-ca.key -sha256 -days 3650 '
        '-out root-ca.crt -subj "/C=IE/O=NET004 Lab Direct Root CA/CN=NET004 Direct Root CA"')

    # Leaf cert
    run('openssl genrsa -out leaf.key 2048')
    run('openssl req -new -key leaf.key -out leaf.csr '
        '-subj "/C=IE/O=NET004 Lab/CN=test.net004-lab.example.com"')

    with open(os.path.join(certs_dir, "leaf.ext"), "w") as f:
        f.write("authorityKeyIdentifier=keyid,issuer\n"
                "basicConstraints=CA:FALSE\n"
                "keyUsage=digitalSignature,nonRepudiation,keyEncipherment,dataEncipherment\n"
                "subjectAltName=DNS:test.net004-lab.example.com\n")

    run('openssl x509 -req -in leaf.csr -CA root-ca.crt -CAkey root-ca.key '
        '-CAcreateserial -out leaf.crt -days 365 -sha256 -extfile leaf.ext')

    # Old Root CA (simulates DST Root CA X3 for cross-signing)
    run('openssl genrsa -out old-root-ca.key 2048')
    run('openssl req -x509 -new -nodes -key old-root-ca.key -sha256 -days 3650 '
        '-out old-root-ca.crt -subj "/C=US/O=Legacy Trust Authority/CN=Legacy Root CA (simulates DST Root CA X3)"')

    # Cross-sign: Old Root signs our Root CA
    run('openssl req -new -key root-ca.key -out root-ca-cross.csr '
        '-subj "/C=IE/O=NET004 Lab Direct Root CA/CN=NET004 Direct Root CA"')

    with open(os.path.join(certs_dir, "cross-sign.ext"), "w") as f:
        f.write("basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n")

    run('openssl x509 -req -in root-ca-cross.csr -CA old-root-ca.crt -CAkey old-root-ca.key '
        '-CAcreateserial -out root-ca-cross-signed.crt -days 3650 -sha256 -extfile cross-sign.ext')

    # Build chain files
    run('cp root-ca.crt chain-direct.pem')
    run('cat root-ca.crt root-ca-cross-signed.crt > chain-cross-signed.pem')

    print("  Certificates generated in lab/certs/")
    return certs_dir


def import_cert_to_acm(certs_dir, chain_type):
    """Import a certificate into ACM. chain_type: 'direct' or 'cross-signed'"""
    with open(os.path.join(certs_dir, "leaf.crt")) as f:
        cert = f.read()
    with open(os.path.join(certs_dir, "leaf.key")) as f:
        key = f.read()

    chain_file = "chain-direct.pem" if chain_type == "direct" else "chain-cross-signed.pem"
    with open(os.path.join(certs_dir, chain_file)) as f:
        chain = f.read()

    resp = acm.import_certificate(
        Certificate=cert.encode(),
        PrivateKey=key.encode(),
        CertificateChain=chain.encode(),
        Tags=[LAB_TAG, {"Key": "Test", "Value": chain_type}],
    )
    return resp["CertificateArn"]


def create_tls_inspection_config(name, cert_arn):
    """Attempt to create a TLS Inspection Configuration."""
    return nfw.create_tls_inspection_configuration(
        TLSInspectionConfigurationName=name,
        TLSInspectionConfiguration={
            "ServerCertificateConfigurations": [{
                "ServerCertificates": [{"ResourceArn": cert_arn}],
                "Scopes": [{
                    "Sources": [{"AddressDefinition": "0.0.0.0/0"}],
                    "Destinations": [{"AddressDefinition": "0.0.0.0/0"}],
                    "SourcePorts": [{"FromPort": 0, "ToPort": 65535}],
                    "DestinationPorts": [{"FromPort": 443, "ToPort": 443}],
                    "Protocols": [6],
                }],
            }]
        },
        Tags=tag(name.replace(f"{PREFIX}-", "")),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DEPLOY
# ─────────────────────────────────────────────────────────────────────────────

def deploy():
    print(f"\n{'='*60}")
    print(f" NET-004 Lab — Full Deployment")
    print(f" Region: {REGION} | Profile: {PROFILE}")
    print(f"{'='*60}\n")

    # ── VPC ──
    print("[1/9] VPC...")
    vpc_id = find_tagged("vpc", "vpc")
    if not vpc_id:
        resp = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=[{"ResourceType": "vpc", "Tags": tag("vpc")}])
        vpc_id = resp["Vpc"]["VpcId"]
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
        ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    print(f"  {vpc_id}")

    # ── IGW ──
    print("[2/9] Internet Gateway...")
    igw_id = find_tagged("igw", "igw")
    if not igw_id:
        resp = ec2.create_internet_gateway(TagSpecifications=[{"ResourceType": "internet-gateway", "Tags": tag("igw")}])
        igw_id = resp["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    print(f"  {igw_id}")

    # ── Subnets ──
    print("[3/9] Subnets...")
    subnets = {}
    for name, cidr in [("fw-subnet", FW_SUBNET_CIDR), ("pub-subnet", PUB_SUBNET_CIDR), ("priv-subnet", PRIV_SUBNET_CIDR)]:
        sid = find_tagged("subnet", name)
        if not sid:
            resp = ec2.create_subnet(
                VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=AZ,
                TagSpecifications=[{"ResourceType": "subnet", "Tags": tag(name)}]
            )
            sid = resp["Subnet"]["SubnetId"]
        subnets[name] = sid
        print(f"  {name}: {sid} ({cidr})")
    ec2.modify_subnet_attribute(SubnetId=subnets["pub-subnet"], MapPublicIpOnLaunch={"Value": True})

    # ── Route Tables ──
    print("[4/9] Route Tables...")
    existing_rts = ec2.describe_route_tables(Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]}])["RouteTables"]
    existing_rt_names = [next((t["Value"] for t in rt.get("Tags", []) if t["Key"] == "Name"), "") for rt in existing_rts]

    if f"{PREFIX}-fw-rt" not in existing_rt_names:
        fw_rt = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("fw-rt")}])
        fw_rt_id = fw_rt["RouteTable"]["RouteTableId"]
        ec2.create_route(RouteTableId=fw_rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
        ec2.associate_route_table(RouteTableId=fw_rt_id, SubnetId=subnets["fw-subnet"])
        print(f"  fw-rt: {fw_rt_id}")
    else:
        print(f"  fw-rt: exists")

    if f"{PREFIX}-pub-rt" not in existing_rt_names:
        pub_rt = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("pub-rt")}])
        pub_rt_id = pub_rt["RouteTable"]["RouteTableId"]
        ec2.create_route(RouteTableId=pub_rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
        ec2.associate_route_table(RouteTableId=pub_rt_id, SubnetId=subnets["pub-subnet"])
        print(f"  pub-rt: {pub_rt_id}")
    else:
        print(f"  pub-rt: exists")

    if f"{PREFIX}-priv-rt" not in existing_rt_names:
        priv_rt = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("priv-rt")}])
        priv_rt_id = priv_rt["RouteTable"]["RouteTableId"]
        ec2.associate_route_table(RouteTableId=priv_rt_id, SubnetId=subnets["priv-subnet"])
        print(f"  priv-rt: {priv_rt_id}")
    else:
        print(f"  priv-rt: exists")

    # ── Security Groups ──
    print("[5/9] Security Groups...")
    alb_sg_id = find_tagged("sg", "alb-sg")
    if not alb_sg_id:
        alb_sg = ec2.create_security_group(
            GroupName=f"{PREFIX}-alb-sg", Description="ALB - HTTPS inbound",
            VpcId=vpc_id, TagSpecifications=[{"ResourceType": "security-group", "Tags": tag("alb-sg")}]
        )
        alb_sg_id = alb_sg["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=alb_sg_id, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
        ])
    print(f"  alb-sg: {alb_sg_id}")

    ec2_sg_id = find_tagged("sg", "ec2-sg")
    if not ec2_sg_id:
        ec2_sg = ec2.create_security_group(
            GroupName=f"{PREFIX}-ec2-sg", Description="EC2 - HTTP from ALB",
            VpcId=vpc_id, TagSpecifications=[{"ResourceType": "security-group", "Tags": tag("ec2-sg")}]
        )
        ec2_sg_id = ec2_sg["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=ec2_sg_id, IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "UserIdGroupPairs": [{"GroupId": alb_sg_id}]}
        ])
    print(f"  ec2-sg: {ec2_sg_id}")

    # ── Network Firewall ──
    print("[6/9] Network Firewall...")
    try:
        rg_resp = nfw.create_rule_group(
            RuleGroupName=f"{PREFIX}-pass-all", Type="STATELESS", Capacity=10,
            RuleGroup={"RulesSource": {"StatelessRulesAndCustomActions": {
                "StatelessRules": [{"RuleDefinition": {
                    "MatchAttributes": {"Sources": [{"AddressDefinition": "0.0.0.0/0"}], "Destinations": [{"AddressDefinition": "0.0.0.0/0"}]},
                    "Actions": ["aws:forward_to_sfe"]}, "Priority": 1}],
                "CustomActions": []}}},
            Tags=tag("pass-all-rg"))
        rg_arn = rg_resp["RuleGroupResponse"]["RuleGroupArn"]
    except ClientError:
        rg_resp = nfw.describe_rule_group(RuleGroupName=f"{PREFIX}-pass-all", Type="STATELESS")
        rg_arn = rg_resp["RuleGroupResponse"]["RuleGroupArn"]
    print(f"  Rule group: {rg_arn.split('/')[-1]}")

    try:
        pol_resp = nfw.create_firewall_policy(
            FirewallPolicyName=f"{PREFIX}-policy",
            FirewallPolicy={
                "StatelessDefaultActions": ["aws:forward_to_sfe"],
                "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"],
                "StatefulDefaultActions": ["aws:alert_established"],
                "StatefulEngineOptions": {"RuleOrder": "STRICT_ORDER"},
                "StatelessRuleGroupReferences": [{"ResourceArn": rg_arn, "Priority": 1}],
            }, Tags=tag("policy"))
        policy_arn = pol_resp["FirewallPolicyResponse"]["FirewallPolicyArn"]
    except ClientError:
        pol_resp = nfw.describe_firewall_policy(FirewallPolicyName=f"{PREFIX}-policy")
        policy_arn = pol_resp["FirewallPolicyResponse"]["FirewallPolicyArn"]
    print(f"  Policy: {policy_arn.split('/')[-1]}")

    try:
        nfw.create_firewall(
            FirewallName=f"{PREFIX}-firewall", FirewallPolicyArn=policy_arn,
            VpcId=vpc_id, SubnetMappings=[{"SubnetId": subnets["fw-subnet"]}],
            DeleteProtection=False, SubnetChangeProtection=False,
            FirewallPolicyChangeProtection=False, Tags=tag("firewall"))
        print("  Firewall creating... waiting for READY (~5 min)")
        wait_firewall_ready(f"{PREFIX}-firewall")
    except ClientError:
        pass

    vpce_id = get_firewall_endpoint()
    if vpce_id:
        print(f"  Firewall endpoint: {vpce_id}")
        if f"{PREFIX}-igw-edge-rt" not in existing_rt_names:
            igw_rt = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table", "Tags": tag("igw-edge-rt")}])
            igw_rt_id = igw_rt["RouteTable"]["RouteTableId"]
            ec2.create_route(RouteTableId=igw_rt_id, DestinationCidrBlock=PUB_SUBNET_CIDR, VpcEndpointId=vpce_id)
            ec2.associate_route_table(RouteTableId=igw_rt_id, GatewayId=igw_id)
            print(f"  IGW edge RT: {igw_rt_id} ({PUB_SUBNET_CIDR} → {vpce_id})")

    # ── EC2 ──
    print("[7/9] EC2 Instance...")
    instance_id = find_tagged("instance")
    if not instance_id:
        ami_resp = ec2.describe_images(
            Owners=["amazon"],
            Filters=[{"Name": "name", "Values": ["al2023-ami-2023*-x86_64"]}, {"Name": "state", "Values": ["available"]}])
        ami_id = sorted(ami_resp["Images"], key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]

        user_data = '#!/bin/bash\nyum install -y nginx\nsystemctl enable nginx\nsystemctl start nginx\necho "<h1>NET-004 Lab</h1>" > /usr/share/nginx/html/index.html'
        inst_resp = ec2.run_instances(
            ImageId=ami_id, InstanceType="t3.micro", MinCount=1, MaxCount=1,
            SubnetId=subnets["priv-subnet"], SecurityGroupIds=[ec2_sg_id],
            UserData=base64.b64encode(user_data.encode()).decode(),
            TagSpecifications=[{"ResourceType": "instance", "Tags": tag("web-server")}])
        instance_id = inst_resp["Instances"][0]["InstanceId"]
    print(f"  {instance_id}")

    # ── ALB ──
    print("[8/9] ALB...")
    try:
        alb_resp = elbv2.create_load_balancer(
            Name=f"{PREFIX}-alb", Subnets=[subnets["pub-subnet"]],
            SecurityGroups=[alb_sg_id], Scheme="internet-facing", Type="application",
            Tags=[{"Key": "Name", "Value": f"{PREFIX}-alb"}, LAB_TAG])
        alb_dns = alb_resp["LoadBalancers"][0]["DNSName"]

        tg_resp = elbv2.create_target_group(
            Name=f"{PREFIX}-tg", Protocol="HTTP", Port=80,
            VpcId=vpc_id, TargetType="instance",
            HealthCheckProtocol="HTTP", HealthCheckPath="/",
            Tags=[LAB_TAG])
        tg_arn = tg_resp["TargetGroups"][0]["TargetGroupArn"]
        elbv2.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": instance_id, "Port": 80}])
        print(f"  ALB: {alb_dns}")
    except ClientError as e:
        if "DuplicateLoadBalancerName" in str(e):
            print("  ALB: exists")
        else:
            raise

    # ── Certificates + TLS Inspection Test ──
    print("[9/9] Certificates & TLS Inspection Test...")
    test()

    print(f"\n{'='*60}")
    print(" Deployment + Test complete!")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────────────────────────────────────

def test():
    print(f"\n{'─'*60}")
    print(" TLS Inspection Configuration — Certificate Validation Test")
    print(f"{'─'*60}\n")

    certs_dir = generate_certs_openssl()

    # ── Test 1: Direct root ──
    print("\n  [Test 1] Chain: leaf → Root CA (self-signed)")
    print("  Expected: NF accepts")
    direct_arn = import_cert_to_acm(certs_dir, "direct")
    print(f"  ACM import OK: ...{direct_arn[-36:]}")

    try:
        resp = create_tls_inspection_config(f"{PREFIX}-tls-direct", direct_arn)
        tls_arn = resp["TLSInspectionConfigurationResponse"]["TLSInspectionConfigurationArn"]
        print(f"  Result: ACCEPTED")
        print(f"  TLS Config ARN: ...{tls_arn[-40:]}")
    except ClientError as e:
        msg = e.response["Error"]["Message"]
        if "already exists" in msg.lower():
            print(f"  Result: ACCEPTED (already exists)")
        else:
            print(f"  Result: REJECTED — {msg}")

    # ── Test 2: Cross-signed root ──
    print("\n  [Test 2] Chain: leaf → Root CA → Root CA cross-signed by Old Root")
    print("  Expected: NF rejects (cross-signed root detected)")
    cross_arn = import_cert_to_acm(certs_dir, "cross-signed")
    print(f"  ACM import OK: ...{cross_arn[-36:]}")

    try:
        resp = create_tls_inspection_config(f"{PREFIX}-tls-cross-signed", cross_arn)
        print(f"  Result: ACCEPTED (unexpected!)")
    except ClientError as e:
        msg = e.response["Error"]["Message"]
        if "already exists" in msg.lower():
            print(f"  Result: already tested")
        else:
            print(f"  Result: REJECTED")
            print(f"  Error:  \"{msg}\"")
            print(f"\n  >>> NET-004 issue CONFIRMED <<<")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────────────────────────────────────

def status():
    print(f"\n{'='*60}")
    print(f" NET-004 Lab Status — {REGION}")
    print(f"{'='*60}\n")

    # VPC
    vpc_id = find_tagged("vpc", "vpc")
    print(f"  VPC:        {vpc_id or 'NOT FOUND'}")

    # Subnets
    for name in ["fw-subnet", "pub-subnet", "priv-subnet"]:
        sid = find_tagged("subnet", name)
        print(f"  {name:12s} {sid or 'NOT FOUND'}")

    # IGW
    igw_id = find_tagged("igw", "igw")
    print(f"  IGW:        {igw_id or 'NOT FOUND'}")

    # Firewall
    try:
        fw = nfw.describe_firewall(FirewallName=f"{PREFIX}-firewall")
        fw_status = fw["FirewallStatus"]["Status"]
        vpce = get_firewall_endpoint()
        print(f"  Firewall:   {fw_status} (endpoint: {vpce})")
    except ClientError:
        print(f"  Firewall:   NOT FOUND")

    # TLS Inspection Configs
    print(f"\n  TLS Inspection Configs:")
    for name in [f"{PREFIX}-tls-direct", f"{PREFIX}-tls-cross-signed"]:
        try:
            nfw.describe_tls_inspection_configuration(TLSInspectionConfigurationName=name)
            print(f"    {name}: EXISTS")
        except ClientError:
            print(f"    {name}: NOT FOUND")

    # ACM Certs
    print(f"\n  ACM Certificates (Lab tagged):")
    certs = acm.list_certificates()["CertificateSummaryList"]
    found = 0
    for cert in certs:
        try:
            tags_resp = acm.list_tags_for_certificate(CertificateArn=cert["CertificateArn"])
            if any(t["Key"] == "Lab" and t["Value"] == "NET-004" for t in tags_resp["Tags"]):
                test_type = next((t["Value"] for t in tags_resp["Tags"] if t["Key"] == "Test"), "unknown")
                print(f"    [{test_type:12s}] ...{cert['CertificateArn'][-36:]}")
                found += 1
        except ClientError:
            pass
    if not found:
        print(f"    (none)")

    # EC2
    instance_id = find_tagged("instance")
    print(f"\n  EC2:        {instance_id or 'NOT FOUND'}")

    # ALB
    try:
        albs = elbv2.describe_load_balancers(Names=[f"{PREFIX}-alb"])
        alb_dns = albs["LoadBalancers"][0]["DNSName"]
        print(f"  ALB:        {alb_dns}")
    except ClientError:
        print(f"  ALB:        NOT FOUND")

    print()


# ─────────────────────────────────────────────────────────────────────────────
# TEARDOWN
# ─────────────────────────────────────────────────────────────────────────────

def teardown():
    print(f"\n{'='*60}")
    print(f" NET-004 Lab Teardown — {REGION}")
    print(f"{'='*60}\n")

    # TLS Inspection Configs
    print("[1/10] TLS Inspection Configurations...")
    for name in [f"{PREFIX}-tls-direct", f"{PREFIX}-tls-cross-signed",
                 f"{PREFIX}-tls-direct-root", f"{PREFIX}-tls-cross-signed"]:
        try:
            nfw.delete_tls_inspection_configuration(TLSInspectionConfigurationName=name)
            print(f"  Deleted: {name}")
        except ClientError:
            pass

    # ACM Certificates
    print("[2/10] ACM certificates...")
    certs = acm.list_certificates()["CertificateSummaryList"]
    for cert in certs:
        try:
            tags_resp = acm.list_tags_for_certificate(CertificateArn=cert["CertificateArn"])
            if any(t["Key"] == "Lab" and t["Value"] == "NET-004" for t in tags_resp["Tags"]):
                acm.delete_certificate(CertificateArn=cert["CertificateArn"])
                print(f"  Deleted: ...{cert['CertificateArn'][-36:]}")
        except ClientError:
            pass

    # Network Firewall
    print("[3/10] Network Firewall...")
    try:
        nfw.delete_firewall(FirewallName=f"{PREFIX}-firewall")
        print("  Deleting firewall (waiting up to 5 min)...")
        for _ in range(30):
            try:
                nfw.describe_firewall(FirewallName=f"{PREFIX}-firewall")
                time.sleep(20)
            except ClientError:
                break
        print("  Firewall deleted")
    except ClientError:
        pass

    # Policy + Rule group
    print("[4/10] Firewall Policy & Rule Group...")
    try:
        nfw.delete_firewall_policy(FirewallPolicyName=f"{PREFIX}-policy")
        print("  Deleted policy")
    except ClientError:
        pass
    try:
        nfw.delete_rule_group(RuleGroupName=f"{PREFIX}-pass-all", Type="STATELESS")
        print("  Deleted rule group")
    except ClientError:
        pass

    # ALB
    print("[5/10] ALB...")
    try:
        albs = elbv2.describe_load_balancers(Names=[f"{PREFIX}-alb"])
        for lb in albs["LoadBalancers"]:
            # Delete listeners first
            listeners = elbv2.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])
            for l in listeners.get("Listeners", []):
                elbv2.delete_listener(ListenerArn=l["ListenerArn"])
            elbv2.delete_load_balancer(LoadBalancerArn=lb["LoadBalancerArn"])
            print(f"  Deleted ALB")
    except ClientError:
        pass

    # Target groups
    try:
        tgs = elbv2.describe_target_groups(Names=[f"{PREFIX}-tg"])
        for tg in tgs["TargetGroups"]:
            elbv2.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
            print(f"  Deleted target group")
    except ClientError:
        pass

    # EC2
    print("[6/10] EC2 instances...")
    instances = ec2.describe_instances(
        Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]},
                 {"Name": "instance-state-name", "Values": ["running", "stopped", "pending"]}])
    instance_ids = [i["InstanceId"] for r in instances["Reservations"] for i in r["Instances"]]
    if instance_ids:
        ec2.terminate_instances(InstanceIds=instance_ids)
        print(f"  Terminating: {instance_ids}")
        waiter = ec2.get_waiter("instance_terminated")
        waiter.wait(InstanceIds=instance_ids, WaiterConfig={"Delay": 10, "MaxAttempts": 30})
        print("  Terminated")

    # Security Groups
    print("[7/10] Security Groups...")
    time.sleep(5)
    sgs = ec2.describe_security_groups(Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]}])
    for sg in sgs["SecurityGroups"]:
        if sg["GroupName"] != "default":
            try:
                ec2.delete_security_group(GroupId=sg["GroupId"])
                print(f"  Deleted: {sg['GroupName']}")
            except ClientError as e:
                print(f"  Retry {sg['GroupName']}...")
                time.sleep(10)
                try:
                    ec2.delete_security_group(GroupId=sg["GroupId"])
                    print(f"  Deleted: {sg['GroupName']}")
                except ClientError:
                    print(f"  Failed: {sg['GroupName']} (may need manual cleanup)")

    # Subnets
    print("[8/10] Subnets...")
    subnets = ec2.describe_subnets(Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]}])
    for subnet in subnets["Subnets"]:
        try:
            ec2.delete_subnet(SubnetId=subnet["SubnetId"])
            print(f"  Deleted: {subnet['SubnetId']}")
        except ClientError as e:
            print(f"  Failed: {subnet['SubnetId']} — {e}")

    # Route tables
    print("[9/10] Route Tables...")
    rts = ec2.describe_route_tables(Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]}])
    for rt in rts["RouteTables"]:
        for assoc in rt.get("Associations", []):
            if not assoc.get("Main", False):
                try:
                    ec2.disassociate_route_table(AssociationId=assoc["RouteTableAssociationId"])
                except ClientError:
                    pass
        try:
            ec2.delete_route_table(RouteTableId=rt["RouteTableId"])
            print(f"  Deleted: {rt['RouteTableId']}")
        except ClientError:
            pass

    # IGW + VPC
    print("[10/10] IGW & VPC...")
    igws = ec2.describe_internet_gateways(Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]}])
    for igw in igws["InternetGateways"]:
        for att in igw.get("Attachments", []):
            ec2.detach_internet_gateway(InternetGatewayId=igw["InternetGatewayId"], VpcId=att["VpcId"])
        ec2.delete_internet_gateway(InternetGatewayId=igw["InternetGatewayId"])
        print(f"  Deleted IGW: {igw['InternetGatewayId']}")

    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Lab", "Values": ["NET-004"]}])
    for vpc in vpcs["Vpcs"]:
        try:
            ec2.delete_vpc(VpcId=vpc["VpcId"])
            print(f"  Deleted VPC: {vpc['VpcId']}")
        except ClientError as e:
            print(f"  VPC delete failed: {e}")

    print(f"\n{'='*60}")
    print(" Teardown complete!")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    commands = {"deploy": deploy, "test": test, "status": status, "teardown": teardown}

    if len(sys.argv) < 2 or sys.argv[1] not in commands:
        print(f"Usage: python {sys.argv[0]} [{' | '.join(commands.keys())}]")
        print()
        print("  deploy   — Create all infra + run cert tests (full lab)")
        print("  test     — Generate certs + test TLS Inspection Config (infra must exist)")
        print("  status   — Show current state of lab resources")
        print("  teardown — Delete all lab resources")
        sys.exit(1)

    commands[sys.argv[1]]()
