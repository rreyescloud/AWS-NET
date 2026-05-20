"""
NET-003: Cleanup script — destroys all resources created by deploy.py

Usage:
    python cleanup.py --profile <aws-profile>

Reads resource ARNs from resources.json (created by deploy.py).
If resources.json is missing, attempts to find resources by name convention (NET003-*).
"""

import boto3
import json
import time
import argparse
import sys
import os


def safe_delete(fn, resource_name):
    try:
        fn()
        print(f"  ✓ Deleted {resource_name}")
        return True
    except Exception as e:
        error_msg = str(e)
        if 'NotFound' in error_msg or 'NoSuch' in error_msg or 'does not exist' in error_msg:
            print(f"  - {resource_name} (already gone)")
        else:
            print(f"  ✗ {resource_name}: {error_msg}")
        return False


def wait_for_delete(describe_fn, resource_name, interval=10, timeout=300):
    elapsed = 0
    while elapsed < timeout:
        try:
            describe_fn()
            print(f"  ... waiting for {resource_name} deletion ({elapsed}s)")
            time.sleep(interval)
            elapsed += interval
        except Exception:
            print(f"  ✓ {resource_name} deleted")
            return True
    print(f"  ⚠ Timeout waiting for {resource_name} deletion")
    return False


def cleanup(profile):
    session = boto3.Session(profile_name=profile)

    east_rds = session.client('rds', region_name='us-east-1')
    west_rds = session.client('rds', region_name='us-west-2')
    arc_config = session.client('route53-recovery-control-config', region_name='us-west-2')
    route53 = session.client('route53')
    iam = session.client('iam')
    arc_rs = session.client('arc-region-switch', region_name='us-west-2')

    # Load resources.json if available
    resources = {}
    resources_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources.json')
    if os.path.exists(resources_file):
        with open(resources_file, 'r') as f:
            resources = json.load(f)
        print("Loaded resources.json")
    else:
        print("No resources.json found — will search by name convention")

    # ================================================================
    # STEP 1: Cancel any running Region Switch execution
    # ================================================================
    print("\n[1/8] Cancelling any running executions...")
    if resources.get('plan_arn'):
        try:
            executions = arc_rs.list_plan_executions(planArn=resources['plan_arn'])
            for ex in executions.get('items', []):
                if ex.get('executionState') in ('inProgress', 'pausedByFailedStep'):
                    arc_rs.cancel_plan_execution(
                        planArn=resources['plan_arn'],
                        executionId=ex['executionId']
                    )
                    print(f"  ✓ Cancelled execution {ex['executionId']}")
                    time.sleep(5)
        except Exception as e:
            print(f"  - No executions to cancel: {e}")

    # ================================================================
    # STEP 2: Delete Region Switch Plan
    # ================================================================
    print("\n[2/8] Deleting Region Switch Plan...")
    if resources.get('plan_arn'):
        safe_delete(
            lambda: arc_rs.delete_plan(arn=resources['plan_arn']),
            f"Plan {resources['plan_arn']}"
        )
    else:
        try:
            plans = arc_rs.list_plans()
            for p in plans.get('items', []):
                if 'net003' in p.get('name', '').lower():
                    safe_delete(lambda arn=p['arn']: arc_rs.delete_plan(arn=arn), p['name'])
        except Exception:
            pass

    # ================================================================
    # STEP 3: Delete IAM Role
    # ================================================================
    print("\n[3/8] Deleting IAM Execution Role...")
    role_name = 'NET003-RegionSwitchExecutionRole'
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName='RegionSwitchPermissions')
        print(f"  ✓ Deleted inline policy")
    except Exception:
        pass
    safe_delete(lambda: iam.delete_role(RoleName=role_name), role_name)

    # ================================================================
    # STEP 4: Delete Route 53 Resources
    # ================================================================
    print("\n[4/8] Deleting Route 53 Resources...")

    hz_id = resources.get('hosted_zone_id')
    if not hz_id:
        try:
            zones = route53.list_hosted_zones_by_name(DNSName='net003-lab.internal')
            for z in zones['HostedZones']:
                if 'net003-lab.internal' in z['Name']:
                    hz_id = z['Id'].split('/')[-1]
                    break
        except Exception:
            pass

    if hz_id:
        # Delete failover records first
        try:
            records = route53.list_resource_record_sets(HostedZoneId=hz_id)
            changes = []
            for r in records['ResourceRecordSets']:
                if r['Type'] in ('CNAME', 'A') and 'app.' in r.get('Name', ''):
                    changes.append({'Action': 'DELETE', 'ResourceRecordSet': r})
            if changes:
                route53.change_resource_record_sets(
                    HostedZoneId=hz_id,
                    ChangeBatch={'Changes': changes}
                )
                print(f"  ✓ Deleted DNS records")
        except Exception as e:
            print(f"  - DNS records: {e}")

        safe_delete(lambda: route53.delete_hosted_zone(Id=hz_id), f"Hosted Zone {hz_id}")

    # Delete health checks
    hc_ids = [resources.get('hc_primary_id'), resources.get('hc_standby_id')]
    if not any(hc_ids):
        try:
            hcs = route53.list_health_checks()
            for hc in hcs['HealthChecks']:
                config = hc.get('HealthCheckConfig', {})
                if config.get('Type') == 'RECOVERY_CONTROL' and 'net003' in config.get('RoutingControlArn', '').lower():
                    hc_ids.append(hc['Id'])
        except Exception:
            pass

    for hc_id in hc_ids:
        if hc_id:
            safe_delete(lambda hid=hc_id: route53.delete_health_check(HealthCheckId=hid), f"Health Check {hc_id}")

    # ================================================================
    # STEP 5: Delete Aurora Global Database
    # ================================================================
    print("\n[5/8] Deleting Aurora Global Database...")

    # Remove secondary from global cluster first
    print("  Removing secondary cluster from global DB...")
    try:
        west_rds.remove_from_global_cluster(
            GlobalClusterIdentifier='net003-global-db',
            DbClusterIdentifier=f"arn:aws:rds:us-west-2:{session.client('sts').get_caller_identity()['Account']}:cluster:net003-secondary-cluster"
        )
        time.sleep(10)
    except Exception as e:
        if 'NotFound' not in str(e):
            print(f"  - Remove secondary: {e}")

    # Delete secondary instance
    safe_delete(
        lambda: west_rds.delete_db_instance(
            DBInstanceIdentifier='net003-secondary-instance-1',
            SkipFinalSnapshot=True
        ),
        "Secondary instance"
    )

    # Wait for instance deletion
    try:
        wait_for_delete(
            lambda: west_rds.describe_db_instances(DBInstanceIdentifier='net003-secondary-instance-1'),
            "Secondary instance",
            interval=15, timeout=300
        )
    except Exception:
        pass

    # Delete secondary cluster
    safe_delete(
        lambda: west_rds.delete_db_cluster(
            DBClusterIdentifier='net003-secondary-cluster',
            SkipFinalSnapshot=True
        ),
        "Secondary cluster"
    )
    time.sleep(10)

    # Remove primary from global cluster
    print("  Removing primary cluster from global DB...")
    try:
        east_rds.remove_from_global_cluster(
            GlobalClusterIdentifier='net003-global-db',
            DbClusterIdentifier=f"arn:aws:rds:us-east-1:{session.client('sts').get_caller_identity()['Account']}:cluster:net003-primary-cluster"
        )
        time.sleep(10)
    except Exception as e:
        if 'NotFound' not in str(e):
            print(f"  - Remove primary: {e}")

    # Delete global cluster
    safe_delete(
        lambda: east_rds.delete_global_cluster(GlobalClusterIdentifier='net003-global-db'),
        "Global cluster"
    )

    # Delete primary instance
    safe_delete(
        lambda: east_rds.delete_db_instance(
            DBInstanceIdentifier='net003-primary-instance-1',
            SkipFinalSnapshot=True
        ),
        "Primary instance"
    )

    try:
        wait_for_delete(
            lambda: east_rds.describe_db_instances(DBInstanceIdentifier='net003-primary-instance-1'),
            "Primary instance",
            interval=15, timeout=300
        )
    except Exception:
        pass

    # Delete primary cluster
    safe_delete(
        lambda: east_rds.delete_db_cluster(
            DBClusterIdentifier='net003-primary-cluster',
            SkipFinalSnapshot=True
        ),
        "Primary cluster"
    )

    # Delete subnet groups
    safe_delete(
        lambda: east_rds.delete_db_subnet_group(DBSubnetGroupName='net003-subnet-group'),
        "Subnet group us-east-1"
    )
    safe_delete(
        lambda: west_rds.delete_db_subnet_group(DBSubnetGroupName='net003-subnet-group'),
        "Subnet group us-west-2"
    )

    # ================================================================
    # STEP 6: Delete ARC Safety Rule
    # ================================================================
    print("\n[6/8] Deleting ARC Safety Rule...")
    if resources.get('safety_rule_arn'):
        safe_delete(
            lambda: arc_config.delete_safety_rule(SafetyRuleArn=resources['safety_rule_arn']),
            "Safety Rule"
        )
    else:
        try:
            if resources.get('panel_arn'):
                rules = arc_config.list_safety_rules(ControlPanelArn=resources['panel_arn'])
                for rule in rules.get('SafetyRules', []):
                    arn = rule.get('ASSERTION', {}).get('SafetyRuleArn') or rule.get('GATING', {}).get('SafetyRuleArn')
                    if arn:
                        safe_delete(lambda a=arn: arc_config.delete_safety_rule(SafetyRuleArn=a), "Safety Rule")
        except Exception:
            pass

    time.sleep(5)

    # ================================================================
    # STEP 7: Delete ARC Routing Controls + Control Panel
    # ================================================================
    print("\n[7/8] Deleting Routing Controls and Control Panel...")

    for rc_key in ['rc_primary_arn', 'rc_standby_arn']:
        if resources.get(rc_key):
            safe_delete(
                lambda arn=resources[rc_key]: arc_config.delete_routing_control(RoutingControlArn=arn),
                rc_key
            )

    time.sleep(5)

    if resources.get('panel_arn'):
        safe_delete(
            lambda: arc_config.delete_control_panel(ControlPanelArn=resources['panel_arn']),
            "Control Panel"
        )

    time.sleep(5)

    # ================================================================
    # STEP 8: Delete ARC Cluster
    # ================================================================
    print("\n[8/8] Deleting ARC Cluster (saves ~$12.50/hr)...")
    if resources.get('cluster_arn'):
        safe_delete(
            lambda: arc_config.delete_cluster(ClusterArn=resources['cluster_arn']),
            "ARC Cluster"
        )
    else:
        try:
            clusters = arc_config.list_clusters()
            for c in clusters['Clusters']:
                if 'NET003' in c['Name']:
                    safe_delete(
                        lambda arn=c['ClusterArn']: arc_config.delete_cluster(ClusterArn=arn),
                        c['Name']
                    )
        except Exception:
            pass

    # ================================================================
    # Done
    # ================================================================
    print("\n" + "=" * 60)
    print("Cleanup complete!")
    print("Note: ARC cluster deletion takes ~5 minutes to fully remove.")
    print("Verify in console that no resources remain.")
    print("=" * 60)

    # Remove resources file
    if os.path.exists(resources_file):
        os.remove(resources_file)
        print("Removed resources.json")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Cleanup NET-003 ARC + Aurora failover lab')
    parser.add_argument('--profile', required=True, help='AWS CLI profile name')
    args = parser.parse_args()

    cleanup(args.profile)
