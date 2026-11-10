#!/usr/bin/env python3
"""
NET-029 lab — AWS Network Firewall + Route 53 resolved IP validation.

Builds the full test bed in the "before" state: resolved IP validation is OFF,
and a classic SNI-only allowlist is active. Tests are run later, one by one.

    spoke-a 10.91.0.0/16  (associated with DNS FW rule group) ─┐
    spoke-b 10.92.0.0/16  (NOT associated)                     ├─ TGW ─> inspection 10.90.0.0/16
    onprem  192.168.0.0/16 (corp DNS -> Resolver inbound EP)  ─┘         tgw-sub -> NFW -> NAT -> IGW
                                                                         dns-sub: Resolver inbound endpoint
    default VPC 172.31.0.0/16: target-partner + target-evil (public IPs, nginx 443/80, sshd 22)
    PHZ "lab": sftp/api/short.partner.lab, files.partner.lab (CNAME), edge.cdn.lab, shared.lab, evil.lab

Usage:
    python3 deploy_lab.py deploy   [--profile lab] [--region us-east-2]
    python3 deploy_lab.py status
    python3 deploy_lab.py teardown

Cost: about USD 1.1/hour (1 NFW endpoint, 1 NAT GW, 4 TGW attachments, Resolver inbound
endpoint, 9 interface endpoints, 6 t3.micro). Run teardown when you finish.
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PREFIX = "net029"
TAGS = [{"Key": "Project", "Value": "NET-029"}]
STATE = Path(__file__).with_name("resources.json")

CIDR = {"insp": "10.90.0.0/16", "a": "10.91.0.0/16", "b": "10.92.0.0/16", "onprem": "192.168.0.0/16"}
SUBNETS = {
    "insp": {"tgw": "10.90.1.0/24", "fw": "10.90.2.0/24", "pub": "10.90.3.0/24", "dns": "10.90.4.0/24"},
    "a": {"wl": "10.91.1.0/24"},
    "b": {"wl": "10.92.1.0/24"},
    "onprem": {"wl": "192.168.1.0/24"},
}
CLIENT_NETS = [CIDR["a"], CIDR["b"], CIDR["onprem"]]
HOME_NET = [CIDR["insp"], CIDR["a"], CIDR["b"], CIDR["onprem"]]
INBOUND_IPS = ["10.90.4.10", "10.90.4.11"]
CORP_DNS_IP = "192.168.1.53"
AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"

BASELINE_RULES = """\
# NET-029 baseline ("before"): classic SNI-only allowlist on TCP 443. SSH is not filtered by domain.
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; content:"sftp.partner.lab"; nocase; startswith; endswith; msg:"classic SNI allow sftp.partner.lab"; flow:to_server, established; sid:1001; rev:1;)
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; content:"api.partner.lab"; nocase; startswith; endswith; msg:"classic SNI allow api.partner.lab"; flow:to_server, established; sid:1002; rev:1;)
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; content:"example.com"; nocase; startswith; endswith; msg:"classic SNI allow example.com"; flow:to_server, established; sid:1003; rev:1;)
drop tls $HOME_NET any -> $EXTERNAL_NET 443 (msg:"TLS SNI not in allowlist"; flow:to_server, established; sid:1099; rev:1;)
"""

CLIENT_USERDATA = """#!/bin/bash
for i in $(seq 1 30); do
  dnf install -y bind-utils nmap-ncat openssh-clients >>/var/log/net029-bootstrap.log 2>&1 && break
  sleep 20
done
echo ready > /var/tmp/net029-ready
"""

CORP_DNS_USERDATA = f"""#!/bin/bash
# Simulated corporate DNS: internet names -> Route 53 Resolver inbound endpoint, amazonaws.com -> local VPC resolver (SSM endpoints)
mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNS=192.168.0.2\nDNSStubListener=no\n' > /etc/systemd/resolved.conf.d/net029.conf
systemctl restart systemd-resolved
rm -f /etc/resolv.conf && echo "nameserver 192.168.0.2" > /etc/resolv.conf
dnf install -y unbound bind-utils >/var/log/net029-bootstrap.log 2>&1
cat > /etc/unbound/unbound.conf <<'EOF'
server:
  interface: 0.0.0.0
  access-control: 192.168.0.0/16 allow
  access-control: 127.0.0.0/8 allow
  module-config: "iterator"
  do-ip6: no
  verbosity: 1
  log-queries: yes
  logfile: "/var/log/unbound.log"
forward-zone:
  name: "amazonaws.com."
  forward-addr: 192.168.0.2
forward-zone:
  name: "."
  forward-addr: {INBOUND_IPS[0]}
  forward-addr: {INBOUND_IPS[1]}
EOF
touch /var/log/unbound.log && chown unbound:unbound /var/log/unbound.log
systemctl enable --now unbound
echo ready > /var/tmp/net029-ready
"""


def target_userdata(role: str, cn: str) -> str:
    return f"""#!/bin/bash
dnf install -y nginx openssl >/var/log/net029-bootstrap.log 2>&1
mkdir -p /etc/nginx/tls
openssl req -x509 -nodes -newkey rsa:2048 -days 30 -subj "/CN={cn}" \\
  -keyout /etc/nginx/tls/key.pem -out /etc/nginx/tls/cert.pem
