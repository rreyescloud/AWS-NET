# NET-003: Multi-Region Automated Failover with ARC Region Switch + Aurora Global Database

## Objective

Design and validate an automated multi-region failover solution for a financial services workload using Amazon Application Recovery Controller (ARC) Region Switch to orchestrate Aurora Global Database switchover and DNS traffic rerouting in a single automated workflow.

This lab replicates a real production scenario where a customer's Region Switch workflow failed to complete the routing control OFF step, leaving the system in an unsafe split-traffic state.


## Business Context — Financial Services

A Tier 1 financial institution operates a critical banking application (payments processing, account management) that requires:

- **RPO < 1 second** (no data loss during failover)
- **RTO < 5 minutes** (automated, no manual intervention)
- **Regulatory compliance** (OCC/FFIEC resilience requirements mandate documented DR procedures)
- **Split-brain prevention** (two active writers = data corruption = regulatory incident)

The solution must guarantee that during failover:
1. The database writer moves to the DR region
2. Application traffic follows the database (not before, not after — in coordination)
3. The old region is fully deactivated (no stale traffic hitting a read-only replica)
4. The entire process is automated and auditable via CloudTrail


## Architecture

```
                    End Users
                       │
                       ▼
            ┌─── Route 53 DNS ───┐
            │   Failover Records  │
            ▼                     ▼
     PRIMARY record         SECONDARY record
     (app.example.com)      (app.example.com)
            │                     │
            ▼                     ▼
     Health Check            Health Check
     (RC Primary)            (RC Standby)
            │                     │
            ▼                     ▼
     Routing Control         Routing Control
     Primary-useast1         Standby-uswest2
     (ON = healthy)          (OFF = unhealthy)
            │                     │
            ▼                     ▼
     ALB us-east-1           ALB us-west-2
            │                     │
            ▼                     ▼
     Application             Application
     (ECS/EC2)              (ECS/EC2)
            │                     │
            ▼                     ▼
     Aurora Writer           Aurora Reader
     (us-east-1)  ──async──▶ (us-west-2)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

     ARC Region Switch Plan
     ┌─────────────────────────────────┐
     │ Step 1: Aurora Switchover       │
     │ Step 2: Routing Controls Switch │
     └─────────────────────────────────┘
```


## Use Cases

1. **Regional Disaster Recovery** — AWS Region experiences degraded services; automated failover shifts all traffic and database writes to the standby region within minutes, meeting regulatory RTO requirements.

2. **Planned Maintenance Failover** — Infrastructure upgrades or compliance patching in the primary region require graceful traffic migration with zero downtime and zero data loss.

3. **Regulatory DR Testing** — OCC/FFIEC requires annual DR testing with evidence. The automated plan provides auditable execution history via CloudTrail for compliance reporting.

4. **Incident Response Automation** — During a P1 incident affecting the primary region, the oncall engineer executes a single command (or clicks one button in console) instead of remembering a multi-step manual runbook under pressure.

5. **Active-Passive Cost Optimization** — Unlike active-active architectures, this pattern keeps the standby region warm but not serving traffic, reducing costs while maintaining rapid recovery capability.


## Implementation — Step by Step

### Prerequisites

- AWS account with permissions to create RDS, ARC, Route 53, and IAM resources
- Two regions selected (this lab: us-east-1 primary, us-west-2 standby)
- VPCs with subnets in at least 2 AZs per region


### Step 1: Enable CloudTrail

Verify CloudTrail is active and logging management events in all regions. This captures all API calls from the Region Switch workflow for audit and troubleshooting.

```bash
aws cloudtrail describe-trails --region us-east-1
aws cloudtrail get-trail-status --name <trail-name> --region us-east-1
```


### Step 2: Create ARC Cluster

The cluster is the data plane — 5 endpoints in 5 regions for ultra-high availability. Even if an entire region goes down, you can operate your routing controls from another endpoint.

```bash
aws route53-recovery-control-config create-cluster \
  --cluster-name "FailoverCluster" \
  --region us-west-2
```

Wait for status DEPLOYED (~5 minutes). Note the 5 cluster endpoints returned.


### Step 3: Create Control Panel and Routing Controls

