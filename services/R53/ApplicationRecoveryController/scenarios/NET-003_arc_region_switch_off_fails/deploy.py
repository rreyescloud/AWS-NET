"""
NET-003: Multi-Region Automated Failover with ARC Region Switch + Aurora Global Database
Deploy script — creates all infrastructure for the failover lab.

Usage:
    python deploy.py --profile <aws-profile> --account-id <account-id>

Resources created:
    - ARC Cluster (5 endpoints, 5 regions)
    - Control Panel + 2 Routing Controls + Safety Rule
    - Aurora Global Database (us-east-1 primary + us-west-2 standby)
    - Route 53 Health Checks + Hosted Zone + Failover Records
    - IAM Execution Role for Region Switch
    - Region Switch Plan (Aurora switchover + routing control switch)
"""

import boto3
import json
import time
import argparse
import sys


def wait_for_status(describe_fn, check_fn, resource_name, interval=10, timeout=600):
    elapsed = 0
    while elapsed < timeout:
        result = describe_fn()
        if check_fn(result):
            print(f"  ✓ {resource_name} ready")
            return result
        print(f"  ... waiting for {resource_name} ({elapsed}s)")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"{resource_name} did not become ready within {timeout}s")


def deploy(profile, account_id):
    session = boto3.Session(profile_name=profile)

    east_rds = session.client('rds', region_name='us-east-1')
    west_rds = session.client('rds', region_name='us-west-2')
    east_ec2 = session.client('ec2', region_name='us-east-1')
    west_ec2 = session.client('ec2', region_name='us-west-2')
    arc_config = session.client('route53-recovery-control-config', region_name='us-west-2')
    route53 = session.client('route53')
    iam = session.client('iam')
    arc_rs = session.client('arc-region-switch', region_name='us-west-2')

    resources = {}

    # ================================================================
    # STEP 1: ARC Cluster
    # ================================================================
    print("\n[1/10] Creating ARC Cluster...")
    cluster_resp = arc_config.create_cluster(ClusterName='NET003-Lab-Cluster')
    cluster_arn = cluster_resp['Cluster']['ClusterArn']
    resources['cluster_arn'] = cluster_arn

    wait_for_status(
        lambda: arc_config.describe_cluster(ClusterArn=cluster_arn),
        lambda r: r['Cluster']['Status'] == 'DEPLOYED',
        "ARC Cluster",
        interval=15, timeout=600
    )

    cluster_info = arc_config.describe_cluster(ClusterArn=cluster_arn)
    endpoints = cluster_info['Cluster']['ClusterEndpoints']
    west_endpoint = next(e for e in endpoints if e['Region'] == 'us-west-2')['Endpoint']
    resources['cluster_endpoints'] = endpoints
    resources['west_endpoint'] = west_endpoint
    print(f"  Cluster ARN: {cluster_arn}")
    print(f"  us-west-2 endpoint: {west_endpoint}")

    # ================================================================
    # STEP 2: Control Panel
    # ================================================================
    print("\n[2/10] Creating Control Panel...")
    panel_resp = arc_config.create_control_panel(
        ClusterArn=cluster_arn,
        ControlPanelName='NET003-FailoverPanel'
    )
    panel_arn = panel_resp['ControlPanel']['ControlPanelArn']
    resources['panel_arn'] = panel_arn
    print(f"  Panel ARN: {panel_arn}")

    time.sleep(5)

    # ================================================================
    # STEP 3: Routing Controls
    # ================================================================
    print("\n[3/10] Creating Routing Controls...")
    rc_primary_resp = arc_config.create_routing_control(
        ClusterArn=cluster_arn,
        ControlPanelArn=panel_arn,
        RoutingControlName='RC-Primary-useast1'
    )
    rc_primary_arn = rc_primary_resp['RoutingControl']['RoutingControlArn']
    resources['rc_primary_arn'] = rc_primary_arn

    rc_standby_resp = arc_config.create_routing_control(
        ClusterArn=cluster_arn,
        ControlPanelArn=panel_arn,
        RoutingControlName='RC-Standby-uswest2'
    )
    rc_standby_arn = rc_standby_resp['RoutingControl']['RoutingControlArn']
    resources['rc_standby_arn'] = rc_standby_arn

    print(f"  RC Primary: {rc_primary_arn}")
    print(f"  RC Standby: {rc_standby_arn}")

    time.sleep(5)

    # Set initial state: Primary ON, Standby OFF
    print("  Setting initial state: Primary=ON, Standby=OFF")
    arc_data = session.client(
        'route53-recovery-cluster',
        region_name='us-west-2',
        endpoint_url=west_endpoint
    )
    arc_data.update_routing_control_states(
        UpdateRoutingControlStateEntries=[
            {'RoutingControlArn': rc_primary_arn, 'RoutingControlState': 'On'},
            {'RoutingControlArn': rc_standby_arn, 'RoutingControlState': 'Off'}
        ]
    )
    print("  ✓ Routing controls set")

    # ================================================================
    # STEP 4: Safety Rule
    # ================================================================
    print("\n[4/10] Creating Safety Rule (at least 1 ON)...")
    safety_resp = arc_config.create_safety_rule(
        AssertionRule={
            'AssertedControls': [rc_primary_arn, rc_standby_arn],
            'ControlPanelArn': panel_arn,
            'Name': 'AtLeastOneOn',
            'RuleConfig': {'Inverted': False, 'Threshold': 1, 'Type': 'ATLEAST'},
            'WaitPeriodMs': 5000
        }
    )
    safety_arn = safety_resp['AssertionRule']['SafetyRuleArn']
    resources['safety_rule_arn'] = safety_arn
    print(f"  Safety Rule: {safety_arn}")

    # ================================================================
    # STEP 5: DB Subnet Groups
    # ================================================================
    print("\n[5/10] Creating DB Subnet Groups...")

    # us-east-1
    east_vpc = east_ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])['Vpcs'][0]['VpcId']
    east_subnets = east_ec2.describe_subnets(
        Filters=[{'Name': 'vpc-id', 'Values': [east_vpc]}]
    )['Subnets']
    east_subnet_ids = [s['SubnetId'] for s in east_subnets[:3]]

    try:
        east_rds.create_db_subnet_group(
            DBSubnetGroupName='net003-subnet-group',
            DBSubnetGroupDescription='NET-003 Aurora lab us-east-1',
            SubnetIds=east_subnet_ids
        )
    except east_rds.exceptions.DBSubnetGroupAlreadyExistsFault:
        pass
    print(f"  ✓ us-east-1 subnet group ({east_vpc})")

    # us-west-2
    west_vpc = west_ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])['Vpcs'][0]['VpcId']
    west_subnets = west_ec2.describe_subnets(
        Filters=[{'Name': 'vpc-id', 'Values': [west_vpc]}]
    )['Subnets']
    west_subnet_ids = [s['SubnetId'] for s in west_subnets[:4]]

    try:
        west_rds.create_db_subnet_group(
            DBSubnetGroupName='net003-subnet-group',
            DBSubnetGroupDescription='NET-003 Aurora lab us-west-2',
            SubnetIds=west_subnet_ids
        )
    except west_rds.exceptions.DBSubnetGroupAlreadyExistsFault:
        pass
    print(f"  ✓ us-west-2 subnet group ({west_vpc})")

    # ================================================================
    # STEP 6: Aurora Global Database
    # ================================================================
    print("\n[6/10] Creating Aurora Global Database...")

    # Get default security group for us-east-1
    east_sg = east_ec2.describe_security_groups(
        Filters=[{'Name': 'group-name', 'Values': ['default']}, {'Name': 'vpc-id', 'Values': [east_vpc]}]
    )['SecurityGroups'][0]['GroupId']

    # Primary cluster
    print("  Creating primary cluster (us-east-1)...")
    east_rds.create_db_cluster(
        DBClusterIdentifier='net003-primary-cluster',
        Engine='aurora-postgresql',
        EngineVersion='16.4',
        MasterUsername='adminuser',
        MasterUserPassword='Lab12345678!',
        DBSubnetGroupName='net003-subnet-group',
        VpcSecurityGroupIds=[east_sg]
    )

    wait_for_status(
        lambda: east_rds.describe_db_clusters(DBClusterIdentifier='net003-primary-cluster'),
        lambda r: r['DBClusters'][0]['Status'] == 'available',
        "Primary cluster",
        interval=10, timeout=300
    )

    # Primary instance
    print("  Creating primary instance...")
    east_rds.create_db_instance(
        DBInstanceIdentifier='net003-primary-instance-1',
        DBClusterIdentifier='net003-primary-cluster',
        DBInstanceClass='db.r6g.large',
        Engine='aurora-postgresql'
    )

    wait_for_status(
        lambda: east_rds.describe_db_instances(DBInstanceIdentifier='net003-primary-instance-1'),
        lambda r: r['DBInstances'][0]['DBInstanceStatus'] == 'available',
        "Primary instance",
        interval=15, timeout=600
    )

    # Global cluster
    print("  Creating global cluster...")
    primary_cluster_arn = f"arn:aws:rds:us-east-1:{account_id}:cluster:net003-primary-cluster"
    east_rds.create_global_cluster(
        GlobalClusterIdentifier='net003-global-db',
        SourceDBClusterIdentifier=primary_cluster_arn
    )
    resources['global_cluster_id'] = 'net003-global-db'

    # Secondary cluster
    print("  Creating secondary cluster (us-west-2)...")
    west_sg = west_ec2.describe_security_groups(
        Filters=[{'Name': 'group-name', 'Values': ['default']}, {'Name': 'vpc-id', 'Values': [west_vpc]}]
    )['SecurityGroups'][0]['GroupId']

    west_rds.create_db_cluster(
        DBClusterIdentifier='net003-secondary-cluster',
        Engine='aurora-postgresql',
        EngineVersion='16.4',
        GlobalClusterIdentifier='net003-global-db',
        DBSubnetGroupName='net003-subnet-group',
        VpcSecurityGroupIds=[west_sg]
    )

    wait_for_status(
        lambda: west_rds.describe_db_clusters(DBClusterIdentifier='net003-secondary-cluster'),
        lambda r: r['DBClusters'][0]['Status'] == 'available',
        "Secondary cluster",
        interval=10, timeout=300
    )

    # Secondary instance
    print("  Creating secondary instance...")
    west_rds.create_db_instance(
        DBInstanceIdentifier='net003-secondary-instance-1',
        DBClusterIdentifier='net003-secondary-cluster',
        DBInstanceClass='db.r6g.large',
        Engine='aurora-postgresql'
    )

    wait_for_status(
        lambda: west_rds.describe_db_instances(DBInstanceIdentifier='net003-secondary-instance-1'),
        lambda r: r['DBInstances'][0]['DBInstanceStatus'] == 'available',
        "Secondary instance",
        interval=15, timeout=600
    )

    secondary_cluster_arn = f"arn:aws:rds:us-west-2:{account_id}:cluster:net003-secondary-cluster"
    resources['primary_cluster_arn'] = primary_cluster_arn
    resources['secondary_cluster_arn'] = secondary_cluster_arn

    primary_endpoint = east_rds.describe_db_clusters(
        DBClusterIdentifier='net003-primary-cluster'
    )['DBClusters'][0]['Endpoint']
    secondary_endpoint = west_rds.describe_db_clusters(
        DBClusterIdentifier='net003-secondary-cluster'
    )['DBClusters'][0]['Endpoint']
    resources['primary_endpoint'] = primary_endpoint
    resources['secondary_endpoint'] = secondary_endpoint

    print(f"  ✓ Global DB ready")
    print(f"    Primary:   {primary_endpoint}")
    print(f"    Secondary: {secondary_endpoint}")

    # ================================================================
    # STEP 7: Route 53 Health Checks + DNS
    # ================================================================
    print("\n[7/10] Creating Route 53 Health Checks and DNS Records...")

    # Health checks
    hc_primary = route53.create_health_check(
        CallerReference=f"net003-hc-primary-{int(time.time())}",
        HealthCheckConfig={
            'Type': 'RECOVERY_CONTROL',
            'RoutingControlArn': rc_primary_arn
        }
    )['HealthCheck']['Id']

    hc_standby = route53.create_health_check(
        CallerReference=f"net003-hc-standby-{int(time.time())}",
        HealthCheckConfig={
            'Type': 'RECOVERY_CONTROL',
            'RoutingControlArn': rc_standby_arn
        }
    )['HealthCheck']['Id']

    resources['hc_primary_id'] = hc_primary
    resources['hc_standby_id'] = hc_standby
    print(f"  HC Primary: {hc_primary}")
    print(f"  HC Standby: {hc_standby}")

    # Hosted Zone
    hz_resp = route53.create_hosted_zone(
        Name='net003-lab.internal',
        CallerReference=f"net003-hz-{int(time.time())}",
        HostedZoneConfig={'Comment': 'NET-003 ARC Lab', 'PrivateZone': False}
    )
    hz_id = hz_resp['HostedZone']['Id'].split('/')[-1]
    nameservers = hz_resp['DelegationSet']['NameServers']
    resources['hosted_zone_id'] = hz_id
    resources['nameservers'] = nameservers
    print(f"  Hosted Zone: {hz_id}")
    print(f"  Nameservers: {nameservers[0]}")

    # Failover Records
    route53.change_resource_record_sets(
        HostedZoneId=hz_id,
        ChangeBatch={
            'Changes': [
                {
                    'Action': 'CREATE',
                    'ResourceRecordSet': {
                        'Name': 'app.net003-lab.internal',
                        'Type': 'CNAME',
                        'SetIdentifier': 'primary-us-east-1',
                        'Failover': 'PRIMARY',
                        'TTL': 60,
                        'ResourceRecords': [{'Value': primary_endpoint}],
                        'HealthCheckId': hc_primary
                    }
                },
                {
                    'Action': 'CREATE',
                    'ResourceRecordSet': {
                        'Name': 'app.net003-lab.internal',
                        'Type': 'CNAME',
                        'SetIdentifier': 'standby-us-west-2',
                        'Failover': 'SECONDARY',
                        'TTL': 60,
                        'ResourceRecords': [{'Value': secondary_endpoint}],
                        'HealthCheckId': hc_standby
                    }
                }
            ]
        }
    )
    print("  ✓ DNS failover records created")

    # ================================================================
    # STEP 8: IAM Execution Role
    # ================================================================
    print("\n[8/10] Creating IAM Execution Role...")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "arc-region-switch.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Aurora",
                "Effect": "Allow",
                "Action": [
                    "rds:SwitchoverGlobalCluster",
                    "rds:FailoverGlobalCluster",
                    "rds:DescribeGlobalClusters",
                    "rds:DescribeDBClusters",
                    "rds:DescribeDBInstances"
                ],
                "Resource": "*"
            },
            {
                "Sid": "ARCDataPlane",
                "Effect": "Allow",
                "Action": [
                    "route53-recovery-cluster:UpdateRoutingControlState",
                    "route53-recovery-cluster:UpdateRoutingControlStates",
                    "route53-recovery-cluster:GetRoutingControlState",
                    "route53-recovery-cluster:ListRoutingControls"
                ],
                "Resource": "*"
            },
            {
                "Sid": "ARCControlPlane",
                "Effect": "Allow",
                "Action": [
                    "route53-recovery-control-config:DescribeCluster",
                    "route53-recovery-control-config:DescribeControlPanel",
                    "route53-recovery-control-config:DescribeRoutingControl",
                    "route53-recovery-control-config:ListClusters",
                    "route53-recovery-control-config:ListRoutingControls"
                ],
                "Resource": "*"
            },
            {
                "Sid": "Route53",
                "Effect": "Allow",
                "Action": [
                    "route53:GetHealthCheck",
                    "route53:GetHealthCheckStatus",
                    "route53:ListHealthChecks"
                ],
                "Resource": "*"
            },
            {
                "Sid": "IAMSelfInspection",
                "Effect": "Allow",
                "Action": [
                    "iam:SimulatePrincipalPolicy",
                    "iam:GetRole",
                    "iam:GetRolePolicy",
                    "iam:ListAttachedRolePolicies",
                    "iam:ListRolePolicies",
                    "iam:GetPolicy",
                    "iam:GetPolicyVersion",
                    "iam:PassRole"
                ],
                "Resource": "*"
            }
        ]
    }

    try:
        role_resp = iam.create_role(
            RoleName='NET003-RegionSwitchExecutionRole',
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
    except iam.exceptions.EntityAlreadyExistsException:
        role_resp = {'Role': {'Arn': f"arn:aws:iam::{account_id}:role/NET003-RegionSwitchExecutionRole"}}

    role_arn = role_resp['Role']['Arn'] if 'Role' in role_resp else f"arn:aws:iam::{account_id}:role/NET003-RegionSwitchExecutionRole"
    resources['execution_role_arn'] = role_arn

    iam.put_role_policy(
        RoleName='NET003-RegionSwitchExecutionRole',
        PolicyName='RegionSwitchPermissions',
        PolicyDocument=json.dumps(permissions_policy)
    )
    print(f"  ✓ Role: {role_arn}")

    # Wait for IAM propagation
    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)

    # ================================================================
    # STEP 9: Region Switch Plan
    # ================================================================
    print("\n[9/10] Creating Region Switch Plan...")

    plan_resp = arc_rs.create_plan(
        name='net003-aurora-failover',
        description='Aurora Global DB switchover + ARC routing control failover',
        regions=['us-east-1', 'us-west-2'],
        primaryRegion='us-east-1',
        recoveryApproach='activePassive',
        executionRole=role_arn,
        workflows=[{
            'workflowTargetAction': 'activate',
            'steps': [
                {
                    'name': 'Switchover Aurora Global DB',
                    'description': 'Failover Aurora writer to target region',
                    'executionBlockType': 'AuroraGlobalDatabase',
                    'executionBlockConfiguration': {
                        'globalAuroraConfig': {
                            'timeoutMinutes': 15,
                            'behavior': 'switchoverOnly',
                            'globalClusterIdentifier': 'net003-global-db',
                            'databaseClusterArns': [primary_cluster_arn, secondary_cluster_arn]
                        }
                    }
                },
                {
                    'name': 'Switch routing controls',
                    'description': 'Turn ON activating region RC and turn OFF deactivating region RC',
                    'executionBlockType': 'ARCRoutingControl',
                    'executionBlockConfiguration': {
                        'arcRoutingControlConfig': {
                            'timeoutMinutes': 5,
                            'regionAndRoutingControls': {
                                'us-east-1': [
                                    {'routingControlArn': rc_primary_arn, 'state': 'On'},
                                    {'routingControlArn': rc_standby_arn, 'state': 'Off'}
                                ],
                                'us-west-2': [
                                    {'routingControlArn': rc_standby_arn, 'state': 'On'},
                                    {'routingControlArn': rc_primary_arn, 'state': 'Off'}
                                ]
                            }
                        }
                    }
                }
            ]
        }]
    )
    plan_arn = plan_resp['plan']['arn']
    resources['plan_arn'] = plan_arn
    print(f"  ✓ Plan: {plan_arn}")

    # ================================================================
    # STEP 10: Summary
    # ================================================================
    print("\n[10/10] Deployment Complete!")
    print("=" * 60)
    print("\nSteady State:")
    print(f"  Aurora Writer:      us-east-1 (net003-primary-cluster)")
    print(f"  Aurora Reader:      us-west-2 (net003-secondary-cluster)")
    print(f"  RC Primary:         ON  (traffic → us-east-1)")
    print(f"  RC Standby:         OFF (no traffic → us-west-2)")
    print(f"\nDNS Test:")
    print(f"  dig app.net003-lab.internal @{nameservers[0]} +short")
    print(f"\nTo execute failover to us-west-2:")
    print(f"  aws arc-region-switch start-plan-execution \\")
    print(f"    --plan-arn {plan_arn} \\")
    print(f"    --target-region us-west-2 \\")
    print(f"    --action activate \\")
    print(f"    --mode graceful \\")
    print(f"    --region us-west-2 --profile {profile}")
    print(f"\nTo failover BACK to us-east-1:")
    print(f"  aws arc-region-switch start-plan-execution \\")
    print(f"    --plan-arn {plan_arn} \\")
    print(f"    --target-region us-east-1 \\")
    print(f"    --action activate \\")
    print(f"    --mode graceful \\")
    print(f"    --region us-west-2 --profile {profile}")
    print("=" * 60)

    # Save resources to file for cleanup
    with open('resources.json', 'w') as f:
        json.dump(resources, f, indent=2)
    print(f"\nResource IDs saved to resources.json (needed for cleanup.py)")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Deploy NET-003 ARC + Aurora failover lab')
    parser.add_argument('--profile', required=True, help='AWS CLI profile name')
    parser.add_argument('--account-id', required=True, help='AWS account ID')
    args = parser.parse_args()

    deploy(args.profile, args.account_id)