cat > /etc/nginx/nginx.conf <<'EOF'
user nginx;
worker_processes 1;
events {{ worker_connections 256; }}
http {{
  access_log /var/log/nginx/access.log;
  server {{
    listen 443 ssl default_server;
    ssl_certificate /etc/nginx/tls/cert.pem;
    ssl_certificate_key /etc/nginx/tls/key.pem;
    location / {{ default_type text/plain; return 200 "{role} tls sni=$ssl_server_name"; }}
  }}
  server {{
    listen 80 default_server;
    location / {{ default_type text/plain; return 200 "{role} http host=$host"; }}
  }}
}}
EOF
systemctl enable --now nginx
echo ready > /var/tmp/net029-ready
"""


# ---------------------------------------------------------------- helpers
class Lab:
    def __init__(self, profile, region):
        self.region = region
        s = boto3.Session(profile_name=profile, region_name=region)
        self.ec2 = s.client("ec2")
        self.nfw = s.client("network-firewall")
        self.r53 = s.client("route53")
        self.rslvr = s.client("route53resolver")
        self.iam = s.client("iam")
        self.ssm = s.client("ssm")
        self.logs = s.client("logs")
        self.sts = s.client("sts")
        self.az = f"{region}a"
        self.R = json.loads(STATE.read_text()) if STATE.exists() else {}

    def save(self):
        STATE.write_text(json.dumps(self.R, indent=2))

    def once(self, key, fn):
        """Create a resource only if it isn't recorded yet (makes deploy resumable)."""
        if key not in self.R:
            self.R[key] = fn()
            self.save()
        return self.R[key]


def cli(cmd):
    print(f"    $ aws {cmd}")


def step(msg):
    print(f"\n== {msg}")


def tagspec(rtype, name):
    return [{"ResourceType": rtype, "Tags": TAGS + [{"Key": "Name", "Value": f"{PREFIX}-{name}"}]}]


def wait_until(desc, fn, timeout=1800, every=15):
    t0 = time.time()
    while True:
        ok, state = fn()
        if ok:
            print(f"    {desc}: {state}")
            return
        if time.time() - t0 > timeout:
            sys.exit(f"Timeout waiting for {desc} (last state: {state})")
        print(f"    {desc}: {state} ... waiting", flush=True)
        time.sleep(every)