```bash
# Control Panel
aws route53-recovery-control-config create-control-panel \
  --cluster-arn <cluster-arn> \
  --control-panel-name "AppFailoverPanel" \
  --region us-west-2

# Routing Control for primary region
aws route53-recovery-control-config create-routing-control \
  --cluster-arn <cluster-arn> \
  --control-panel-arn <panel-arn> \
  --routing-control-name "RC-Primary-useast1" \
  --region us-west-2

# Routing Control for standby region
aws route53-recovery-control-config create-routing-control \
  --cluster-arn <cluster-arn> \
  --control-panel-arn <panel-arn> \
  --routing-control-name "RC-Standby-uswest2" \
  --region us-west-2
```


### Step 4: Set Initial Routing Control State

```bash
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"<RC-Primary-ARN>","RoutingControlState":"On"},
    {"RoutingControlArn":"<RC-Standby-ARN>","RoutingControlState":"Off"}]' \
  --region us-west-2 \
  --endpoint-url "<cluster-endpoint>"
```


### Step 5: Create Safety Rule

Prevents both controls from being OFF simultaneously (split-brain protection).

```bash
aws route53-recovery-control-config create-safety-rule \
  --assertion-rule '{
    "AssertedControls": ["<RC-Primary-ARN>", "<RC-Standby-ARN>"],
    "ControlPanelArn": "<panel-arn>",
    "Name": "AtLeastOneOn",
    "RuleConfig": {"Inverted": false, "Threshold": 1, "Type": "ATLEAST"},
    "WaitPeriodMs": 5000
  }' \
  --region us-west-2
```


### Step 6: Create Aurora Global Database

```bash
# Primary cluster (us-east-1)
aws rds create-db-cluster \
  --db-cluster-identifier app-primary-cluster \
  --engine aurora-postgresql \
  --engine-version 16.4 \
  --master-username adminuser \
  --master-user-password <password> \
  --db-subnet-group-name <subnet-group> \
  --region us-east-1

# Primary instance
aws rds create-db-instance \
  --db-instance-identifier app-primary-instance-1 \
  --db-cluster-identifier app-primary-cluster \
  --db-instance-class db.r6g.large \
  --engine aurora-postgresql \
  --region us-east-1

# Convert to Global Database
aws rds create-global-cluster \
  --global-cluster-identifier app-global-db \
  --source-db-cluster-identifier arn:aws:rds:us-east-1:<account>:cluster:app-primary-cluster \
  --region us-east-1

# Secondary cluster (us-west-2)
aws rds create-db-cluster \
  --db-cluster-identifier app-secondary-cluster \
  --engine aurora-postgresql \
  --engine-version 16.4 \
  --global-cluster-identifier app-global-db \
  --db-subnet-group-name <subnet-group> \
  --region us-west-2

# Secondary instance
aws rds create-db-instance \
  --db-instance-identifier app-secondary-instance-1 \
  --db-cluster-identifier app-secondary-cluster \
  --db-instance-class db.r6g.large \
  --engine aurora-postgresql \
  --region us-west-2
```


### Step 7: Create Route 53 Health Checks and DNS Records

Health checks linked to routing controls:

```bash
# Health check for primary routing control
aws route53 create-health-check \
  --caller-reference "hc-primary-$(date +%s)" \
  --health-check-config '{
    "Type": "RECOVERY_CONTROL",
    "RoutingControlArn": "<RC-Primary-ARN>"
  }'

# Health check for standby routing control
aws route53 create-health-check \
  --caller-reference "hc-standby-$(date +%s)" \
  --health-check-config '{
    "Type": "RECOVERY_CONTROL",
    "RoutingControlArn": "<RC-Standby-ARN>"
  }'
```

DNS failover records:

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --change-batch '{
    "Changes": [
      {
        "Action": "CREATE",
        "ResourceRecordSet": {
          "Name": "app.example.com",
          "Type": "CNAME",
          "SetIdentifier": "primary",
          "Failover": "PRIMARY",
          "TTL": 60,
          "ResourceRecords": [{"Value": "<primary-cluster-endpoint>"}],
          "HealthCheckId": "<hc-primary-id>"
        }
      },
      {
        "Action": "CREATE",
        "ResourceRecordSet": {
          "Name": "app.example.com",
          "Type": "CNAME",
          "SetIdentifier": "standby",
          "Failover": "SECONDARY",
          "TTL": 60,
          "ResourceRecords": [{"Value": "<secondary-cluster-endpoint>"}],
          "HealthCheckId": "<hc-standby-id>"
        }
      }
    ]
  }'
