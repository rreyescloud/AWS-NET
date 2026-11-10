#!/usr/bin/env python3
"""NET-018 Phase 2 — Route 53 failover + weighted routing + HTTPS health checks, no EKS.

Three internet-facing ALBs stand in for the EKS ingresses: ALB-1 and ALB-2 in the primary
region (weighted 70/30 under use1.api), ALB-3 in the secondary (usw2.api). api.<zone> is a
failover pair that aliases the two regional names. Each ALB forwards to a tiny app on one
EC2 instance per region, so target health (ETH) and the /healthz health check can be
broken independently. DNS is read from the zone's authoritative name servers, so the zone
never has to be delegated.

deploy STEP            network | albs | dns | records | all — one layer at a time
status                 Targets, health checks, last failure reasons, DNS answers
watch                  Poll every 5 s and timestamp every change (Ctrl+C to stop)
split [N]              N queries of use1.api at the zone's name server, counted per ALB
break ALB MODE         hc       /healthz rule returns fixed 503 (targets stay healthy)
                       targets  app's /ready fails → targets unhealthy (/healthz still 200)
                       both     hc + targets
                       slow     /healthz takes 3 s (over the 2 s budget)
                       sg       remove the Route 53 health checker prefix list from the ALB SG
                       host     /healthz rule no longer matches the checker's Host header
                       tls13    listener accepts TLS 1.3 only
                       empty-tg add a listener rule that forwards to an empty target group
restore ALB            Undo every break on ALB
eth on|off             EvaluateTargetHealth on the SECONDARY failover record
iphc ALB               Create an IP-based health check pinned to one of ALB's current IPs
teardown               Delete everything and verify

Usage:
    python deploy_lab.py deploy network --profile lab338
"""

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

RESOURCE_FILE = Path(__file__).parent / "resources.json"

PREFIX = "NET-018"
SHORT = "NET018"
# The design uses us-east-1 / eu-west-1. The lab account's SCP only allows US regions.
PRIMARY, SECONDARY = "us-east-1", "us-west-2"
REGIONS = {PRIMARY: "10.18.0.0/16", SECONDARY: "10.28.0.0/16"}
REGIONAL = {PRIMARY: "use1", SECONDARY: "usw2"}
# alb → (region, weight, app port)
ALBS = {"alb1": (PRIMARY, 70, 8081), "alb2": (PRIMARY, 30, 8082), "alb3": (SECONDARY, 100, 8083)}
ZONE_CANDIDATES = ["net018-lab.example", "net018-lab.test"]   # never delegated
AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
INSTANCE_TYPE = "t4g.nano"
EC2_ROLE = f"{PREFIX}-ec2"
TLS_DEFAULT, TLS13_ONLY = "ELBSecurityPolicy-TLS13-1-2-2021-06", "ELBSecurityPolicy-TLS13-1-3-2021-06"
HC_INTERVAL, HC_THRESHOLD = 10, 3       # fast checks: failover in ~30 s + 60 s ALB alias TTL
TAGS = [{"Key": "Project", "Value": PREFIX}]

APP_PY = r'''
import http.server, json, os, socketserver, sys, time
PORT, NAME, REGION = int(sys.argv[1]), sys.argv[2], sys.argv[3]
F = "/opt/net018/" + NAME

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/ready":                      # ALB target group health check
            code = 503 if os.path.exists(F + ".ready-down") else 200
            body = {"alb": NAME, "ready": code == 200}
        elif path == "/healthz":                  # Route 53 health check
            if os.path.exists(F + ".slow"):
                time.sleep(3)
            code = 500 if os.path.exists(F + ".healthz-down") else 200
            body = {"alb": NAME, "status": "ok" if code == 200 else "fail"}
        else:
            code, body = 200, {"alb": NAME, "region": REGION, "host": self.headers.get("Host")}
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

socketserver.ThreadingTCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer(("", PORT), H).serve_forever()
'''

USER_DATA = """#!/bin/bash
mkdir -p /opt/net018
cat > /opt/net018/app.py <<'EOF'
{app}
EOF
for svc in {services}; do
  name=${{svc%%:*}}; port=${{svc##*:}}
  cat > /etc/systemd/system/net018-$name.service <<EOF
[Unit]
Description=NET-018 $name
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /opt/net018/app.py $port $name {region}
Restart=always
[Install]
WantedBy=multi-user.target
EOF
  systemctl enable --now net018-$name
done
"""


def get_session(profile, region=PRIMARY):
    return boto3.Session(profile_name=profile, region_name=region)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def spec(resource_type, name):
    return [{"ResourceType": resource_type, "Tags": [{"Key": "Name", "Value": f"{PREFIX}-{name}"}] + TAGS}]


def retry(fn, *a, attempts=12, delay=5, codes=("InvalidParameterValueException",), **kw):
    for i in range(attempts):
        try:
            return fn(*a, **kw)
        except ClientError as e:
            if e.response["Error"]["Code"] not in codes or i == attempts - 1:
                raise
            time.sleep(delay)


def console(*lines):
    print("\n  Check in the console:")
    for line in lines:
        print(f"    - {line}")


def run_on_ec2(sess, instance_id, commands, timeout=90):
    ssm = sess.client("ssm")
    cmd_id = retry(ssm.send_command, InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                   Parameters={"commands": commands}, codes=("InvalidInstanceId",))["Command"]["CommandId"]
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(2)
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        except ClientError:
            continue
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            return inv["StandardOutputContent"] + inv["StandardErrorContent"]
    return "(timed out waiting for SSM command)"