# ---------------------------------------------------------------- deploy
def deploy(L: Lab):
    acct = L.sts.get_caller_identity()["Account"]
    print(f"Account {acct} · Region {L.region} · AZ {L.az}")
    R = L.R

    step("IAM role + instance profile for SSM")
    def mk_role():
        name = f"{PREFIX}-lab-ssm"
        cli(f"iam create-role --role-name {name} --assume-role-policy-document file://ec2-trust.json")
        L.iam.create_role(RoleName=name, Tags=TAGS, AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}))
        L.iam.attach_role_policy(RoleName=name, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        L.iam.create_instance_profile(InstanceProfileName=name)
        L.iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
        return name
    L.once("iam_profile", mk_role)

    step("VPCs and subnets")
    for v, cidr in CIDR.items():
        def mk_vpc(v=v, cidr=cidr):
            cli(f"ec2 create-vpc --cidr-block {cidr}")
            vid = L.ec2.create_vpc(CidrBlock=cidr, TagSpecifications=tagspec("vpc", v))["Vpc"]["VpcId"]
            L.ec2.get_waiter("vpc_available").wait(VpcIds=[vid])
            L.ec2.modify_vpc_attribute(VpcId=vid, EnableDnsSupport={"Value": True})
            L.ec2.modify_vpc_attribute(VpcId=vid, EnableDnsHostnames={"Value": True})
            return vid
        L.once(f"vpc_{v}", mk_vpc)
        for s, scidr in SUBNETS[v].items():
            L.once(f"sub_{v}_{s}", lambda v=v, s=s, scidr=scidr: (
                cli(f"ec2 create-subnet --vpc-id {R[f'vpc_{v}']} --cidr-block {scidr} --availability-zone {L.az}"),
                L.ec2.create_subnet(VpcId=R[f"vpc_{v}"], CidrBlock=scidr, AvailabilityZone=L.az,
                                    TagSpecifications=tagspec("subnet", f"{v}-{s}"))["Subnet"]["SubnetId"])[1])
    print("    done")

    step("Internet gateway + NAT gateway (inspection VPC)")
    def mk_igw():
        cli(f"ec2 create-internet-gateway && ec2 attach-internet-gateway --vpc-id {R['vpc_insp']}")
        igw = L.ec2.create_internet_gateway(TagSpecifications=tagspec("internet-gateway", "igw"))["InternetGateway"]["InternetGatewayId"]
        L.ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=R["vpc_insp"])
        return igw
    L.once("igw", mk_igw)
    L.once("eip_nat", lambda: L.ec2.allocate_address(Domain="vpc", TagSpecifications=tagspec("elastic-ip", "nat"))["AllocationId"])
    def mk_nat():
        cli(f"ec2 create-nat-gateway --subnet-id {R['sub_insp_pub']} --allocation-id {R['eip_nat']}")
        return L.ec2.create_nat_gateway(SubnetId=R["sub_insp_pub"], AllocationId=R["eip_nat"],
                                        TagSpecifications=tagspec("natgateway", "nat"))["NatGateway"]["NatGatewayId"]
    L.once("nat", mk_nat)
    L.ec2.get_waiter("nat_gateway_available").wait(NatGatewayIds=[R["nat"]])
    R["nat_public_ip"] = L.ec2.describe_addresses(AllocationIds=[R["eip_nat"]])["Addresses"][0]["PublicIp"]
    L.save()
    print(f"    NAT public IP {R['nat_public_ip']}")

    step("Transit Gateway, attachments and route tables")
    def mk_tgw():
        cli("ec2 create-transit-gateway --options DefaultRouteTableAssociation=disable,DefaultRouteTablePropagation=disable")
        return L.ec2.create_transit_gateway(
            Description="NET-029 lab", TagSpecifications=tagspec("transit-gateway", "tgw"),
            Options={"DefaultRouteTableAssociation": "disable", "DefaultRouteTablePropagation": "disable",
                     "DnsSupport": "enable", "VpnEcmpSupport": "enable"})["TransitGateway"]["TransitGatewayId"]
    L.once("tgw", mk_tgw)
    wait_until("TGW", lambda: (lambda s: (s == "available", s))(
        L.ec2.describe_transit_gateways(TransitGatewayIds=[R["tgw"]])["TransitGateways"][0]["State"]), every=20)
    att_subnet = {"insp": R["sub_insp_tgw"], "a": R["sub_a_wl"], "b": R["sub_b_wl"], "onprem": R["sub_onprem_wl"]}
    for v in CIDR:
        L.once(f"tgwatt_{v}", lambda v=v: (
            cli(f"ec2 create-transit-gateway-vpc-attachment --transit-gateway-id {R['tgw']} --vpc-id {R[f'vpc_{v}']} --subnet-ids {att_subnet[v]}"),
            L.ec2.create_transit_gateway_vpc_attachment(
                TransitGatewayId=R["tgw"], VpcId=R[f"vpc_{v}"], SubnetIds=[att_subnet[v]],
                Options={"ApplianceModeSupport": "enable" if v == "insp" else "disable"},
                TagSpecifications=tagspec("transit-gateway-attachment", v))["TransitGatewayVpcAttachment"]["TransitGatewayAttachmentId"])[1])
    for v in CIDR:
        wait_until(f"TGW attachment {v}", lambda v=v: (lambda s: (s == "available", s))(
            L.ec2.describe_transit_gateway_vpc_attachments(TransitGatewayAttachmentIds=[R[f"tgwatt_{v}"]])
            ["TransitGatewayVpcAttachments"][0]["State"]), every=20)
    for rt in ("spoke", "insp"):
        L.once(f"tgwrt_{rt}", lambda rt=rt: L.ec2.create_transit_gateway_route_table(
            TransitGatewayId=R["tgw"], TagSpecifications=tagspec("transit-gateway-route-table", f"rt-{rt}"))
            ["TransitGatewayRouteTable"]["TransitGatewayRouteTableId"])
    for rt in ("spoke", "insp"):
        wait_until(f"TGW route table {rt}", lambda rt=rt: (lambda s: (s == "available", s))(
            L.ec2.describe_transit_gateway_route_tables(TransitGatewayRouteTableIds=[R[f"tgwrt_{rt}"]])
            ["TransitGatewayRouteTables"][0]["State"]), every=10)
    def tgw_routing():
        for v in ("a", "b", "onprem"):
            L.ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=R["tgwrt_spoke"], TransitGatewayAttachmentId=R[f"tgwatt_{v}"])
            L.ec2.enable_transit_gateway_route_table_propagation(TransitGatewayRouteTableId=R["tgwrt_insp"], TransitGatewayAttachmentId=R[f"tgwatt_{v}"])
        L.ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=R["tgwrt_insp"], TransitGatewayAttachmentId=R["tgwatt_insp"])
        cli(f"ec2 create-transit-gateway-route --transit-gateway-route-table-id {R['tgwrt_spoke']} --destination-cidr-block 0.0.0.0/0 --transit-gateway-attachment-id {R['tgwatt_insp']}")
        L.ec2.create_transit_gateway_route(TransitGatewayRouteTableId=R["tgwrt_spoke"], DestinationCidrBlock="0.0.0.0/0",
                                           TransitGatewayAttachmentId=R["tgwatt_insp"])
        return True
    L.once("tgw_routing", tgw_routing)

    step("Network Firewall: log groups, baseline rule group, policy, firewall")
    for lt in ("flow", "alert"):
        def mk_lg(lt=lt):
            name = f"/nfw/{PREFIX}/{lt}"
            cli(f"logs create-log-group --log-group-name {name}")
            try:
                L.logs.create_log_group(logGroupName=name, tags={"Project": "NET-029"})
            except L.logs.exceptions.ResourceAlreadyExistsException:
                pass
            L.logs.put_retention_policy(logGroupName=name, retentionInDays=7)
            return name
        L.once(f"lg_{lt}", mk_lg)
    def mk_rg():
        cli(f"network-firewall create-rule-group --rule-group-name {PREFIX}-baseline-classic-sni --type STATEFUL --capacity 100 --rules file://baseline.rules")
        return L.nfw.create_rule_group(
            RuleGroupName=f"{PREFIX}-baseline-classic-sni", Type="STATEFUL", Capacity=100, Tags=TAGS,
            Description="NET-029 baseline: classic SNI-only allowlist",
            RuleGroup={"RulesSource": {"RulesString": BASELINE_RULES},
                       "StatefulRuleOptions": {"RuleOrder": "DEFAULT_ACTION_ORDER"}})["RuleGroupResponse"]["RuleGroupArn"]
    L.once("nfw_rg_baseline", mk_rg)
    def mk_policy():
        cli(f"network-firewall create-firewall-policy --firewall-policy-name {PREFIX}-policy --firewall-policy file://policy.json")
        return L.nfw.create_firewall_policy(
            FirewallPolicyName=f"{PREFIX}-policy", Tags=TAGS, Description="NET-029 lab policy",
            FirewallPolicy={
                "StatelessDefaultActions": ["aws:forward_to_sfe"],
                "StatelessFragmentDefaultActions": ["aws:forward_to_sfe"],
                "StatefulRuleGroupReferences": [{"ResourceArn": R["nfw_rg_baseline"]}],
                "StatefulEngineOptions": {"RuleOrder": "DEFAULT_ACTION_ORDER", "StreamExceptionPolicy": "DROP"},
                "PolicyVariables": {"RuleVariables": {"HOME_NET": {"Definition": HOME_NET}}},
            })["FirewallPolicyResponse"]["FirewallPolicyArn"]
    L.once("nfw_policy", mk_policy)
    def mk_fw():
        cli(f"network-firewall create-firewall --firewall-name {PREFIX}-fw --vpc-id {R['vpc_insp']} --subnet-mappings SubnetId={R['sub_insp_fw']}")
        return L.nfw.create_firewall(
            FirewallName=f"{PREFIX}-fw", FirewallPolicyArn=R["nfw_policy"], VpcId=R["vpc_insp"],
            SubnetMappings=[{"SubnetId": R["sub_insp_fw"]}], DeleteProtection=False, Tags=TAGS)["Firewall"]["FirewallArn"]
    L.once("nfw_fw", mk_fw)
    def fw_state():
        st = L.nfw.describe_firewall(FirewallArn=R["nfw_fw"])["FirewallStatus"]
        return st["Status"] == "READY", st["Status"]
    wait_until("Firewall", fw_state, timeout=2400, every=30)
    R["nfw_endpoint"] = L.nfw.describe_firewall(FirewallArn=R["nfw_fw"])["FirewallStatus"]["SyncStates"][L.az]["Attachment"]["EndpointId"]
    L.save()
    print(f"    endpoint {R['nfw_endpoint']}")
    def logging():
        flow = {"LogType": "FLOW", "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": R["lg_flow"]}}
        alert = {"LogType": "ALERT", "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": R["lg_alert"]}}
        cli(f"network-firewall update-logging-configuration --firewall-arn ... (FLOW, then FLOW+ALERT)")
        L.nfw.update_logging_configuration(FirewallArn=R["nfw_fw"], LoggingConfiguration={"LogDestinationConfigs": [flow]})
        L.nfw.update_logging_configuration(FirewallArn=R["nfw_fw"], LoggingConfiguration={"LogDestinationConfigs": [flow, alert]})
        return True
    L.once("nfw_logging", logging)

    step("VPC route tables")
    def rt(name, vpc, subnets, routes):
        def mk():
            rid = L.ec2.create_route_table(VpcId=vpc, TagSpecifications=tagspec("route-table", name))["RouteTable"]["RouteTableId"]
            for s in subnets:
                L.ec2.associate_route_table(RouteTableId=rid, SubnetId=s)
            for dst, kind, target in routes:
                cli(f"ec2 create-route --route-table-id {rid} --destination-cidr-block {dst} --{kind} {target}")
                L.ec2.create_route(RouteTableId=rid, DestinationCidrBlock=dst, **{
                    "gateway-id": {"GatewayId": target}, "nat-gateway-id": {"NatGatewayId": target},
                    "transit-gateway-id": {"TransitGatewayId": target}, "vpc-endpoint-id": {"VpcEndpointId": target}}[kind])
            return rid
        return L.once(f"rt_{name}", mk)
    ep, tgw = R["nfw_endpoint"], R["tgw"]
    rt("insp-tgw", R["vpc_insp"], [R["sub_insp_tgw"]], [("0.0.0.0/0", "vpc-endpoint-id", ep)])
    rt("insp-fw", R["vpc_insp"], [R["sub_insp_fw"]],
       [("0.0.0.0/0", "nat-gateway-id", R["nat"])] + [(c, "transit-gateway-id", tgw) for c in CLIENT_NETS])
    rt("insp-pub", R["vpc_insp"], [R["sub_insp_pub"]],
       [("0.0.0.0/0", "gateway-id", R["igw"])] + [(c, "vpc-endpoint-id", ep) for c in CLIENT_NETS])
    rt("insp-dns", R["vpc_insp"], [R["sub_insp_dns"]], [(c, "transit-gateway-id", tgw) for c in CLIENT_NETS])
    for v in ("a", "b", "onprem"):
        rt(f"{v}-wl", R[f"vpc_{v}"], [R[f"sub_{v}_wl"]], [("0.0.0.0/0", "transit-gateway-id", tgw)])

    step("Security groups")
    def sg(name, vpc, perms):
        def mk():
            gid = L.ec2.create_security_group(GroupName=f"{PREFIX}-{name}", Description=f"NET-029 {name}", VpcId=vpc,
                                              TagSpecifications=tagspec("security-group", name))["GroupId"]
            if perms:
                L.ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=perms)
            return gid
        return L.once(f"sg_{name}", mk)
    def p(proto, port, cidr):
        return {"IpProtocol": proto, "FromPort": port, "ToPort": port, "IpRanges": [{"CidrIp": cidr}]}
    for v in ("a", "b", "onprem"):
        sg(f"{v}-endpoints", R[f"vpc_{v}"], [p("tcp", 443, CIDR[v])])
        sg(f"{v}-clients", R[f"vpc_{v}"], [])
    sg("onprem-corpdns", R["vpc_onprem"], [p("udp", 53, CIDR["onprem"]), p("tcp", 53, CIDR["onprem"])])
    sg("insp-inbound", R["vpc_insp"], [p("udp", 53, CIDR["onprem"]), p("tcp", 53, CIDR["onprem"])])
    default_vpc = L.ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    R["default_vpc"] = default_vpc
    nat_cidr = f"{R['nat_public_ip']}/32"
    sg("targets", default_vpc, [p("tcp", 22, nat_cidr), p("tcp", 80, nat_cidr), p("tcp", 443, nat_cidr)])

    step("VPC endpoints (SSM interface + S3 gateway) so instance access never crosses the firewall")
    for v in ("a", "b", "onprem"):
        for svc in ("ssm", "ssmmessages", "ec2messages"):
            L.once(f"vpce_{v}_{svc}", lambda v=v, svc=svc: (
                cli(f"ec2 create-vpc-endpoint --vpc-endpoint-type Interface --service-name com.amazonaws.{L.region}.{svc} --vpc-id {R[f'vpc_{v}']} --private-dns-enabled"),
                L.ec2.create_vpc_endpoint(VpcEndpointType="Interface", VpcId=R[f"vpc_{v}"],
                                          ServiceName=f"com.amazonaws.{L.region}.{svc}", SubnetIds=[R[f"sub_{v}_wl"]],
                                          SecurityGroupIds=[R[f"sg_{v}-endpoints"]], PrivateDnsEnabled=True,
                                          TagSpecifications=tagspec("vpc-endpoint", f"{v}-{svc}"))["VpcEndpoint"]["VpcEndpointId"])[1])
        L.once(f"vpce_{v}_s3", lambda v=v: L.ec2.create_vpc_endpoint(
            VpcEndpointType="Gateway", VpcId=R[f"vpc_{v}"], ServiceName=f"com.amazonaws.{L.region}.s3",
            RouteTableIds=[R[f"rt_{v}-wl"]], TagSpecifications=tagspec("vpc-endpoint", f"{v}-s3"))["VpcEndpoint"]["VpcEndpointId"])
    print("    done")

    step("Route 53 Resolver DNS Firewall anchor rule group (associated with spoke-a + inspection, NOT spoke-b)")
    def mk_dnsfw():
        cli(f"route53resolver create-firewall-rule-group --name {PREFIX}-anchor")
        return L.rslvr.create_firewall_rule_group(CreatorRequestId=str(uuid.uuid4()), Name=f"{PREFIX}-anchor",
                                                  Tags=[{"Key": "Project", "Value": "NET-029"}])["FirewallRuleGroup"]["Id"]
    L.once("dnsfw_rg", mk_dnsfw)
    for v, prio in (("a", 101), ("insp", 102)):
        L.once(f"dnsfw_assoc_{v}", lambda v=v, prio=prio: (
            cli(f"route53resolver associate-firewall-rule-group --firewall-rule-group-id {R['dnsfw_rg']} --vpc-id {R[f'vpc_{v}']} --priority {prio}"),
            L.rslvr.associate_firewall_rule_group(CreatorRequestId=str(uuid.uuid4()), FirewallRuleGroupId=R["dnsfw_rg"],
                                                  VpcId=R[f"vpc_{v}"], Priority=prio, Name=f"{PREFIX}-anchor-{v}")
            ["FirewallRuleGroupAssociation"]["Id"])[1])
    for v in ("a", "insp"):
        wait_until(f"DNS FW association {v}", lambda v=v: (lambda s: (s == "COMPLETE", s))(
            L.rslvr.get_firewall_rule_group_association(FirewallRuleGroupAssociationId=R[f"dnsfw_assoc_{v}"])
            ["FirewallRuleGroupAssociation"]["Status"]), every=10)

    step("Route 53 Resolver inbound endpoint (inspection VPC) for the simulated on-prem DNS")
    def mk_inbound():
        cli(f"route53resolver create-resolver-endpoint --direction INBOUND --ip-addresses SubnetId={R['sub_insp_dns']},Ip={INBOUND_IPS[0]} SubnetId={R['sub_insp_dns']},Ip={INBOUND_IPS[1]}")
        return L.rslvr.create_resolver_endpoint(
            CreatorRequestId=str(uuid.uuid4()), Name=f"{PREFIX}-inbound", Direction="INBOUND",
            SecurityGroupIds=[R["sg_insp-inbound"]], Tags=[{"Key": "Project", "Value": "NET-029"}],
            IpAddresses=[{"SubnetId": R["sub_insp_dns"], "Ip": ip} for ip in INBOUND_IPS])["ResolverEndpoint"]["Id"]
    L.once("inbound_ep", mk_inbound)
    wait_until("Inbound endpoint", lambda: (lambda s: (s == "OPERATIONAL", s))(
        L.rslvr.get_resolver_endpoint(ResolverEndpointId=R["inbound_ep"])["ResolverEndpoint"]["Status"]), every=15)

    step("On-prem DHCP options -> corp DNS")
    def mk_dhcp():
        cli(f"ec2 create-dhcp-options --dhcp-configurations Key=domain-name-servers,Values={CORP_DNS_IP}")
        did = L.ec2.create_dhcp_options(DhcpConfigurations=[{"Key": "domain-name-servers", "Values": [CORP_DNS_IP]}],
                                        TagSpecifications=tagspec("dhcp-options", "onprem"))["DhcpOptions"]["DhcpOptionsId"]
        L.ec2.associate_dhcp_options(DhcpOptionsId=did, VpcId=R["vpc_onprem"])
        return did
    L.once("dhcp_onprem", mk_dhcp)

    step("EC2 instances (targets in default VPC, clients, corp DNS)")
    ami = L.ssm.get_parameter(Name=AMI_PARAM)["Parameter"]["Value"]
    default_sub = L.ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [default_vpc]},
                                                  {"Name": "availability-zone", "Values": [L.az]},
                                                  {"Name": "default-for-az", "Values": ["true"]}])["Subnets"][0]["SubnetId"]

    def launch(name, subnet, sg_id, userdata, public=False, private_ip=None):
        nic = {"DeviceIndex": 0, "SubnetId": subnet, "Groups": [sg_id], "AssociatePublicIpAddress": public}
        if private_ip:
            nic["PrivateIpAddress"] = private_ip
        cli(f"ec2 run-instances --image-id {ami} --instance-type t3.micro --subnet-id {subnet} --iam-instance-profile Name={R['iam_profile']} (name {PREFIX}-{name})")
        for _ in range(12):
            try:
                return L.ec2.run_instances(
                    ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1, NetworkInterfaces=[nic],
                    IamInstanceProfile={"Name": R["iam_profile"]}, UserData=userdata,
                    MetadataOptions={"HttpTokens": "required"},
                    TagSpecifications=tagspec("instance", name))["Instances"][0]["InstanceId"]
            except ClientError as e:
                if "iamInstanceProfile" in str(e) or "Invalid IAM Instance Profile" in str(e):
                    time.sleep(10)
                    continue
                raise
        sys.exit("Instance profile never became usable")

    L.once("i_target_partner", lambda: launch("target-partner", default_sub, R["sg_targets"],
                                              target_userdata("PARTNER", "sftp.partner.lab"), public=True))
    L.once("i_target_evil", lambda: launch("target-evil", default_sub, R["sg_targets"],
                                           target_userdata("EVIL", "evil.lab"), public=True))
    L.once("i_corpdns", lambda: launch("onprem-corpdns", R["sub_onprem_wl"], R["sg_onprem-corpdns"],
                                       CORP_DNS_USERDATA, private_ip=CORP_DNS_IP))
    for v in ("a", "b", "onprem"):
        L.once(f"i_client_{v}", lambda v=v: launch(f"client-{v}", R[f"sub_{v}_wl"], R[f"sg_{v}-clients"], CLIENT_USERDATA))
    ids = [R[k] for k in R if k.startswith("i_")]
    L.ec2.get_waiter("instance_running").wait(InstanceIds=ids)
    for k in ("i_target_partner", "i_target_evil"):
        R[k.replace("i_", "ip_")] = L.ec2.describe_instances(InstanceIds=[R[k]])["Reservations"][0]["Instances"][0]["PublicIpAddress"]
    L.save()
    print(f"    target-partner {R['ip_target_partner']} · target-evil {R['ip_target_evil']}")

    step("Private hosted zone 'lab' (spoke-a, spoke-b, inspection)")
    def mk_phz():
        cli(f"route53 create-hosted-zone --name lab --vpc VPCRegion={L.region},VPCId={R['vpc_a']} --hosted-zone-config PrivateZone=true")
        zid = L.r53.create_hosted_zone(Name="lab", CallerReference=str(uuid.uuid4()),
                                       VPC={"VPCRegion": L.region, "VPCId": R["vpc_a"]},
                                       HostedZoneConfig={"Comment": "NET-029 lab", "PrivateZone": True})["HostedZone"]["Id"]
        for v in ("b", "insp"):
            L.r53.associate_vpc_with_hosted_zone(HostedZoneId=zid, VPC={"VPCRegion": L.region, "VPCId": R[f"vpc_{v}"]})
        return zid
    L.once("phz", mk_phz)
    P, E = R["ip_target_partner"], R["ip_target_evil"]
    records = [("sftp.partner.lab", "A", P, 300), ("api.partner.lab", "A", P, 300), ("short.partner.lab", "A", P, 30),
               ("edge.cdn.lab", "A", P, 300), ("files.partner.lab", "CNAME", "edge.cdn.lab", 300),
               ("shared.lab", "A", P, 300), ("evil.lab", "A", E, 300)]
    cli(f"route53 change-resource-record-sets --hosted-zone-id {R['phz']} --change-batch file://records.json")
    L.r53.change_resource_record_sets(HostedZoneId=R["phz"], ChangeBatch={"Changes": [
        {"Action": "UPSERT", "ResourceRecordSet": {"Name": n, "Type": t, "TTL": ttl, "ResourceRecords": [{"Value": v}]}}
        for n, t, v, ttl in records]})
    R["phz_records"] = records
    L.save()
    for n, t, v, ttl in records:
        print(f"    {n:<20} {t:<5} {v:<16} TTL {ttl}")

    print("\nDeploy complete. Resolved IP validation is OFF (baseline). Next: python3 deploy_lab.py status")