```


### Step 8: Create IAM Execution Role for Region Switch

This is critical. The role needs permissions for Aurora, ARC, Route 53, AND IAM self-inspection.

```bash
# Create role with trust policy
aws iam create-role \
  --role-name RegionSwitchExecutionRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "arc-region-switch.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach permissions policy
aws iam put-role-policy \
  --role-name RegionSwitchExecutionRole \
  --policy-name RegionSwitchPermissions \
  --policy-document '{
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
  }'
```


### Step 9: Create Region Switch Plan

The plan uses a single routing control step with a bidirectional mapping:

```bash
aws arc-region-switch create-plan \
  --name "app-failover-plan" \
  --description "Aurora Global DB switchover + routing control failover" \
  --regions '["us-east-1","us-west-2"]' \
  --primary-region "us-east-1" \
  --recovery-approach "activePassive" \
  --execution-role "arn:aws:iam::<account>:role/RegionSwitchExecutionRole" \
  --workflows '[
    {
      "workflowTargetAction": "activate",
      "steps": [
        {
          "name": "Switchover Aurora Global DB",
          "executionBlockType": "AuroraGlobalDatabase",
          "executionBlockConfiguration": {
            "globalAuroraConfig": {
              "timeoutMinutes": 15,
              "behavior": "switchoverOnly",
              "globalClusterIdentifier": "app-global-db",
              "databaseClusterArns": [
                "arn:aws:rds:us-east-1:<account>:cluster:app-primary-cluster",
                "arn:aws:rds:us-west-2:<account>:cluster:app-secondary-cluster"
              ]
            }
          }
        },
        {
          "name": "Switch routing controls",
          "executionBlockType": "ARCRoutingControl",
          "executionBlockConfiguration": {
            "arcRoutingControlConfig": {
              "timeoutMinutes": 5,
              "regionAndRoutingControls": {
                "us-east-1": [
                  {"routingControlArn": "<RC-Primary-ARN>", "state": "On"},
                  {"routingControlArn": "<RC-Standby-ARN>", "state": "Off"}
                ],
                "us-west-2": [
                  {"routingControlArn": "<RC-Standby-ARN>", "state": "On"},
                  {"routingControlArn": "<RC-Primary-ARN>", "state": "Off"}
                ]
              }
            }
          }
        }
      ]
    }
  ]' \
  --region us-west-2
```


### Step 10: Execute the Region Switch

```bash
aws arc-region-switch start-plan-execution \
  --plan-arn <plan-arn> \
  --target-region "us-west-2" \
  --action "activate" \
  --mode "graceful" \
  --region us-west-2
```

Monitor execution:

```bash
aws arc-region-switch get-plan-execution \
  --plan-arn <plan-arn> \
  --execution-id <execution-id> \
  --region us-west-2