def need(r, *keys):
    for k in keys:
        if not r.get(k):
            raise SystemExit(f"missing '{k}' in {RESOURCE_FILE.name} — run the earlier deploy step first")


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 1 — NETWORK, SECURITY GROUPS, APP INSTANCES
# ═══════════════════════════════════════════════════════════════════════════════

def deploy_iam(sess, r):
    iam = sess.client("iam")
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {
        "Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        iam.create_role(RoleName=EC2_ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=TAGS)
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    iam.attach_role_policy(RoleName=EC2_ROLE, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    try:
        iam.create_instance_profile(InstanceProfileName=EC2_ROLE, Tags=TAGS)
        iam.add_role_to_instance_profile(InstanceProfileName=EC2_ROLE, RoleName=EC2_ROLE)
        time.sleep(10)   # instance profile propagation
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    print(f"[IAM] role + instance profile {EC2_ROLE} (SSM only, to flip the app's failure flags)")
    r["iam"] = True
    save(r)


def my_ip():
    import urllib.request
    return urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5).read().decode().strip()


def deploy_network_region(sess, r, region, cidr):
    reg = r.setdefault("regions", {}).setdefault(region, {"cidr": cidr})
    s = REGIONAL[region]
    ec2 = sess.client("ec2")
    print(f"\n[{region}]")
    if not reg.get("vpc_id"):
        reg["vpc_id"] = ec2.create_vpc(CidrBlock=cidr, TagSpecifications=spec("vpc", s))["Vpc"]["VpcId"]
        ec2.get_waiter("vpc_available").wait(VpcIds=[reg["vpc_id"]])
        ec2.modify_vpc_attribute(VpcId=reg["vpc_id"], EnableDnsHostnames={"Value": True})
        save(r)
    vpc = reg["vpc_id"]
    if not reg.get("igw_id"):
        reg["igw_id"] = ec2.create_internet_gateway(
            TagSpecifications=spec("internet-gateway", s))["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=reg["igw_id"], VpcId=vpc)
        save(r)
    if not reg.get("rtb_id"):
        reg["rtb_id"] = ec2.create_route_table(
            VpcId=vpc, TagSpecifications=spec("route-table", s))["RouteTable"]["RouteTableId"]
        ec2.create_route(RouteTableId=reg["rtb_id"], DestinationCidrBlock="0.0.0.0/0", GatewayId=reg["igw_id"])
        save(r)
    if not reg.get("subnet_ids"):
        azs = [z["ZoneName"] for z in ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]},
                     {"Name": "zone-type", "Values": ["availability-zone"]}])["AvailabilityZones"]][:2]
        base = cidr.split(".")
        subnets = []
        for i, az in enumerate(azs):
            block = f"{base[0]}.{base[1]}.{i + 1}.0/24"
            sid = ec2.create_subnet(VpcId=vpc, CidrBlock=block, AvailabilityZone=az,
                                    TagSpecifications=spec("subnet", f"{s}-{az[-1]}"))["Subnet"]["SubnetId"]
            ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={"Value": True})
            ec2.associate_route_table(RouteTableId=reg["rtb_id"], SubnetId=sid)
            subnets.append(sid)
        reg["subnet_ids"] = subnets
        save(r)
    print(f"  vpc {vpc} {cidr} · 2 public subnets · igw {reg['igw_id']}")

    # ALB SG: 443 from the Route 53 health checker prefix list (+ your IP for curl)
    if not reg.get("alb_sg"):
        pl = ec2.describe_managed_prefix_lists(Filters=[{"Name": "prefix-list-name", "Values": [
            f"com.amazonaws.{region}.route53-healthchecks"]}])["PrefixLists"][0]["PrefixListId"]
        reg["hc_prefix_list"] = pl
        reg["alb_sg"] = ec2.create_security_group(
            GroupName=f"{PREFIX}-alb", Description="NET-018 ALB: Route 53 health checkers + operator",
            VpcId=vpc, TagSpecifications=spec("security-group", f"{s}-alb"))["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=reg["alb_sg"], IpPermissions=[hc_sg_rule(pl)])
        ip = my_ip()
        reg["operator_ip"] = ip
        ec2.authorize_security_group_ingress(GroupId=reg["alb_sg"], IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
            "IpRanges": [{"CidrIp": f"{ip}/32", "Description": "operator curl"}]}])
        save(r)
    print(f"  alb sg {reg['alb_sg']}: 443 from {reg['hc_prefix_list']} (Route 53 health checkers)"
          f" + {reg['operator_ip']}/32")
    ports = [p for (rg, _, p) in ALBS.values() if rg == region]
    if not reg.get("app_sg"):
        reg["app_sg"] = ec2.create_security_group(
            GroupName=f"{PREFIX}-app", Description="NET-018 app instance", VpcId=vpc,
            TagSpecifications=spec("security-group", f"{s}-app"))["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=reg["app_sg"], IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": min(ports), "ToPort": max(ports),
            "UserIdGroupPairs": [{"GroupId": reg["alb_sg"]}]}])
        save(r)
    print(f"  app sg {reg['app_sg']}: {min(ports)}-{max(ports)} from the ALB SG only")

    if not reg.get("instance_id"):
        ami = sess.client("ssm").get_parameter(Name=AMI_PARAM)["Parameter"]["Value"]
        services = " ".join(f'"{name}:{p}"' for name, (rg, _, p) in ALBS.items() if rg == region)
        ud = USER_DATA.format(app=APP_PY.strip(), services=services, region=region)
        reg["instance_id"] = retry(
            sess.client("ec2").run_instances, ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
            SubnetId=reg["subnet_ids"][0], SecurityGroupIds=[reg["app_sg"]],
            IamInstanceProfile={"Name": EC2_ROLE}, UserData=ud,
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            TagSpecifications=spec("instance", f"{s}-app"),
            codes=("InvalidParameterValue",))["Instances"][0]["InstanceId"]
        save(r)
    apps = ", ".join(f"{n}:{p}" for n, (rg, _, p) in ALBS.items() if rg == region)
    print(f"  instance {reg['instance_id']} ({INSTANCE_TYPE}) serving {apps}")


