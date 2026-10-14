#!/usr/bin/env python3
"""NET-021 — Cross-region DNS failover for a service behind private, shared ALBs.

Two regions, each with an internal ALB shared by two target groups (svc-a, the service,
and svc-b, a decoy "other service"). A Lambda probe publishes svc-a's healthy-host count
every minute; a CloudWatch alarm on it backs a Route 53 CLOUDWATCH_METRIC health check on
the region's weighted alias record. DNS is read straight from the zone's authoritative
name servers, so no client inside the VPCs is needed.

deploy                          Build both regions + the hosted zone, records and health checks
status                          Targets, alarms, health checks and current DNS answers
dns                             Query svc-global and the regional names once
measure REGION MODE [TIMEOUT]   Break REGION and timestamp each stage until DNS drops it
                                MODE: app    svc-a health endpoint returns 503
                                      zero   deregister svc-a's target (scale to zero)
                                      decoy  svc-b (the other service on the ALB) fails
                                      probe  the probe stops running (EventBridge rule off)
restore REGION                  Undo every break in REGION
eth on|off                      EvaluateTargetHealth on the svc-global records
teardown                        Delete everything and verify

Usage:
    python deploy_lab.py deploy [--profile PROFILE]
"""

import argparse
import io
import ipaddress
import json
import os
import subprocess
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

RESOURCE_FILE = Path(__file__).parent / "resources.json"

PREFIX = "NET-021"
SHORT = "NET021"                       # ELB names: alphanumerics and hyphens, 32 chars
# The case runs in eu-west-1 / eu-west-2. Any two regions reproduce it; the lab account's
# SCP only allows US regions, so these stand in for them.
REGIONS = {"us-east-1": "10.10.0.0/16", "us-east-2": "10.20.0.0/16"}
ZONE_CANDIDATES = ["net021-lab.example", "net021-lab.test"]   # never delegated
GLOBAL_NAME = "svc-global"
NAMESPACE = f"{SHORT}/GlobalDns"
APP_PORT, DECOY_PORT = 8080, 8081
AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
INSTANCE_TYPE = "t4g.nano"
EC2_ROLE, PROBE_ROLE = f"{PREFIX}-ec2", f"{PREFIX}-probe"
PROBE_FN, PROBE_RULE = f"{PREFIX}-probe", f"{PREFIX}-probe-schedule"
TAGS = [{"Key": "Project", "Value": PREFIX}]

APP_PY = r'''
import http.server, json, os, socketserver, sys
PORT, NAME, REGION = int(sys.argv[1]), sys.argv[2], sys.argv[3]
FLAGS = "/opt/net021"

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        down = os.path.exists(f"{FLAGS}/{NAME}.down")
        degraded = os.path.exists(f"{FLAGS}/{NAME}.degraded")
        if self.path in ("/internal/healthcheck", "/health"):
            # degraded still answers 200: only a body check can tell
            code, body = (503, {"status": "down"}) if down else (200, {"status": "degraded" if degraded else "ok"})
        elif self.path == "/live":
            code, body = 200, {"status": "alive"}
        else:
            code, body = 200, {"service": NAME, "region": REGION}
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
mkdir -p /opt/net021
cat > /opt/net021/app.py <<'EOF'
{app}
EOF
for svc in "svc-a:{app_port}" "svc-b:{decoy_port}"; do
  name=${{svc%%:*}}; port=${{svc##*:}}
  cat > /etc/systemd/system/net021-$name.service <<EOF
[Unit]
Description=NET-021 $name
After=network-online.target
[Service]
ExecStart=/usr/bin/python3 /opt/net021/app.py $port $name {region}
Restart=always
[Install]
WantedBy=multi-user.target
EOF
  systemctl enable --now net021-$name
done
"""

PROBE_CODE = '''
import os
import boto3

elb = boto3.client("elbv2")
cw = boto3.client("cloudwatch")


def handler(event, context):
    targets = elb.describe_target_health(TargetGroupArn=os.environ["TG_ARN"])["TargetHealthDescriptions"]
    healthy = sum(1 for t in targets if t["TargetHealth"]["State"] == "healthy")
    # publish an explicit value every run, including 0 — the gap the native metric leaves
    cw.put_metric_data(Namespace=os.environ["NAMESPACE"], MetricData=[{
        "MetricName": "HealthyHostCount",
        "Dimensions": [{"Name": "TargetGroup", "Value": os.environ["TG_DIM"]}],
        "Value": healthy, "Unit": "Count"}])
    return {"healthy": healthy, "registered": len(targets)}