# ---------------------------------------------------------------- status
def status(L: Lab):
    R = L.R
    if not R:
        sys.exit("No resources.json — nothing deployed.")
    step("Network Firewall")
    fw = L.nfw.describe_firewall(FirewallArn=R["nfw_fw"])
    print(f"    firewall {fw['FirewallStatus']['Status']} · endpoint {R.get('nfw_endpoint')}")
    pol = L.nfw.describe_firewall_policy(FirewallPolicyArn=R["nfw_policy"])
    keys = sorted(pol["FirewallPolicy"].keys())
    print(f"    policy {pol['FirewallPolicyResponse']['FirewallPolicyStatus']} · keys {keys}")
    print(f"    resolved IP validation: {'ON' if any('Resolved' in k for k in keys) else 'OFF (baseline)'}")
    lc = L.nfw.describe_logging_configuration(FirewallArn=R["nfw_fw"])["LoggingConfiguration"]["LogDestinationConfigs"]
    print(f"    logging {[c['LogType'] for c in lc]}")
    step("DNS")
    for v in ("a", "insp"):
        a = L.rslvr.get_firewall_rule_group_association(FirewallRuleGroupAssociationId=R[f"dnsfw_assoc_{v}"])["FirewallRuleGroupAssociation"]
        print(f"    DNS FW association {v}: {a['Status']}")
    print(f"    spoke-b: intentionally NOT associated")
    print(f"    inbound endpoint: {L.rslvr.get_resolver_endpoint(ResolverEndpointId=R['inbound_ep'])['ResolverEndpoint']['Status']} {INBOUND_IPS}")
    step("Transit Gateway attachments")
    for v in CIDR:
        s = L.ec2.describe_transit_gateway_vpc_attachments(TransitGatewayAttachmentIds=[R[f"tgwatt_{v}"]])["TransitGatewayVpcAttachments"][0]["State"]
        print(f"    {v:<7} {s}")
    step("Instances (SSM)")
    ids = {k: R[k] for k in R if k.startswith("i_")}
    info = {i["InstanceId"]: i for i in L.ssm.describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": list(ids.values())}])["InstanceInformationList"]}
    for k, iid in ids.items():
        st = L.ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        print(f"    {k[2:]:<16} {iid} {st['State']['Name']:<8} priv {st.get('PrivateIpAddress',''):<14} "
              f"pub {st.get('PublicIpAddress','-'):<15} ssm {info.get(iid, {}).get('PingStatus', 'not registered')}")
    step("CLI/SDK support for the feature")
    import botocore
    has = "Route53ResolvedIPValidation" in L.nfw.meta.service_model.shape_names
    print(f"    botocore {botocore.__version__}: Route53ResolvedIPValidation {'supported' if has else 'NOT in model — upgrade boto3/AWS CLI before tests'}")


# ---------------------------------------------------------------- teardown
def teardown(L: Lab):
    R = L.R
    if not R:
        sys.exit("No resources.json — nothing to tear down.")
    acct = L.sts.get_caller_identity()["Account"]
    print(f"About to delete every NET-029 resource in account {acct}, region {L.region}.")
    if input("Type the account ID to confirm: ").strip() != acct:
        sys.exit("Aborted.")

    def tryit(desc, fn):
        try:
            print(f"    {desc}")
            fn()
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if "NotFound" in code or "NoSuch" in code or code in ("ResourceNotFoundException", "InvalidParameterValue"):
                return
            print(f"      ! {code}: {e.response['Error']['Message']}")

    step("Instances")
    ids = [R[k] for k in R if k.startswith("i_")]
    if ids:
        tryit("terminate instances", lambda: L.ec2.terminate_instances(InstanceIds=ids))
        tryit("wait terminated", lambda: L.ec2.get_waiter("instance_terminated").wait(InstanceIds=ids))
    step("DNS")
    if "phz" in R:
        def del_phz():
            for n, t, v, ttl in R.get("phz_records", []):
                tryit(f"delete {n}", lambda n=n, t=t, v=v, ttl=ttl: L.r53.change_resource_record_sets(
                    HostedZoneId=R["phz"], ChangeBatch={"Changes": [{"Action": "DELETE", "ResourceRecordSet": {
                        "Name": n, "Type": t, "TTL": ttl, "ResourceRecords": [{"Value": v}]}}]}))
            for v in ("b", "insp"):
                tryit(f"disassociate PHZ {v}", lambda v=v: L.r53.disassociate_vpc_from_hosted_zone(
                    HostedZoneId=R["phz"], VPC={"VPCRegion": L.region, "VPCId": R[f"vpc_{v}"]}))
            L.r53.delete_hosted_zone(Id=R["phz"])
        tryit("delete PHZ", del_phz)
    if "dhcp_onprem" in R:
        tryit("reset DHCP options", lambda: L.ec2.associate_dhcp_options(DhcpOptionsId="default", VpcId=R["vpc_onprem"]))
        tryit("delete DHCP options", lambda: L.ec2.delete_dhcp_options(DhcpOptionsId=R["dhcp_onprem"]))
    if "inbound_ep" in R:
        tryit("delete inbound endpoint", lambda: L.rslvr.delete_resolver_endpoint(ResolverEndpointId=R["inbound_ep"]))
    for v in ("a", "insp"):
        if f"dnsfw_assoc_{v}" in R:
            tryit(f"disassociate DNS FW {v}", lambda v=v: L.rslvr.disassociate_firewall_rule_group(FirewallRuleGroupAssociationId=R[f"dnsfw_assoc_{v}"]))
    if "dnsfw_rg" in R:
        def del_rg():
            for _ in range(40):
                left = L.rslvr.list_firewall_rule_group_associations(FirewallRuleGroupId=R["dnsfw_rg"])["FirewallRuleGroupAssociations"]
                if not left:
                    break
                print(f"      waiting for {len(left)} association(s) to go away")
                time.sleep(15)
            L.rslvr.delete_firewall_rule_group(FirewallRuleGroupId=R["dnsfw_rg"])
        tryit("delete DNS FW rule group", del_rg)
    step("VPC endpoints")
    vpces = [R[k] for k in R if k.startswith("vpce_")]
    if vpces:
        tryit("delete endpoints", lambda: L.ec2.delete_vpc_endpoints(VpcEndpointIds=vpces))
    step("Network Firewall")
    if "nfw_fw" in R:
        tryit("logging FLOW only", lambda: L.nfw.update_logging_configuration(FirewallArn=R["nfw_fw"], LoggingConfiguration={
            "LogDestinationConfigs": [{"LogType": "FLOW", "LogDestinationType": "CloudWatchLogs", "LogDestination": {"logGroup": R["lg_flow"]}}]}))
        tryit("logging off", lambda: L.nfw.update_logging_configuration(FirewallArn=R["nfw_fw"], LoggingConfiguration={"LogDestinationConfigs": []}))
        tryit("delete firewall", lambda: L.nfw.delete_firewall(FirewallArn=R["nfw_fw"]))
        def gone():
            try:
                return False, L.nfw.describe_firewall(FirewallArn=R["nfw_fw"])["FirewallStatus"]["Status"]
            except L.nfw.exceptions.ResourceNotFoundException:
                return True, "deleted"
        wait_until("Firewall", gone, timeout=2400, every=30)
    if "nfw_policy" in R:
        tryit("delete policy", lambda: L.nfw.delete_firewall_policy(FirewallPolicyArn=R["nfw_policy"]))
    for k in [k for k in R if k.startswith("nfw_rg")]:
        tryit(f"delete rule group {k}", lambda k=k: L.nfw.delete_rule_group(RuleGroupArn=R[k]))
    for k in ("lg_flow", "lg_alert"):
        if k in R:
            tryit(f"delete log group {R[k]}", lambda k=k: L.logs.delete_log_group(logGroupName=R[k]))
    step("Transit Gateway")
    for v in CIDR:
        if f"tgwatt_{v}" in R:
            tryit(f"delete attachment {v}", lambda v=v: L.ec2.delete_transit_gateway_vpc_attachment(TransitGatewayAttachmentId=R[f"tgwatt_{v}"]))
    def atts_gone():
        atts = L.ec2.describe_transit_gateway_vpc_attachments(Filters=[{"Name": "transit-gateway-id", "Values": [R["tgw"]]}])["TransitGatewayVpcAttachments"]
        live = [a["State"] for a in atts if a["State"] not in ("deleted",)]
        return not live, live or "all deleted"
    if "tgw" in R:
        wait_until("TGW attachments", atts_gone, every=20)
        for rt in ("spoke", "insp"):
            if f"tgwrt_{rt}" in R:
                tryit(f"delete TGW route table {rt}", lambda rt=rt: L.ec2.delete_transit_gateway_route_table(TransitGatewayRouteTableId=R[f"tgwrt_{rt}"]))
        time.sleep(10)
        tryit("delete TGW", lambda: L.ec2.delete_transit_gateway(TransitGatewayId=R["tgw"]))
    step("NAT, IGW, EIP")
    if "nat" in R:
        tryit("delete NAT", lambda: L.ec2.delete_nat_gateway(NatGatewayId=R["nat"]))
        tryit("wait NAT deleted", lambda: L.ec2.get_waiter("nat_gateway_deleted").wait(NatGatewayIds=[R["nat"]]))
    if "eip_nat" in R:
        tryit("release EIP", lambda: L.ec2.release_address(AllocationId=R["eip_nat"]))
    if "igw" in R:
        tryit("detach IGW", lambda: L.ec2.detach_internet_gateway(InternetGatewayId=R["igw"], VpcId=R["vpc_insp"]))
        tryit("delete IGW", lambda: L.ec2.delete_internet_gateway(InternetGatewayId=R["igw"]))
    step("Route tables, security groups, subnets, VPCs")
    for k in [k for k in R if k.startswith("rt_")]:
        def del_rt(k=k):
            rt = L.ec2.describe_route_tables(RouteTableIds=[R[k]])["RouteTables"][0]
            for a in rt["Associations"]:
                if not a.get("Main"):
                    L.ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
            L.ec2.delete_route_table(RouteTableId=R[k])
        tryit(f"delete {k}", del_rt)
    # wait for endpoint ENIs before deleting SGs/subnets
    time.sleep(30)
    for k in [k for k in R if k.startswith("sg_")]:
        for _ in range(10):
            try:
                L.ec2.delete_security_group(GroupId=R[k])
                print(f"    delete {k}")
                break
            except ClientError as e:
                if e.response["Error"]["Code"] == "InvalidGroup.NotFound":
                    break
                time.sleep(15)
    for k in [k for k in R if k.startswith("sub_")]:
        tryit(f"delete {k}", lambda k=k: L.ec2.delete_subnet(SubnetId=R[k]))
    for v in CIDR:
        if f"vpc_{v}" in R:
            tryit(f"delete vpc {v}", lambda v=v: L.ec2.delete_vpc(VpcId=R[f"vpc_{v}"]))
    step("IAM")
    if "iam_profile" in R:
        n = R["iam_profile"]
        tryit("remove role from profile", lambda: L.iam.remove_role_from_instance_profile(InstanceProfileName=n, RoleName=n))
        tryit("delete profile", lambda: L.iam.delete_instance_profile(InstanceProfileName=n))
        tryit("detach policy", lambda: L.iam.detach_role_policy(RoleName=n, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"))
        tryit("delete role", lambda: L.iam.delete_role(RoleName=n))
    STATE.rename(STATE.with_suffix(".torn-down.json"))
    print("\nTeardown complete. Re-run status in the console to confirm nothing is left.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NET-029 lab")
    ap.add_argument("action", choices=["deploy", "status", "teardown"])
    ap.add_argument("--profile", default="lab338")
    ap.add_argument("--region", default="us-east-2")
    a = ap.parse_args()
    lab = Lab(a.profile, a.region)
    {"deploy": deploy, "status": status, "teardown": teardown}[a.action](lab)