def hc_sg_rule(pl):
    return {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
            "PrefixListIds": [{"PrefixListId": pl, "Description": "Route 53 health checkers"}]}


def step_network(profile, r):
    print("STEP 1 — network, security groups, app instances")
    deploy_iam(get_session(profile), r)
    for region, cidr in REGIONS.items():
        deploy_network_region(get_session(profile, region), r, region, cidr)
    console(
        "VPC → Your VPCs, filter tag Project=NET-018: one VPC in us-east-1 and one in us-west-2",
        "VPC → Security groups → NET-018-alb → Inbound rules: 443 from the pl-... prefix list. "
        "Open the prefix list (VPC → Managed prefix lists) to see the checker CIDRs",
        "VPC → Managed prefix lists: com.amazonaws.<region>.route53-healthchecks exists per region",
        "EC2 → Instances → NET-018-*-app → Status checks 2/2. Note: no inbound from the internet",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — CERTIFICATES, TARGET GROUPS, ALBs, LISTENER RULES
# ═══════════════════════════════════════════════════════════════════════════════

def self_signed():
    """Route 53 HTTPS health checks don't validate certificates, so a self-signed one is enough."""
    with tempfile.TemporaryDirectory() as d:
        key, crt = Path(d) / "k.pem", Path(d) / "c.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
                        "-subj", "/CN=net018-lab.example", "-keyout", str(key), "-out", str(crt),
                        "-addext", "subjectAltName=DNS:*.net018-lab.example,DNS:*.elb.amazonaws.com"],
                       check=True, capture_output=True)
        return crt.read_bytes(), key.read_bytes()


def deploy_albs_region(sess, r, region):
    reg = r["regions"][region]
    elb, acm = sess.client("elbv2"), sess.client("acm")
    print(f"\n[{region}]")
    if not reg.get("cert_arn"):
        crt, key = self_signed()
        reg["cert_arn"] = acm.import_certificate(Certificate=crt, PrivateKey=key, Tags=TAGS)["CertificateArn"]
        save(r)
    print(f"  ACM imported self-signed cert {reg['cert_arn'].split('/')[-1]}")
    sess.client("ec2").get_waiter("instance_running").wait(InstanceIds=[reg["instance_id"]])

    for name, (rg, _, port) in ALBS.items():
        if rg != region:
            continue
        a = r.setdefault("albs", {}).setdefault(name, {"region": region, "port": port})
        if not a.get("tg_arn"):
            a["tg_arn"] = elb.create_target_group(
                Name=f"{SHORT}-{name}", Protocol="HTTP", Port=port, VpcId=reg["vpc_id"], TargetType="instance",
                HealthCheckPath="/ready", HealthCheckIntervalSeconds=5, HealthCheckTimeoutSeconds=4,
                HealthyThresholdCount=2, UnhealthyThresholdCount=2, Matcher={"HttpCode": "200"},
                Tags=TAGS)["TargetGroups"][0]["TargetGroupArn"]
            elb.modify_target_group_attributes(TargetGroupArn=a["tg_arn"], Attributes=[
                {"Key": "deregistration_delay.timeout_seconds", "Value": "5"}])
            elb.register_targets(TargetGroupArn=a["tg_arn"], Targets=[{"Id": reg["instance_id"], "Port": port}])
            save(r)
        if not a.get("alb_arn"):
            lb = elb.create_load_balancer(
                Name=f"{SHORT}-{name}", Subnets=reg["subnet_ids"], SecurityGroups=[reg["alb_sg"]],
                Scheme="internet-facing", Type="application", IpAddressType="ipv4", Tags=TAGS)["LoadBalancers"][0]
            a.update(alb_arn=lb["LoadBalancerArn"], alb_dns=lb["DNSName"].lower(),
                     alb_zone=lb["CanonicalHostedZoneId"])
            save(r)
    for name, a in r["albs"].items():
        if a["region"] != region:
            continue
        elb.get_waiter("load_balancer_available").wait(LoadBalancerArns=[a["alb_arn"]])
        if not a.get("listener_arn"):
            a["listener_arn"] = retry(
                elb.create_listener, LoadBalancerArn=a["alb_arn"], Protocol="HTTPS", Port=443,
                SslPolicy=TLS_DEFAULT, Certificates=[{"CertificateArn": reg["cert_arn"]}],
                DefaultActions=[{"Type": "fixed-response", "FixedResponseConfig": {
                    "StatusCode": "404", "ContentType": "text/plain", "MessageBody": "no route for this host"}}],
                codes=("CertificateNotFound",), Tags=TAGS)["Listeners"][0]["ListenerArn"]
            save(r)
        if not a.get("hc_rule"):
            a["hc_rule"] = elb.create_rule(ListenerArn=a["listener_arn"], Priority=10,
                                           Conditions=hc_conditions(a["alb_dns"]),
                                           Actions=forward(a["tg_arn"]))["Rules"][0]["RuleArn"]
            a["app_rule"] = elb.create_rule(
                ListenerArn=a["listener_arn"], Priority=20,
                Conditions=[{"Field": "host-header", "HostHeaderConfig": {"Values": [
                    a["alb_dns"], "*.net018-lab.*", "api.net018-lab.*"]}}],
                Actions=forward(a["tg_arn"]))["Rules"][0]["RuleArn"]
            save(r)
        print(f"  {name}: https://{a['alb_dns']}  (zone {a['alb_zone']})")
        print(f"        rule 10: Host={a['alb_dns'][:28]}… AND path /healthz → tg {SHORT}-{name}")
        print(f"        rule 20: app hosts → tg {SHORT}-{name} · default: fixed 404")