```

Verify with DNS:

```bash
dig app.example.com +short
```


## Things NOT to Do

### IAM Execution Role

- **DO NOT omit `iam:SimulatePrincipalPolicy` and IAM read permissions.** Region Switch needs to self-inspect its own policy before executing. Without these, it fails with a misleading error: "does not have endpoints for Routing Control... Verify your plan's execution IAM role has the necessary permissions." The error does not mention IAM — it blames the routing control.

- **DO NOT use restrictive Resource ARNs for ARC data plane permissions.** The routing control data plane requires `Resource: "*"` because the cluster endpoints are dynamically resolved. Scoped ARNs will cause "not authorized to access control panel" warnings during plan evaluation.

- **DO NOT forget the trust policy for `arc-region-switch.amazonaws.com`.** Without it, the workflow cannot assume the role at all and fails silently.

### Plan Configuration

- **DO NOT split routing control ON and OFF into separate steps.** The engine may select the wrong region entry or execute them out of order, leaving both controls in the same state. Use a single ARCRoutingControl step with BOTH controls (ON + OFF) in the same regionAndRoutingControls entry.

- **DO NOT put a single control per region entry.** Each region entry must contain ALL routing control changes for that failover direction (both the ON and the OFF).

- **DO NOT iterate plan versions blindly.** If the plan fails 2+ times with the same error pattern, the problem is likely IAM permissions or a fundamental configuration issue — not the step definition.

### Aurora Global Database

- **DO NOT attempt a switchover if both clusters are not in `available` state.** The switchover will fail or hang.

- **DO NOT forget to create DB instances in both clusters.** Region Switch validates that instances exist before executing the Aurora block. A cluster without instances will fail plan evaluation.

- **DO NOT mix engine versions between primary and secondary clusters.** The switchover requires identical Major.Minor.Patch versions.

### Routing Controls and Safety Rules

- **DO NOT leave the system without a safety rule.** Without "at least 1 ON" assertion, a misconfigured workflow can leave both controls OFF — meaning zero traffic reaches either region.

- **DO NOT operate routing controls without specifying the cluster endpoint-url.** The standard regional endpoint (`route53-recovery-cluster.<region>.amazonaws.com`) will fail. You must use the specific cluster endpoint.

### General

- **DO NOT execute a Region Switch without verifying steady state first.** If the system is already in a partially-failed state from a previous execution, the next execution will produce unpredictable results.

- **DO NOT assume "completed" means "correct".** Always verify the final state (Aurora writer location + routing control states + DNS resolution) after execution.


## Troubleshooting — Lessons from Production Cases

### Error: "does not have endpoints for Routing Control"

Root cause: IAM execution role missing self-inspection permissions.
Fix: Add `iam:SimulatePrincipalPolicy`, `iam:GetRole`, `iam:GetRolePolicy`, `iam:ListAttachedRolePolicies`, `iam:ListRolePolicies`, `iam:GetPolicy`, `iam:GetPolicyVersion`.

### Error: "not authorized to access control panel"

Root cause: ARC control plane permissions missing or scoped too narrowly.
Fix: Add `route53-recovery-control-config:Describe*` and `List*` with `Resource: "*"`.

### Workflow shows "executionFailed" but no CloudTrail event for the OFF

Root cause: The workflow engine failed internally before making the API call. This means the step definition is invalid or permissions prevent the engine from resolving cluster endpoints.
Fix: Check plan evaluation warnings via `list-plan-execution-events`.

### Both routing controls end up ON after execution

Root cause: Routing control steps split into separate ON/OFF steps with incorrect region mapping.
Fix: Combine into a single step with both ON and OFF in the same regionAndRoutingControls entry.

### Aurora switchover succeeds but routing controls don't change

Root cause: The execution role has Aurora permissions but lacks ARC permissions. Aurora step completes, ARC step fails, leaving the system in split-brain state.
Fix: Verify ALL permission blocks in the execution role before first execution.


## Cost Considerations

| Resource | Cost | Notes |
|---|---|---|
| ARC Cluster | ~$12.50/hr | 5 endpoints x $0.0025/hr each. Always-on. |
| Aurora Primary (db.r6g.large) | ~$0.26/hr | Scales with instance class |
| Aurora Secondary (db.r6g.large) | ~$0.26/hr | Same class recommended |
| Health Checks (2x Recovery Control) | ~$0.75/month each | Minimal |
| Route 53 Hosted Zone | $0.50/month | Per zone |

For a production financial workload, the ARC cluster cost (~$9,000/month) is justified by the regulatory requirement for automated DR with auditable execution.


## Files

| File | Purpose |
|---|---|
| README.md | This document |
| architecture.drawio | Diagram focused on the failure scenario |
| architecture_full.drawio | Full architecture: ARC + Aurora + Route 53 flow |
| correspondence_01.md | Support correspondence for the original case |
| test_commands.sh | Lab commands for ARC routing control testing |


## References

[1] Introducing Amazon Application Recovery Controller Region Switch
https://aws.amazon.com/blogs/aws/introducing-amazon-application-recovery-controller-region-switch-a-multi-region-application-recovery-service/

[2] Implementing Warm Standby for AWS Disaster Recovery (Route 53, ARC, Aurora Global Database, S3)
https://medium.com/@irinazarzu/implementing-warm-standby-for-aws-disaster-recovery-route-53-arc-aurora-global-database-s3-e72eb5af2037

[3] Aurora Global Database block in ARC Region Switch
https://docs.aws.amazon.com/r53recovery/latest/dg/aurora-global-database-block.html

[4] IAM permissions for Region Switch with Aurora
https://docs.aws.amazon.com/r53recovery/latest/dg/security_iam_region_switch_aurora.html

[5] Aurora Global Database Disaster Recovery (Failover)
https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html#aurora-global-database-failover

[6] Aurora Global Database Managed Failover
https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html#aurora-global-database-disaster-recovery.managed-failover

[7] Identity-based policy examples for Region Switch in ARC
https://docs.aws.amazon.com/r53recovery/latest/dg/security_iam_id-based-policy-examples-region-switch.html

[8] Best practices for Region Switch in ARC
https://docs.aws.amazon.com/r53recovery/latest/dg/best-practices.region-switch.html
