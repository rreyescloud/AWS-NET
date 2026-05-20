import boto3

session = boto3.Session(profile_name='rchiasro1', region_name='us-east-1')

ec2 = session.client('ec2')
r53 = session.client('route53')
wafv2 = session.client('wafv2')
network_fw = session.client('network-firewall')

print("=== VPCs ===")
for vpc in ec2.describe_vpcs()['Vpcs']:
    name = next((t['Value'] for t in vpc.get('Tags', []) if t['Key'] == 'Name'), 'Sin nombre')
    print(f"  {name} | {vpc['VpcId']} | {vpc['CidrBlock']}")

print("\n=== Route 53 Hosted Zones ===")
zones = r53.list_hosted_zones()['HostedZones']
if zones:
    for zone in zones:
        print(f"  {zone['Name']} | {zone['Id']} | Records: {zone['ResourceRecordSetCount']}")
else:
    print("  (ninguna)")

print("\n=== WAF Web ACLs ===")
acls = wafv2.list_web_acls(Scope='REGIONAL')['WebACLs']
if acls:
    for acl in acls:
        print(f"  {acl['Name']} | {acl['Id']}")
else:
    print("  (ninguna)")

print("\n=== Network Firewalls ===")
firewalls = network_fw.list_firewalls()['Firewalls']
if firewalls:
    for fw in firewalls:
        print(f"  {fw['FirewallName']} | {fw['FirewallArn']}")
else:
    print("  (ninguno)")