def hc_conditions(host):
    return [{"Field": "host-header", "HostHeaderConfig": {"Values": [host]}},
            {"Field": "path-pattern", "PathPatternConfig": {"Values": ["/healthz"]}}]


def forward(tg):
    return [{"Type": "forward", "TargetGroupArn": tg}]


def step_albs(profile, r):
    need(r, "regions")
    print("STEP 2 — certificates, target groups, ALBs, listener rules")
    for region in REGIONS:
        deploy_albs_region(get_session(profile, region), r, region)
    print("\nwaiting for targets to pass /ready ...")
    for name, a in r["albs"].items():
        try:
            get_session(profile, a["region"]).client("elbv2").get_waiter("target_in_service").wait(
                TargetGroupArn=a["tg_arn"], WaiterConfig={"Delay": 10, "MaxAttempts": 30})
            print(f"  {name}: target healthy")
        except WaiterError:
            print(f"  {name}: target not healthy yet — check `status`")
    a1 = r["albs"]["alb1"]["alb_dns"]
    console(
        "EC2 → Load Balancers: NET018-alb1, NET018-alb2 (us-east-1), NET018-alb3 (us-west-2), "
        "Scheme internet-facing, State Active",
        "Load balancer → Listeners and rules → HTTPS:443 → rules 10 (/healthz for the ALB's own DNS name), "
        "20 (app hosts) and the default fixed 404",
        "Target groups → NET018-alb1 → Targets: healthy on /ready. Health checks tab: path /ready, 5 s, 2/2",
        "Certificate Manager: an Imported certificate (In use: Yes) in each region",
        f"From your terminal: curl -sk https://{a1}/healthz   → 200 (rule 10 matches)",
        f"                    curl -sk https://{a1}/healthz -H 'Host: other' → 404 (default action)",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — HOSTED ZONE + HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def step_dns(profile, r):
    need(r, "albs")
    print("STEP 3 — hosted zone and health checks")
    r53 = get_session(profile).client("route53")
    if not r.get("zone_id"):
        for name in ZONE_CANDIDATES:
            try:
                z = r53.create_hosted_zone(Name=name, CallerReference=f"{PREFIX}-{time.time()}",
                                           HostedZoneConfig={"Comment": "NET-018 lab (not delegated)"})
                r.update(zone_id=z["HostedZone"]["Id"].split("/")[-1], zone_name=name,
                         name_servers=z["DelegationSet"]["NameServers"])
                save(r)
                break
            except ClientError as e:
                print(f"  {name} rejected: {e.response['Error']['Code']}")
        else:
            raise SystemExit("no zone name accepted")
    print(f"  public zone {r['zone_name']} ({r['zone_id']}) · not delegated, queried at {r['name_servers'][0]}")
    for name, a in r["albs"].items():
        if not a.get("hc_id"):
            a["hc_id"] = r53.create_health_check(
                CallerReference=f"{PREFIX}-{name}-{time.time()}",
                HealthCheckConfig={"Type": "HTTPS", "FullyQualifiedDomainName": a["alb_dns"], "Port": 443,
                                   "ResourcePath": "/healthz", "EnableSNI": True,
                                   "RequestInterval": HC_INTERVAL, "FailureThreshold": HC_THRESHOLD})[
                "HealthCheck"]["Id"]
            r53.change_tags_for_resource(ResourceType="healthcheck", ResourceId=a["hc_id"],
                                         AddTags=[{"Key": "Name", "Value": f"{PREFIX}-{name}"}] + TAGS)
            save(r)
        print(f"  hc-{name} {a['hc_id']}: HTTPS {a['alb_dns'][:30]}…:443/healthz SNI, "
              f"every {HC_INTERVAL} s, {HC_THRESHOLD} failures")
    console(
        f"Route 53 → Hosted zones → {r['zone_name']}: only NS and SOA so far. Note the 4 name servers — "
        "nobody delegates to them, the script queries them directly",
        "Route 53 → Health checks → NET-018-alb1 → Health checkers tab: one row per checker region, "
        "status Success: HTTP Status Code 200. Takes 1-2 minutes to turn Healthy",
        "Health check → Monitoring tab: HealthCheckStatus and HealthCheckPercentageHealthy (stored in us-east-1)",
        "Health check → Latest failure reason: empty while healthy; this is what you read on every break",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — RECORDS (Exercise 1: InvalidChangeBatch and the atomic fix)
# ═══════════════════════════════════════════════════════════════════════════════

def rr(r, name):
    return f"{name}.{r['zone_name']}"


def weighted(r, alb, action="UPSERT"):
    a = r["albs"][alb]
    region, weight, _ = ALBS[alb]
    return {"Action": action, "ResourceRecordSet": {
        "Name": rr(r, f"{REGIONAL[region]}.api"), "Type": "A", "SetIdentifier": alb, "Weight": weight,
        "HealthCheckId": a["hc_id"],
        "AliasTarget": {"HostedZoneId": a["alb_zone"], "DNSName": a["alb_dns"], "EvaluateTargetHealth": True}}}


def failover(r, role, eth=True, action="UPSERT"):
    region = PRIMARY if role == "PRIMARY" else SECONDARY
    return {"Action": action, "ResourceRecordSet": {
        "Name": rr(r, "api"), "Type": "A", "SetIdentifier": role.lower(), "Failover": role,
        "AliasTarget": {"HostedZoneId": r["zone_id"], "DNSName": rr(r, f"{REGIONAL[region]}.api"),
                        "EvaluateTargetHealth": eth}}}


def simple_api(r, action):
    a = r["albs"]["alb1"]
    return {"Action": action, "ResourceRecordSet": {
        "Name": rr(r, "api"), "Type": "A",
        "AliasTarget": {"HostedZoneId": a["alb_zone"], "DNSName": a["alb_dns"], "EvaluateTargetHealth": True}}}


def step_records(profile, r):
    need(r, "zone_id")
    print("STEP 4 — records (Exercise 1 built in)")
    r53 = get_session(profile).client("route53")
    zone = r["zone_id"]

    def change(changes, comment):
        cid = r53.change_resource_record_sets(HostedZoneId=zone, ChangeBatch={
            "Comment": comment, "Changes": changes})["ChangeInfo"]["Id"]
        r53.get_waiter("resource_record_sets_changed").wait(Id=cid)

    change([weighted(r, x) for x in ALBS], "regional weighted layer")
    print(f"  {rr(r, 'use1.api')}: weighted alb1=70 alb2=30 · {rr(r, 'usw2.api')}: weighted alb3=100")
    print("    all alias → ALB, EvaluateTargetHealth=true, health check attached")

    if not r.get("records_done"):
        # 4a — what ExternalDNS would leave behind: a simple record on the shared name
        change([simple_api(r, "UPSERT")], "simple record, as ExternalDNS creates it")
        print(f"\n  4a. simple A alias {rr(r, 'api')} → alb1  (what ExternalDNS publishes by default)")
        # 4b — adding failover by hand on the same name
        try:
            change([failover(r, "PRIMARY", action="CREATE")], "failover next to simple: expected to fail")
            print("  4b. unexpected: failover record accepted")
        except ClientError as e:
            r["exercise1_error"] = e.response["Error"]["Message"]
            print(f"  4b. CREATE failover PRIMARY on the same name → {e.response['Error']['Code']}:")
            print(f"      {e.response['Error']['Message']}")
        # 4c — the fix: one change batch, DELETE must match the existing record exactly
        change([simple_api(r, "DELETE"), failover(r, "PRIMARY", action="CREATE"),
                failover(r, "SECONDARY", action="CREATE")], "atomic migration simple → failover")
        print("  4c. one change batch: DELETE simple + CREATE PRIMARY + CREATE SECONDARY → INSYNC")
        r["records_done"] = True
        r["secondary_eth"] = True
        save(r)
    print(f"\n  {rr(r, 'api')}: failover PRIMARY → use1.api, SECONDARY → usw2.api, both ETH=true")
    console(
        f"Route 53 → {r['zone_name']} → Records: 3 weighted A (use1.api ×2, usw2.api ×1) and 2 failover A "
        "(api primary/secondary). Each row shows routing policy, set ID, alias target and health check ID",
        "Open the api primary record: 'Alias to another record in this hosted zone' → use1.api, "
        "Evaluate target health = Yes, no health check of its own (it inherits from use1.api)",
        "Route 53 → Hosted zone → Test record (Test DNS answer): record api, type A → returns ALB-1 or ALB-2 IPs",
        "CloudTrail → Event history → ChangeResourceRecordSets: the failed 4b call and the 4c batch",
    )
    print("\nNext: python deploy_lab.py status --profile <profile>")


def deploy(profile, args):
    steps = {"network": step_network, "albs": step_albs, "dns": step_dns, "records": step_records}
    if not args or args[0] not in list(steps) + ["all"]:
        raise SystemExit(f"usage: deploy {' | '.join(list(steps) + ['all'])}")
    r = load()
    r["account"] = get_session(profile).client("sts").get_caller_identity()["Account"]
    print(f"NET-018 · account {r['account']} · {PRIMARY} (primary) + {SECONDARY} (secondary)\n")
    for name in (steps if args[0] == "all" else [args[0]]):
        steps[name](profile, r)
        print()


# ═══════════════════════════════════════════════════════════════════════════════
#  OBSERVATION
# ═══════════════════════════════════════════════════════════════════════════════

def dig(server, name, rtype="A"):
    cmd = ["dig", "+short", "+time=2", "+tries=1"] + ([f"@{server}"] if server else []) + [name, rtype]
    return [x for x in subprocess.run(cmd, capture_output=True, text=True).stdout.split() if x[0].isdigit()]


def ip_map(r):
    """Public resolver → each ALB's current IPs. ALB IPs change; rebuilt on every call."""
    m = {}
    for name, a in r["albs"].items():
        for ip in dig(None, a["alb_dns"]):
            m[ip] = name
    return m


def answer(r, name, m):
    ips = dig(r["name_servers"][0], rr(r, name))
    return sorted({m.get(ip, f"?{ip}") for ip in ips}) or ["no answer"]


def hc_state(r53, hc_id):
    obs = r53.get_health_check_status(HealthCheckId=hc_id)["HealthCheckObservations"]
    ok = sum(1 for o in obs if o["StatusReport"]["Status"].startswith("Success"))
    # Route 53 rule: healthy when more than 18 % of checkers report healthy
    return ("Healthy" if obs and ok / len(obs) > 0.18 else "Unhealthy"), f"{ok}/{len(obs)}"


def last_failure(r53, hc_id):
    obs = r53.get_health_check_last_failure_reason(HealthCheckId=hc_id)["HealthCheckObservations"]
    if not obs:
        return None
    o = max(obs, key=lambda x: x["StatusReport"]["CheckedTime"])
    return f"{o['Region']} {o['StatusReport']['CheckedTime']:%H:%M:%S}: {o['StatusReport']['Status'][:110]}"


def tg_state(elb, arn):
    t = elb.describe_target_health(TargetGroupArn=arn)["TargetHealthDescriptions"]
    return sum(1 for x in t if x["TargetHealth"]["State"] == "healthy"), len(t)


def snapshot(profile, r):
    r53 = get_session(profile).client("route53")
    out = {}
    for name, a in r["albs"].items():
        elb = get_session(profile, a["region"]).client("elbv2")
        out[name] = {"tg": tg_state(elb, a["tg_arn"]), "hc": hc_state(r53, a["hc_id"]), "breaks": a.get("breaks", [])}
    m = ip_map(r)
    for name in ("api", "use1.api", "usw2.api"):
        out[name] = answer(r, name, m)
    return out


def status(profile, _=None):
    r = load()
    need(r, "records_done")
    r53 = get_session(profile).client("route53")
    s = snapshot(profile, r)
    for name, a in r["albs"].items():
        x = s[name]
        print(f"{name} ({a['region']}, weight {ALBS[name][1]})  targets {x['tg'][0]}/{x['tg'][1]} healthy · "
              f"health check {x['hc'][0]} ({x['hc'][1]} checkers OK) · breaks {x['breaks'] or 'none'}")
        lf = last_failure(r53, a["hc_id"])
        if lf:
            print(f"    last failure: {lf}")
    print(f"\nSECONDARY EvaluateTargetHealth: {r.get('secondary_eth')}")
    for name in ("api", "use1.api", "usw2.api"):
        print(f"{rr(r, name):30s} → {', '.join(s[name])}")


def watch(profile, _=None):
    r = load()
    need(r, "records_done")
    t0, last = time.time(), None
    print("polling every 5 s, Ctrl+C to stop. A line is printed only when something changes.\n")
    try:
        while True:
            s = snapshot(profile, r)
            parts = [f"{n} tg {s[n]['tg'][0]}/{s[n]['tg'][1]} hc {s[n]['hc'][0][0]}({s[n]['hc'][1]})" for n in ALBS]
            dns_part = f"api→{'+'.join(s['api'])}  use1→{'+'.join(s['use1.api'])}"
            line = " · ".join(parts) + " │ " + dns_part
            if line != last:
                now = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"{now} +{time.time() - t0:4.0f}s  {line}")
                last = line
            time.sleep(5)
    except KeyboardInterrupt:
        print()


def split(profile, args):
    r = load()
    need(r, "records_done")
    n = int(args[0]) if args else 200
    m, counts = ip_map(r), {}
    for _ in range(n):
        ips = dig(r["name_servers"][0], rr(r, "use1.api"))
        key = m.get(ips[0], f"?{ips[0]}") if ips else "no answer"
        counts[key] = counts.get(key, 0) + 1
    print(f"{n} queries of {rr(r, 'use1.api')} at {r['name_servers'][0]}:")
    for k, v in sorted(counts.items()):
        print(f"  {k:10s} {v:5d}  {100 * v / n:5.1f} %")


# ═══════════════════════════════════════════════════════════════════════════════
#  BREAK / RESTORE
# ═══════════════════════════════════════════════════════════════════════════════

MODES = ("hc", "targets", "both", "slow", "sg", "host", "tls13", "empty-tg")


def need_alb(r, args):
    if not args or args[0] not in r.get("albs", {}):
        raise SystemExit(f"ALB must be one of {list(r.get('albs', {}))}")
    return args[0]


def apply_break(profile, r, alb, mode):
    a = r["albs"][alb]
    reg = r["regions"][a["region"]]
    sess = get_session(profile, a["region"])
    elb, ec2 = sess.client("elbv2"), sess.client("ec2")
    if mode in ("hc", "both"):
        elb.modify_rule(RuleArn=a["hc_rule"], Actions=[{"Type": "fixed-response", "FixedResponseConfig": {
            "StatusCode": "503", "ContentType": "text/plain", "MessageBody": "healthz forced down"}}])
    if mode in ("targets", "both"):
        run_on_ec2(sess, reg["instance_id"], [f"touch /opt/net018/{alb}.ready-down"])
    if mode == "slow":
        run_on_ec2(sess, reg["instance_id"], [f"touch /opt/net018/{alb}.slow"])
    if mode == "sg":
        # the SG is shared by every ALB in the region
        ec2.revoke_security_group_ingress(GroupId=reg["alb_sg"], IpPermissions=[hc_sg_rule(reg["hc_prefix_list"])])
    if mode == "host":
        elb.modify_rule(RuleArn=a["hc_rule"], Conditions=hc_conditions("healthz.wrong-host.invalid"))
    if mode == "tls13":
        elb.modify_listener(ListenerArn=a["listener_arn"], SslPolicy=TLS13_ONLY)
    if mode == "empty-tg":
        if not a.get("empty_tg"):
            a["empty_tg"] = elb.create_target_group(
                Name=f"{SHORT}-{alb}-empty", Protocol="HTTP", Port=a["port"], VpcId=reg["vpc_id"],
                TargetType="instance", HealthCheckPath="/ready", Tags=TAGS)["TargetGroups"][0]["TargetGroupArn"]
        a["empty_rule"] = elb.create_rule(
            ListenerArn=a["listener_arn"], Priority=30,
            Conditions=[{"Field": "path-pattern", "PathPatternConfig": {"Values": ["/empty"]}}],
            Actions=forward(a["empty_tg"]))["Rules"][0]["RuleArn"]
    a.setdefault("breaks", []).append(mode)
    save(r)


def brk(profile, args):
    r = load()
    alb = need_alb(r, args)
    if len(args) < 2 or args[1] not in MODES:
        raise SystemExit(f"usage: break ALB {{{'|'.join(MODES)}}}")
    apply_break(profile, r, alb, args[1])
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {alb}: break '{args[1]}' applied. "
          f"Run `watch` to timestamp the effect.")
    if args[1] == "sg":
        print("  note: the SG is shared, so every ALB in this region loses the checker rule")