'''


def get_session(profile, region="us-east-1"):
    return boto3.Session(profile_name=profile, region_name=region)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def short(region):
    """us-east-1 -> ue1, eu-west-2 -> ew2"""
    a, b, n = region.split("-")
    return f"{a[0]}{b[0]}{n}"


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


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

def deploy_iam(sess, r):
    iam = sess.client("iam")
    print("\n[IAM]")
    ec2_trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {
        "Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    lambda_trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {
        "Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    for name, trust in ((EC2_ROLE, ec2_trust), (PROBE_ROLE, lambda_trust)):
        try:
            iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Tags=TAGS)
            print(f"  role {name}")
        except iam.exceptions.EntityAlreadyExistsException:
            print(f"  role {name} (exists)")
    iam.attach_role_policy(RoleName=EC2_ROLE,
                           PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    iam.put_role_policy(RoleName=PROBE_ROLE, PolicyName="probe", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["elasticloadbalancing:DescribeTargetHealth",
                                           "cloudwatch:PutMetricData"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                           "logs:PutLogEvents"], "Resource": "*"}]}))
    try:
        iam.create_instance_profile(InstanceProfileName=EC2_ROLE, Tags=TAGS)
        iam.add_role_to_instance_profile(InstanceProfileName=EC2_ROLE, RoleName=EC2_ROLE)
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    r["probe_role_arn"] = iam.get_role(RoleName=PROBE_ROLE)["Role"]["Arn"]
    r["iam"] = True
    save(r)
    time.sleep(10)   # role and instance profile propagation


def deploy_region(sess, r, region, cidr):
    s = short(region)
    reg = r.setdefault("regions", {}).setdefault(region, {"cidr": cidr})
    ec2, elb = sess.client("ec2"), sess.client("elbv2")
    print(f"\n[{region}]")

    # ── network ──────────────────────────────────────────────────────────────
    if not reg.get("vpc_id"):
        reg["vpc_id"] = ec2.create_vpc(CidrBlock=cidr, TagSpecifications=spec("vpc", s))["Vpc"]["VpcId"]
        ec2.get_waiter("vpc_available").wait(VpcIds=[reg["vpc_id"]])
        ec2.modify_vpc_attribute(VpcId=reg["vpc_id"], EnableDnsHostnames={"Value": True})
        save(r)
    vpc = reg["vpc_id"]
    print(f"  vpc {vpc} {cidr}")
    if not reg.get("igw_id"):
        reg["igw_id"] = ec2.create_internet_gateway(
            TagSpecifications=spec("internet-gateway", s))["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=reg["igw_id"], VpcId=vpc)
        save(r)
    if not reg.get("rtb_id"):
        reg["rtb_id"] = ec2.create_route_table(
            VpcId=vpc, TagSpecifications=spec("route-table", s))["RouteTable"]["RouteTableId"]
        ec2.create_route(RouteTableId=reg["rtb_id"], DestinationCidrBlock="0.0.0.0/0",
                         GatewayId=reg["igw_id"])
        save(r)
    if not reg.get("subnet_ids"):
        azs = [z["ZoneName"] for z in ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]},
                     {"Name": "zone-type", "Values": ["availability-zone"]}])["AvailabilityZones"]][:2]
        net = ipaddress.ip_network(cidr)
        subnets = []
        for i, az in enumerate(azs):
            block = str(list(net.subnets(new_prefix=24))[i + 1])
            sid = ec2.create_subnet(VpcId=vpc, CidrBlock=block, AvailabilityZone=az,
                                    TagSpecifications=spec("subnet", f"{s}-{az[-1]}"))["Subnet"]["SubnetId"]
            # public IP only so the instance reaches SSM without endpoints or a NAT gateway;
            # nothing listens publicly — the app SG only admits the internal ALB
            ec2.modify_subnet_attribute(SubnetId=sid, MapPublicIpOnLaunch={"Value": True})
            ec2.associate_route_table(RouteTableId=reg["rtb_id"], SubnetId=sid)
            subnets.append(sid)
        reg["subnet_ids"] = subnets
        save(r)

    # ── security groups ──────────────────────────────────────────────────────
    if not reg.get("alb_sg"):
        reg["alb_sg"] = ec2.create_security_group(
            GroupName=f"{PREFIX}-alb", Description="NET-021 internal ALB", VpcId=vpc,
            TagSpecifications=spec("security-group", f"{s}-alb"))["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=reg["alb_sg"], IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": cidr}]}])
        save(r)
    if not reg.get("app_sg"):
        reg["app_sg"] = ec2.create_security_group(
            GroupName=f"{PREFIX}-app", Description="NET-021 app instance", VpcId=vpc,
            TagSpecifications=spec("security-group", f"{s}-app"))["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=reg["app_sg"], IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": APP_PORT, "ToPort": DECOY_PORT,
            "UserIdGroupPairs": [{"GroupId": reg["alb_sg"]}]}])
        save(r)

    # ── instance: serves svc-a and the decoy svc-b ───────────────────────────
    if not reg.get("instance_id"):
        ami = sess.client("ssm").get_parameter(Name=AMI_PARAM)["Parameter"]["Value"]
        ud = USER_DATA.format(app=APP_PY.strip(), app_port=APP_PORT, decoy_port=DECOY_PORT, region=region)
        reg["instance_id"] = retry(
            ec2.run_instances, ImageId=ami, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
            SubnetId=reg["subnet_ids"][0], SecurityGroupIds=[reg["app_sg"]],
            IamInstanceProfile={"Name": EC2_ROLE}, UserData=ud,
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            TagSpecifications=spec("instance", f"{s}-app"),
            codes=("InvalidParameterValue",))["Instances"][0]["InstanceId"]
        save(r)
    print(f"  instance {reg['instance_id']}")

    # ── target groups ────────────────────────────────────────────────────────
    for key, name, port, path in (("tg_a", "svc-a", APP_PORT, "/internal/healthcheck"),
                                  ("tg_b", "svc-b", DECOY_PORT, "/health")):
        if reg.get(key):
            continue
        tg = elb.create_target_group(
            Name=f"{SHORT}-{s}-{name}", Protocol="HTTP", Port=port, VpcId=vpc, TargetType="instance",
            HealthCheckProtocol="HTTP", HealthCheckPath=path, HealthCheckIntervalSeconds=5,
            HealthCheckTimeoutSeconds=4, HealthyThresholdCount=2, UnhealthyThresholdCount=2,
            Matcher={"HttpCode": "200-299"}, Tags=TAGS)["TargetGroups"][0]
        elb.modify_target_group_attributes(TargetGroupArn=tg["TargetGroupArn"], Attributes=[
            {"Key": "deregistration_delay.timeout_seconds", "Value": "5"}])
        reg[key] = tg["TargetGroupArn"]
        save(r)
        print(f"  target group {name}: {path} every 5 s, 2/2")
    ec2.get_waiter("instance_running").wait(InstanceIds=[reg["instance_id"]])
    for key, port in (("tg_a", APP_PORT), ("tg_b", DECOY_PORT)):
        elb.register_targets(TargetGroupArn=reg[key], Targets=[{"Id": reg["instance_id"], "Port": port}])

    # ── shared internal ALB ──────────────────────────────────────────────────
    if not reg.get("alb_arn"):
        lb = elb.create_load_balancer(
            Name=f"{SHORT}-{s}-shared", Subnets=reg["subnet_ids"], SecurityGroups=[reg["alb_sg"]],
            Scheme="internal", Type="application", IpAddressType="ipv4", Tags=TAGS)["LoadBalancers"][0]
        reg.update(alb_arn=lb["LoadBalancerArn"], alb_dns=lb["DNSName"], alb_zone=lb["CanonicalHostedZoneId"])
        save(r)
    print(f"  internal ALB {reg['alb_dns']}")
    elb.get_waiter("load_balancer_available").wait(LoadBalancerArns=[reg["alb_arn"]])
    if not reg.get("listener_arn"):
        reg["listener_arn"] = elb.create_listener(
            LoadBalancerArn=reg["alb_arn"], Protocol="HTTP", Port=80,
            DefaultActions=[{"Type": "fixed-response", "FixedResponseConfig": {
                "StatusCode": "404", "ContentType": "text/plain", "MessageBody": "no route"}}],
            Tags=TAGS)["Listeners"][0]["ListenerArn"]
        for prio, host, key in ((10, "svc-a.*", "tg_a"), (20, "svc-b.*", "tg_b")):
            elb.create_rule(ListenerArn=reg["listener_arn"], Priority=prio,
                            Conditions=[{"Field": "host-header", "HostHeaderConfig": {"Values": [host]}}],
                            Actions=[{"Type": "forward", "TargetGroupArn": reg[key]}])
        save(r)

    reg["tg_a_dim"] = reg["tg_a"].split(":")[-1]          # targetgroup/name/id
    reg["alb_dim"] = reg["alb_arn"].split(":loadbalancer/")[-1]   # app/name/id

    # ── probe: EventBridge → Lambda → custom metric ──────────────────────────
    lam, events, cw = sess.client("lambda"), sess.client("events"), sess.client("cloudwatch")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.py", PROBE_CODE)
    env = {"Variables": {"TG_ARN": reg["tg_a"], "TG_DIM": reg["tg_a_dim"], "NAMESPACE": NAMESPACE}}
    try:
        fn = retry(lam.create_function, FunctionName=PROBE_FN, Runtime="python3.12",
                   Role=r["probe_role_arn"], Handler="index.handler", Code={"ZipFile": buf.getvalue()},
                   Architectures=["arm64"], Timeout=30, Environment=env,
                   Tags={"Project": PREFIX})["FunctionArn"]
    except lam.exceptions.ResourceConflictException:
        fn = lam.get_function(FunctionName=PROBE_FN)["Configuration"]["FunctionArn"]
    reg["probe_arn"] = fn
    lam.get_waiter("function_active_v2").wait(FunctionName=PROBE_FN)
    rule_arn = events.put_rule(Name=PROBE_RULE, ScheduleExpression="rate(1 minute)", State="ENABLED",
                               Tags=TAGS)["RuleArn"]
    try:
        lam.add_permission(FunctionName=PROBE_FN, StatementId="eventbridge", Action="lambda:InvokeFunction",
                           Principal="events.amazonaws.com", SourceArn=rule_arn)
    except lam.exceptions.ResourceConflictException:
        pass
    events.put_targets(Rule=PROBE_RULE, Targets=[{"Id": "probe", "Arn": fn}])
    lam.invoke(FunctionName=PROBE_FN)     # first datapoint now rather than in a minute
    print("  probe: EventBridge rate(1 minute) → Lambda → DescribeTargetHealth → PutMetricData")

    # ── alarms: the probe one (current design) and the native one (original design) ──
    reg["alarm_probe"] = f"{PREFIX}-{s}-probe-no-healthy"
    cw.put_metric_alarm(
        AlarmName=reg["alarm_probe"], Namespace=NAMESPACE, MetricName="HealthyHostCount",
        Dimensions=[{"Name": "TargetGroup", "Value": reg["tg_a_dim"]}], Statistic="Minimum",
        Period=60, EvaluationPeriods=1, DatapointsToAlarm=1, Threshold=1,
        ComparisonOperator="LessThanThreshold", TreatMissingData="breaching", Tags=TAGS)
    reg["alarm_native"] = f"{PREFIX}-{s}-native-healthy"
    cw.put_metric_alarm(
        AlarmName=reg["alarm_native"], Namespace="AWS/ApplicationELB", MetricName="HealthyHostCount",
        Dimensions=[{"Name": "TargetGroup", "Value": reg["tg_a_dim"]},
                    {"Name": "LoadBalancer", "Value": reg["alb_dim"]}],
        Statistic="Minimum", Period=60, EvaluationPeriods=1, DatapointsToAlarm=1, Threshold=1,
        ComparisonOperator="LessThanThreshold", TreatMissingData="missing", Tags=TAGS)
    print(f"  alarms: {reg['alarm_probe']} (breaching) · {reg['alarm_native']} (native, missing)")
    save(r)


def deploy_dns(sess, r):
    r53 = sess.client("route53")
    print("\n[Route 53]")
    if not r.get("zone_id"):
        last = None
        for name in ZONE_CANDIDATES:
            try:
                z = r53.create_hosted_zone(Name=name, CallerReference=f"{PREFIX}-{time.time()}",
                                           HostedZoneConfig={"Comment": "NET-021 lab (not delegated)"})
                r["zone_id"] = z["HostedZone"]["Id"].split("/")[-1]
                r["zone_name"] = name
                r["name_servers"] = z["DelegationSet"]["NameServers"]
                save(r)
                break
            except ClientError as e:
                last = e
                print(f"  {name} rejected: {e.response['Error']['Code']}")
        else:
            raise SystemExit(f"no zone name accepted: {last}")
    print(f"  public zone {r['zone_name']} ({r['zone_id']}) · queried at {r['name_servers'][0]}")

    changes = []
    for region, reg in r["regions"].items():
        for key, alarm, idhs in (("hc_probe", reg["alarm_probe"], "Unhealthy"),
                                 ("hc_native", reg["alarm_native"], "LastKnownStatus")):
            if not reg.get(key):
                reg[key] = r53.create_health_check(
                    CallerReference=f"{PREFIX}-{key}-{region}-{time.time()}",
                    HealthCheckConfig={"Type": "CLOUDWATCH_METRIC",
                                       "AlarmIdentifier": {"Region": region, "Name": alarm},
                                       "InsufficientDataHealthStatus": idhs})["HealthCheck"]["Id"]
                r53.change_tags_for_resource(ResourceType="healthcheck", ResourceId=reg[key],
                                             AddTags=[{"Key": "Name", "Value": f"{alarm}"}] + TAGS)
                save(r)
        print(f"  {region}: health check {reg['hc_probe']} (probe alarm, insufficient data → Unhealthy)"
              f" · {reg['hc_native']} (native alarm, not attached, last known status)")
        changes.append(global_record(r, region, eth=False, action="UPSERT"))
        changes.append({"Action": "UPSERT", "ResourceRecordSet": {
            "Name": f"svc-{short(region)}.{r['zone_name']}", "Type": "CNAME", "TTL": 60,
            "ResourceRecords": [{"Value": reg["alb_dns"]}]}})
    r53.change_resource_record_sets(HostedZoneId=r["zone_id"], ChangeBatch={"Changes": changes})
    print(f"  {GLOBAL_NAME}.{r['zone_name']}: weighted alias 50/50, EvaluateTargetHealth=false, health check")
    print("  regional CNAMEs svc-<region>: TTL 60, no health check")
    r["eth"] = False
    save(r)


def global_record(r, region, eth, action="UPSERT"):
    reg = r["regions"][region]
    return {"Action": action, "ResourceRecordSet": {
        "Name": f"{GLOBAL_NAME}.{r['zone_name']}", "Type": "A", "SetIdentifier": region, "Weight": 50,
        "HealthCheckId": reg["hc_probe"],
        "AliasTarget": {"HostedZoneId": reg["alb_zone"], "DNSName": reg["alb_dns"],
                        "EvaluateTargetHealth": eth}}}


def deploy(profile):
    r = load()
    base = get_session(profile)
    r["account"] = base.client("sts").get_caller_identity()["Account"]
    print(f"NET-021 deploy · account {r['account']} · {', '.join(REGIONS)}")
    deploy_iam(base, r)
    for region, cidr in REGIONS.items():
        deploy_region(get_session(profile, region), r, region, cidr)
    deploy_dns(base, r)

    print("\nwaiting for targets to pass the ALB health check...")
    for region, reg in r["regions"].items():
        elb = get_session(profile, region).client("elbv2")
        for key in ("tg_a", "tg_b"):
            try:
                elb.get_waiter("target_in_service").wait(
                    TargetGroupArn=reg[key], WaiterConfig={"Delay": 10, "MaxAttempts": 40})
            except WaiterError:
                print(f"  {region} {key} not healthy yet — check `status`")
    print(f"\nDone. state → {RESOURCE_FILE.name}")
    print("Health checks need a couple of minutes of probe data before both regions answer.")
    print("Next: python deploy_lab.py status --profile <profile>")


# ═══════════════════════════════════════════════════════════════════════════════
#  OBSERVATION
# ═══════════════════════════════════════════════════════════════════════════════

def region_of(ip, r):
    for region, reg in r["regions"].items():
        if ipaddress.ip_address(ip) in ipaddress.ip_network(reg["cidr"]):
            return region
    return f"?{ip}"


def dig(r, name, rtype="A", tries=8):
    """Ask the zone's own name server: Route 53's decision, before any resolver cache."""
    ns = r["name_servers"][0]
    answers = set()
    for _ in range(tries):
        out = subprocess.run(["dig", "+short", "+time=2", "+tries=1", f"@{ns}", name, rtype],
                             capture_output=True, text=True).stdout.split()
        answers.update(out)
    return answers


def global_regions(r):
    ips = [a for a in dig(r, f"{GLOBAL_NAME}.{r['zone_name']}") if a[0].isdigit()]
    return sorted({region_of(ip, r) for ip in ips})


def hc_status(r53, hc_id):
    obs = r53.get_health_check_status(HealthCheckId=hc_id)["HealthCheckObservations"]
    ok = sum(1 for o in obs if o["StatusReport"]["Status"].startswith("Success"))
    return "Healthy" if ok * 2 > len(obs) else "Unhealthy", f"{ok}/{len(obs)}"


def tg_health(elb, arn):
    t = elb.describe_target_health(TargetGroupArn=arn)["TargetHealthDescriptions"]
    return sum(1 for x in t if x["TargetHealth"]["State"] == "healthy"), len(t)


def latest_metric(cw, namespace, dims, minutes=4):
    end = datetime.now(timezone.utc)
    pts = cw.get_metric_statistics(Namespace=namespace, MetricName="HealthyHostCount", Dimensions=dims,
                                   StartTime=end - timedelta(minutes=minutes), EndTime=end, Period=60,
                                   Statistics=["Minimum"])["Datapoints"]
    if not pts:
        return None
    p = max(pts, key=lambda x: x["Timestamp"])
    return p["Minimum"], p["Timestamp"]


def snapshot(profile, r, region):
    reg = r["regions"][region]
    sess = get_session(profile, region)
    elb, cw = sess.client("elbv2"), sess.client("cloudwatch")
    r53 = get_session(profile).client("route53")
    a = tg_health(elb, reg["tg_a"])
    b = tg_health(elb, reg["tg_b"])
    alarms = {x["AlarmName"]: x["StateValue"] for x in cw.describe_alarms(
        AlarmNames=[reg["alarm_probe"], reg["alarm_native"]])["MetricAlarms"]}
    probe_m = latest_metric(cw, NAMESPACE, [{"Name": "TargetGroup", "Value": reg["tg_a_dim"]}])
    native_m = latest_metric(cw, "AWS/ApplicationELB", [{"Name": "TargetGroup", "Value": reg["tg_a_dim"]},
                                                        {"Name": "LoadBalancer", "Value": reg["alb_dim"]}])
    return {"svc_a": a, "svc_b": b,
            "probe_metric": probe_m, "native_metric": native_m,
            "alarm_probe": alarms.get(reg["alarm_probe"]), "alarm_native": alarms.get(reg["alarm_native"]),
            "hc_probe": hc_status(r53, reg["hc_probe"]), "hc_native": hc_status(r53, reg["hc_native"])}


def fmt_metric(m):
    if not m:
        return "no data"
    return f"{m[0]:.0f} @ {m[1].strftime('%H:%M')}"


def status(profile, _=None):
    r = load()
    if not r.get("regions"):
        raise SystemExit("nothing deployed")
    for region in r["regions"]:
        s = snapshot(profile, r, region)
        print(f"{region}")
        print(f"  svc-a targets healthy {s['svc_a'][0]}/{s['svc_a'][1]} · svc-b (decoy) "
              f"{s['svc_b'][0]}/{s['svc_b'][1]}")
        print(f"  probe metric  {fmt_metric(s['probe_metric']):14s} alarm {s['alarm_probe']:17s} "
              f"health check {s['hc_probe'][0]} ({s['hc_probe'][1]})")
        print(f"  native metric {fmt_metric(s['native_metric']):14s} alarm {s['alarm_native']:17s} "
              f"health check {s['hc_native'][0]} ({s['hc_native'][1]})  [not attached]")
    print(f"\nEvaluateTargetHealth on {GLOBAL_NAME}: {r.get('eth')}")
    dns(profile)


def dns(profile, _=None):
    r = load()
    print(f"{GLOBAL_NAME}.{r['zone_name']} → {', '.join(global_regions(r)) or 'no answer'}")
    for region in r["regions"]:
        name = f"svc-{short(region)}.{r['zone_name']}"
        print(f"{name} → {', '.join(dig(r, name, 'CNAME', tries=1)) or 'no answer'}  (no health check)")


# ═══════════════════════════════════════════════════════════════════════════════
#  BREAK / RESTORE / MEASURE
# ═══════════════════════════════════════════════════════════════════════════════

def need_region(r, args):
    if not args or args[0] not in r.get("regions", {}):
        raise SystemExit(f"region must be one of {list(r.get('regions', {}))}")
    return args[0]


def apply_break(profile, r, region, mode):
    reg = r["regions"][region]
    sess = get_session(profile, region)
    if mode == "app":
        run_on_ec2(sess, reg["instance_id"], ["touch /opt/net021/svc-a.down"])
    elif mode == "decoy":
        run_on_ec2(sess, reg["instance_id"], ["touch /opt/net021/svc-b.down"])
    elif mode == "zero":
        sess.client("elbv2").deregister_targets(TargetGroupArn=reg["tg_a"],
                                               Targets=[{"Id": reg["instance_id"], "Port": APP_PORT}])
    elif mode == "probe":
        sess.client("events").disable_rule(Name=PROBE_RULE)
    elif mode == "degraded":
        run_on_ec2(sess, reg["instance_id"], ["touch /opt/net021/svc-a.degraded"])
    else:
        raise SystemExit("MODE: app | zero | decoy | probe | degraded")


def restore(profile, args):
    r = load()
    region = need_region(r, args)
    reg = r["regions"][region]
    sess = get_session(profile, region)
    run_on_ec2(sess, reg["instance_id"], ["rm -f /opt/net021/*.down /opt/net021/*.degraded"])
    sess.client("elbv2").register_targets(TargetGroupArn=reg["tg_a"],
                                         Targets=[{"Id": reg["instance_id"], "Port": APP_PORT}])
    sess.client("events").enable_rule(Name=PROBE_RULE)
    print(f"{region}: flags cleared, svc-a target registered, probe rule enabled")
    print("recovery takes a few minutes: targets 2/2 → probe → alarm OK → health check → DNS")


def measure(profile, args):
    r = load()
    region = need_region(r, args)
    if len(args) < 2:
        raise SystemExit("usage: measure REGION MODE [TIMEOUT]")
    mode, timeout = args[1], int(args[2]) if len(args) > 2 else 420
    other = next(x for x in r["regions"] if x != region)

    before = global_regions(r)
    print(f"before: {GLOBAL_NAME} answers {before} · EvaluateTargetHealth={r.get('eth')}")
    if region not in before:
        raise SystemExit(f"{region} is not being served yet — run restore and wait")

    t0 = time.time()
    apply_break(profile, r, region, mode)
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"{stamp} broke {region} with mode={mode} — polling every 5 s (timeout {timeout} s)\n")

    marks, last = {}, None

    def mark(key, cond):
        if cond and key not in marks:
            marks[key] = time.time() - t0

    while time.time() - t0 < timeout:
        s = snapshot(profile, r, region)
        served = global_regions(r)
        el = time.time() - t0
        mark("svc-a targets unhealthy", s["svc_a"][0] == 0)
        mark("svc-b (decoy) targets unhealthy", s["svc_b"][0] == 0)
        mark("probe metric = 0", s["probe_metric"] is not None and s["probe_metric"][0] == 0)
        mark("probe alarm ALARM", s["alarm_probe"] == "ALARM")
        mark("probe health check Unhealthy", s["hc_probe"][0] == "Unhealthy")
        mark("native metric stops / 0", s["native_metric"] is None or s["native_metric"][0] == 0)
        mark("native health check Unhealthy", s["hc_native"][0] == "Unhealthy")
        mark(f"DNS stops answering {region}", region not in served)
        line = (f"+{el:5.0f}s  svc-a {s['svc_a'][0]}/{s['svc_a'][1]}  decoy {s['svc_b'][0]}/{s['svc_b'][1]}  "
                f"probe {fmt_metric(s['probe_metric']):13s} {s['alarm_probe']:17s} hc {s['hc_probe'][0]:9s}  "
                f"native {fmt_metric(s['native_metric']):13s} hc {s['hc_native'][0]:9s}  dns {served}")
        if line[8:] != last:
            print(line)
            last = line[8:]
        if region not in served:
            break
        time.sleep(5)

    print("\nstages (seconds after the break):")
    for k, v in sorted(marks.items(), key=lambda kv: kv[1]):
        print(f"  {v:6.0f}  {k}")
    missing = [k for k in ("probe health check Unhealthy", f"DNS stops answering {region}") if k not in marks]
    for k in missing:
        print(f"     —   {k}  (not reached within {timeout} s)")
    if f"DNS stops answering {region}" in marks:
        print(f"\n{GLOBAL_NAME} now answers only {other}. Add the 60 s alias TTL and client caching on top.")
    res = r.setdefault("results", [])
    res.append({"region": region, "mode": mode, "eth": r.get("eth"), "at": stamp,
                "stages": {k: round(v) for k, v in marks.items()}})
    save(r)
    print(f"\nrecorded in {RESOURCE_FILE.name}. Undo with: restore {region}")


def eth(profile, args):
    if not args or args[0] not in ("on", "off"):
        raise SystemExit("usage: eth on|off")
    r = load()
    on = args[0] == "on"
    r53 = get_session(profile).client("route53")
    r53.change_resource_record_sets(HostedZoneId=r["zone_id"], ChangeBatch={
        "Changes": [global_record(r, region, eth=on) for region in r["regions"]]})
    r["eth"] = on
    save(r)
    print(f"EvaluateTargetHealth={on} on both {GLOBAL_NAME} records (health checks still attached)")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def safe(label, fn, *a, **kw):
    try:
        fn(*a, **kw)
        print(f"  deleted {label}")
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchHealthCheck", "NoSuchHostedZone", "ResourceNotFoundException", "NoSuchEntity",
                    "InvalidGroup.NotFound", "InvalidVpcID.NotFound", "InvalidSubnetID.NotFound",
                    "InvalidInternetGatewayID.NotFound", "InvalidRouteTableID.NotFound",
                    "LoadBalancerNotFound", "TargetGroupNotFound", "ResourceNotFound"):
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
    print("NET-021 teardown")
    r53 = get_session(profile).client("route53")

    # ── DNS first: records, then health checks, then the zone ────────────────
    if r.get("zone_id"):
        try:
            sets = r53.list_resource_record_sets(HostedZoneId=r["zone_id"])["ResourceRecordSets"]
            changes = [{"Action": "DELETE", "ResourceRecordSet": s} for s in sets if s["Type"] not in ("NS", "SOA")]
            if changes:
                r53.change_resource_record_sets(HostedZoneId=r["zone_id"], ChangeBatch={"Changes": changes})
                print(f"  deleted {len(changes)} records")
        except ClientError as e:
            print(f"  records: {e.response['Error']['Code']}")
    for region, reg in r.get("regions", {}).items():
        for key in ("hc_probe", "hc_native"):
            if reg.get(key):
                safe(f"health check {reg[key]}", r53.delete_health_check, HealthCheckId=reg[key])
    if r.get("zone_id"):
        safe(f"hosted zone {r['zone_name']}", r53.delete_hosted_zone, Id=r["zone_id"])

    # ── per region ───────────────────────────────────────────────────────────
    for region, reg in r.get("regions", {}).items():
        print(f"\n[{region}]")
        sess = get_session(profile, region)
        ec2, elb = sess.client("ec2"), sess.client("elbv2")
        lam, events, cw, logs = (sess.client(x) for x in ("lambda", "events", "cloudwatch", "logs"))

        try:
            events.remove_targets(Rule=PROBE_RULE, Ids=["probe"])
        except ClientError:
            pass
        safe("probe schedule", events.delete_rule, Name=PROBE_RULE)
        safe("probe function", lam.delete_function, FunctionName=PROBE_FN)
        safe("probe log group", logs.delete_log_group, logGroupName=f"/aws/lambda/{PROBE_FN}")
        alarms = [a for a in (reg.get("alarm_probe"), reg.get("alarm_native")) if a]
        if alarms:
            safe("alarms", cw.delete_alarms, AlarmNames=alarms)

        if reg.get("alb_arn"):
            safe("internal ALB", elb.delete_load_balancer, LoadBalancerArn=reg["alb_arn"])
            try:
                elb.get_waiter("load_balancers_deleted").wait(LoadBalancerArns=[reg["alb_arn"]])
            except WaiterError:
                pass
        for key in ("tg_a", "tg_b"):
            if reg.get(key):
                until(f"{key} released", lambda k=key: safe(f"target group {k}", elb.delete_target_group,
                                                          TargetGroupArn=reg[k]), timeout=120)
        if reg.get("instance_id"):
            safe(f"instance {reg['instance_id']}", ec2.terminate_instances, InstanceIds=[reg["instance_id"]])
            try:
                ec2.get_waiter("instance_terminated").wait(InstanceIds=[reg["instance_id"]])
            except WaiterError:
                pass
        # ALB ENIs can linger for a few minutes after the load balancer is gone
        for key in ("app_sg", "alb_sg"):
            if reg.get(key):
                until(f"{key} free of ENIs", lambda k=key: safe(f"security group {k}", ec2.delete_security_group,
                                                              GroupId=reg[k]), timeout=600, interval=20)
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
            safe(f"vpc {reg['vpc_id']}", ec2.delete_vpc, VpcId=reg["vpc_id"])

    # ── IAM ──────────────────────────────────────────────────────────────────
    print("\n[IAM]")
    iam = get_session(profile).client("iam")
    try:
        iam.remove_role_from_instance_profile(InstanceProfileName=EC2_ROLE, RoleName=EC2_ROLE)
    except ClientError:
        pass
    safe("instance profile", iam.delete_instance_profile, InstanceProfileName=EC2_ROLE)
    try:
        iam.detach_role_policy(RoleName=EC2_ROLE, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    except ClientError:
        pass
    safe(f"role {EC2_ROLE}", iam.delete_role, RoleName=EC2_ROLE)
    try:
        iam.delete_role_policy(RoleName=PROBE_ROLE, PolicyName="probe")
    except ClientError:
        pass
    safe(f"role {PROBE_ROLE}", iam.delete_role, RoleName=PROBE_ROLE)

    verify(profile, r)


def verify(profile, r):
    print("\n[verify]")
    left = []
    r53 = get_session(profile).client("route53")
    if r.get("zone_id"):
        try:
            r53.get_hosted_zone(Id=r["zone_id"])
            left.append(f"zone {r['zone_id']}")
        except ClientError:
            pass
    for region, reg in r.get("regions", {}).items():
        for key in ("hc_probe", "hc_native"):
            if reg.get(key):
                try:
                    r53.get_health_check(HealthCheckId=reg[key])
                    left.append(f"health check {reg[key]}")
                except ClientError:
                    pass
        sess = get_session(profile, region)
        vpcs = sess.client("ec2").describe_vpcs(Filters=[{"Name": "tag:Project", "Values": [PREFIX]}])["Vpcs"]
        left += [f"{region} vpc {v['VpcId']}" for v in vpcs]
        lbs = [lb["LoadBalancerName"] for lb in sess.client("elbv2").describe_load_balancers()["LoadBalancers"]
               if lb["LoadBalancerName"].startswith(SHORT)]
        left += [f"{region} alb {n}" for n in lbs]
        try:
            sess.client("lambda").get_function(FunctionName=PROBE_FN)
            left.append(f"{region} lambda {PROBE_FN}")
        except ClientError:
            pass
    iam = get_session(profile).client("iam")
    for role in (EC2_ROLE, PROBE_ROLE):
        try:
            iam.get_role(RoleName=role)
            left.append(f"role {role}")
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
    commands = {"deploy": deploy, "status": status, "dns": dns, "measure": measure,
                "restore": restore, "eth": eth, "teardown": teardown}
    parser = argparse.ArgumentParser(description="NET-021 private ALB metric failover lab")
    parser.add_argument("command", choices=list(commands))
    parser.add_argument("args", nargs="*")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    a = parser.parse_args()
    if not a.profile:
        raise SystemExit("--profile is required (or set AWS_PROFILE)")
    if a.command == "deploy":
        deploy(a.profile)
    else:
        commands[a.command](a.profile, a.args)


if __name__ == "__main__":
    main()