def restore(profile, args):
    r = load()
    alb = need_alb(r, args)
    a = r["albs"][alb]
    reg = r["regions"][a["region"]]
    sess = get_session(profile, a["region"])
    elb, ec2 = sess.client("elbv2"), sess.client("ec2")
    elb.modify_rule(RuleArn=a["hc_rule"], Conditions=hc_conditions(a["alb_dns"]), Actions=forward(a["tg_arn"]))
    elb.modify_listener(ListenerArn=a["listener_arn"], SslPolicy=TLS_DEFAULT)
    run_on_ec2(sess, reg["instance_id"], [f"rm -f /opt/net018/{alb}.*"])
    try:
        ec2.authorize_security_group_ingress(GroupId=reg["alb_sg"], IpPermissions=[hc_sg_rule(reg["hc_prefix_list"])])
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
    if a.get("empty_rule"):
        elb.delete_rule(RuleArn=a.pop("empty_rule"))
    a["breaks"] = []
    save(r)
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {alb}: rule, listener, flags and SG restored")


def eth(profile, args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: eth on|off")
    r = load()
    need(r, "records_done")
    on = args[0] == "on"
    get_session(profile).client("route53").change_resource_record_sets(
        HostedZoneId=r["zone_id"], ChangeBatch={"Changes": [failover(r, "SECONDARY", eth=on)]})
    r["secondary_eth"] = on
    save(r)
    print(f"SECONDARY failover record: EvaluateTargetHealth={on}")


def iphc(profile, args):
    r = load()
    alb = need_alb(r, args)
    a = r["albs"][alb]
    ip = dig(None, a["alb_dns"])[0]
    r53 = get_session(profile).client("route53")
    hc = r53.create_health_check(
        CallerReference=f"{PREFIX}-ip-{alb}-{time.time()}",
        HealthCheckConfig={"Type": "HTTPS", "IPAddress": ip, "FullyQualifiedDomainName": a["alb_dns"],
                           "Port": 443, "ResourcePath": "/healthz", "EnableSNI": True,
                           "RequestInterval": 30, "FailureThreshold": 3})["HealthCheck"]["Id"]
    r53.change_tags_for_resource(ResourceType="healthcheck", ResourceId=hc,
                                 AddTags=[{"Key": "Name", "Value": f"{PREFIX}-{alb}-ip-{ip}"}] + TAGS)
    r.setdefault("ip_hcs", []).append(hc)
    save(r)
    print(f"IP-based health check {hc} pinned to {ip} ({alb}). ALB IPs rotate: when {ip} is no longer "
          f"in `dig {a['alb_dns']}`, this check probes an address you don't own.")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

GONE = ("NoSuchHealthCheck", "NoSuchHostedZone", "ResourceNotFoundException", "NoSuchEntity",
        "InvalidGroup.NotFound", "InvalidVpcID.NotFound", "InvalidSubnetID.NotFound",
        "InvalidInternetGatewayID.NotFound", "InvalidRouteTableID.NotFound", "InvalidInstanceID.NotFound",
        "LoadBalancerNotFound", "TargetGroupNotFound", "RuleNotFound", "ResourceNotFound")


def safe(label, fn, *a, **kw):
    try:
        fn(*a, **kw)
        print(f"  deleted {label}")
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in GONE:
            print(f"  {label}: already gone")
            return True
        print(f"  {label}: {code} {e.response['Error'].get('Message', '')[:120]}")
        return False


def until(desc, fn, timeout=300, interval=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        print(f"  waiting: {desc}")
        time.sleep(interval)
    return False


def teardown(profile, _=None):
    r = load()
    print("NET-018 teardown")
    r53 = get_session(profile).client("route53")

    # records → health checks → zone (a health check in use by a record can't be deleted)
    if r.get("zone_id"):
        try:
            sets = r53.list_resource_record_sets(HostedZoneId=r["zone_id"])["ResourceRecordSets"]
            # aliases to records in the zone must go before (or with) their targets: one batch does both
            changes = [{"Action": "DELETE", "ResourceRecordSet": s} for s in sets if s["Type"] not in ("NS", "SOA")]
            if changes:
                r53.change_resource_record_sets(HostedZoneId=r["zone_id"], ChangeBatch={"Changes": changes})
                print(f"  deleted {len(changes)} records")
        except ClientError as e:
            print(f"  records: {e.response['Error']['Code']}")
    for hc in [a.get("hc_id") for a in r.get("albs", {}).values()] + r.get("ip_hcs", []):
        if hc:
            safe(f"health check {hc}", r53.delete_health_check, HealthCheckId=hc)
    if r.get("zone_id"):
        safe(f"hosted zone {r['zone_name']}", r53.delete_hosted_zone, Id=r["zone_id"])

    for region, reg in r.get("regions", {}).items():
        print(f"\n[{region}]")
        sess = get_session(profile, region)
        ec2, elb, acm = sess.client("ec2"), sess.client("elbv2"), sess.client("acm")
        albs = {n: a for n, a in r.get("albs", {}).items() if a["region"] == region}
        for name, a in albs.items():
            if a.get("alb_arn"):
                safe(f"ALB {name}", elb.delete_load_balancer, LoadBalancerArn=a["alb_arn"])
        for a in albs.values():
            if a.get("alb_arn"):
                try:
                    elb.get_waiter("load_balancers_deleted").wait(LoadBalancerArns=[a["alb_arn"]])
                except WaiterError:
                    pass
        for name, a in albs.items():
            for key in ("tg_arn", "empty_tg"):
                if a.get(key):
                    until(f"{name} {key} released", lambda k=key, x=a: safe(
                        f"target group {name} {k}", elb.delete_target_group, TargetGroupArn=x[k]), timeout=120)
        if reg.get("cert_arn"):
            until("certificate released", lambda: safe("certificate", acm.delete_certificate,
                                                       CertificateArn=reg["cert_arn"]), timeout=300, interval=15)
        if reg.get("instance_id") and safe(f"instance {reg['instance_id']}", ec2.terminate_instances,
                                           InstanceIds=[reg["instance_id"]]):
            try:
                ec2.get_waiter("instance_terminated").wait(InstanceIds=[reg["instance_id"]])
            except WaiterError:
                pass
        if reg.get("vpc_id"):
            # account automation (e.g. GuardDuty runtime monitoring) can add endpoints + SGs to any VPC
            vpc_filter = [{"Name": "vpc-id", "Values": [reg["vpc_id"]]}]
            eps = [e["VpcEndpointId"] for e in ec2.describe_vpc_endpoints(Filters=vpc_filter)["VpcEndpoints"]]
            if eps:
                safe(f"foreign VPC endpoints {eps}", ec2.delete_vpc_endpoints, VpcEndpointIds=eps)
                time.sleep(30)
            sgs = [g["GroupId"] for g in ec2.describe_security_groups(Filters=vpc_filter)["SecurityGroups"]
                   if g["GroupName"] != "default"]
            for g in ([reg.get("app_sg"), reg.get("alb_sg")] + sgs):
                if g:
                    until(f"{g} free of ENIs", lambda x=g: safe(f"security group {x}", ec2.delete_security_group,
                                                                GroupId=x), timeout=600, interval=20)
        for sid in reg.get("subnet_ids", []):
            until(f"subnet {sid}", lambda s=sid: safe(f"subnet {s}", ec2.delete_subnet, SubnetId=s), timeout=300)
        if reg.get("rtb_id"):
            safe("route table", ec2.delete_route_table, RouteTableId=reg["rtb_id"])
        if reg.get("igw_id"):
            try:
                ec2.detach_internet_gateway(InternetGatewayId=reg["igw_id"], VpcId=reg["vpc_id"])
            except ClientError:
                pass
            safe("internet gateway", ec2.delete_internet_gateway, InternetGatewayId=reg["igw_id"])
        if reg.get("vpc_id"):
            until(f"vpc {reg['vpc_id']}", lambda v=reg["vpc_id"]: safe(f"vpc {v}", ec2.delete_vpc, VpcId=v),
                  timeout=300, interval=20)

    print("\n[IAM]")
    iam = get_session(profile).client("iam")
    try:
        iam.remove_role_from_instance_profile(InstanceProfileName=EC2_ROLE, RoleName=EC2_ROLE)
    except ClientError:
        pass
    safe("instance profile", iam.delete_instance_profile, InstanceProfileName=EC2_ROLE)
    try:
        for p in iam.list_attached_role_policies(RoleName=EC2_ROLE)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=EC2_ROLE, PolicyArn=p["PolicyArn"])
    except ClientError:
        pass
    safe(f"role {EC2_ROLE}", iam.delete_role, RoleName=EC2_ROLE)
    verify(profile, r)


def verify(profile, r):
    print("\n[verify]")
    left = []
    r53 = get_session(profile).client("route53")
    for z in r53.list_hosted_zones()["HostedZones"]:
        if z["Name"].rstrip(".") in ZONE_CANDIDATES:
            left.append(f"zone {z['Name']}")
    for page in r53.get_paginator("list_health_checks").paginate():
        left += [f"health check {h['Id']}" for h in page["HealthChecks"] if h["CallerReference"].startswith(PREFIX)]
    for region in REGIONS:
        sess = get_session(profile, region)
        vpcs = sess.client("ec2").describe_vpcs(Filters=[{"Name": "tag:Project", "Values": [PREFIX]}])["Vpcs"]
        left += [f"{region} vpc {v['VpcId']}" for v in vpcs]
        left += [f"{region} alb {lb['LoadBalancerName']}" for lb in
                 sess.client("elbv2").describe_load_balancers()["LoadBalancers"]
                 if lb["LoadBalancerName"].startswith(SHORT)]
        inst = sess.client("ec2").describe_instances(Filters=[
            {"Name": "tag:Project", "Values": [PREFIX]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
        left += [f"{region} instance {i['InstanceId']}" for res in inst["Reservations"] for i in res["Instances"]]
        reg = r.get("regions", {}).get(region, {})
        if reg.get("cert_arn"):
            try:
                sess.client("acm").describe_certificate(CertificateArn=reg["cert_arn"])
                left.append(f"{region} certificate")
            except ClientError:
                pass
    try:
        get_session(profile).client("iam").get_role(RoleName=EC2_ROLE)
        left.append(f"role {EC2_ROLE}")
    except ClientError:
        pass
    if left:
        print(f"  STILL PRESENT: {left}")
        raise SystemExit(1)
    print("  nothing left")
    RESOURCE_FILE.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    commands = {"deploy": deploy, "status": status, "watch": watch, "split": split, "break": brk,
                "restore": restore, "eth": eth, "iphc": iphc, "teardown": teardown}
    parser = argparse.ArgumentParser(description="NET-018 Phase 2 lab (ALBs, no EKS)")
    parser.add_argument("command", choices=list(commands))
    parser.add_argument("args", nargs="*")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    a = parser.parse_args()
    if not a.profile:
        raise SystemExit("--profile is required (or set AWS_PROFILE)")
    commands[a.command](a.profile, a.args)


if __name__ == "__main__":
    main()
