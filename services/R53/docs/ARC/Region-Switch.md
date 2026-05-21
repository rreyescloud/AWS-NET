# ARC Region Switch

## What is Region Switch

Region Switch orchestrates multi-step failovers automatically. Instead of manually executing each step (DB failover, routing control changes, scaling), you define a plan and execute it with one command.

## Plan Structure

```
Plan
  ├── Regions: ["us-east-1", "us-west-2"]
  ├── Primary Region: us-east-1
  ├── Recovery Approach: activePassive | activeActive
  ├── Execution Role: arn:aws:iam::ACCOUNT:role/ROLE
  └── Workflows:
       └── Workflow (workflowTargetAction: activate)
            ├── Step 1: Aurora switchover
            ├── Step 2: Routing controls switch
            └── Step 3: ECS scaling (optional)
```

## Execution Block Types

| Type | What it does |
|---|---|
| `AuroraGlobalDatabase` | Switchover or failover Aurora Global DB |
| `ARCRoutingControl` | Change routing control states (ON/OFF) |
| `ECSServiceScaling` | Scale ECS services in target region |
| `EC2AutoScaling` | Scale EC2 ASGs |
| `ManualApproval` | Pause for human confirmation |
| `CustomActionLambda` | Run custom Lambda logic |
| `Route53HealthCheck` | Manage health checks |
| `Parallel` | Run multiple steps simultaneously |

## regionAndRoutingControls — Bidirectional Mapping

This is the most critical and confusing part. The map defines what state controls should be in WHEN EACH REGION IS ACTIVATED:

```json
"regionAndRoutingControls": {
  "us-east-1": [
    {"routingControlArn": "RC-Primary", "state": "On"},
    {"routingControlArn": "RC-Standby", "state": "Off"}
  ],
  "us-west-2": [
    {"routingControlArn": "RC-Standby", "state": "On"},
    {"routingControlArn": "RC-Primary", "state": "Off"}
  ]
}
```

When you execute `--target-region us-west-2 --action activate`:
- Engine uses the "us-west-2" entry
- Sets RC-Standby → On, RC-Primary → Off

When you execute `--target-region us-east-1 --action activate`:
- Engine uses the "us-east-1" entry
- Sets RC-Primary → On, RC-Standby → Off

### CRITICAL: Use a SINGLE step with BOTH controls

DO NOT split ON and OFF into separate steps. The engine may select the wrong entry or execute out of order. Put both changes in one ARCRoutingControl step.

## IAM Execution Role — Critical Permissions

The execution role needs MORE than just the service permissions. It must be able to **self-inspect its own policy**.

### Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "arc-region-switch.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
```

### Required Permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Aurora",
      "Effect": "Allow",
      "Action": ["rds:SwitchoverGlobalCluster", "rds:FailoverGlobalCluster", "rds:DescribeGlobalClusters", "rds:DescribeDBClusters", "rds:DescribeDBInstances"],
      "Resource": "*"
    },
    {
      "Sid": "ARCDataPlane",
      "Effect": "Allow",
      "Action": ["route53-recovery-cluster:UpdateRoutingControlState", "route53-recovery-cluster:UpdateRoutingControlStates", "route53-recovery-cluster:GetRoutingControlState", "route53-recovery-cluster:ListRoutingControls"],
      "Resource": "*"
    },
    {
      "Sid": "ARCControlPlane",
      "Effect": "Allow",
      "Action": ["route53-recovery-control-config:DescribeCluster", "route53-recovery-control-config:DescribeControlPanel", "route53-recovery-control-config:DescribeRoutingControl", "route53-recovery-control-config:ListClusters", "route53-recovery-control-config:ListRoutingControls"],
      "Resource": "*"
    },
    {
      "Sid": "IAMSelfInspection",
      "Effect": "Allow",
      "Action": ["iam:SimulatePrincipalPolicy", "iam:GetRole", "iam:GetRolePolicy", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies", "iam:GetPolicy", "iam:GetPolicyVersion", "iam:PassRole"],
      "Resource": "*"
    }
  ]
}
```

### Why IAM Self-Inspection is Critical

Region Switch validates permissions BEFORE executing. Without the IAM read permissions, it cannot verify it has the right access and fails with a misleading error:

```
"Region switch does not have endpoints for Routing Control <ARN>. 
Verify your plan's execution IAM role has the necessary permissions and try again."
```

This error says "endpoints" but the real problem is IAM. The service needs `iam:SimulatePrincipalPolicy` to test its own permissions against the routing control resources.

## Common Errors

### "does not have endpoints for Routing Control"
- **Cause**: Missing IAM self-inspection permissions
- **Fix**: Add iam:SimulatePrincipalPolicy, iam:GetRole, iam:GetRolePolicy, etc.

### "not authorized to access control panel"
- **Cause**: Missing ARC control plane permissions
- **Fix**: Add route53-recovery-control-config:Describe* and List* with Resource: "*"

### "executionFailed / unknown step"
- **Cause**: Step definition invalid or permissions prevent endpoint resolution
- **Fix**: Check list-plan-execution-events for the real error

### Plan keeps failing across multiple versions
- **Cause**: Usually IAM, not the plan definition
- **Fix**: Verify ALL permission blocks before iterating the plan

## Execution Commands

```bash
# Start execution
aws arc-region-switch start-plan-execution \
  --plan-arn <plan-arn> \
  --target-region "us-west-2" \
  --action "activate" \
  --mode "graceful" \
  --region us-west-2

# Monitor execution
aws arc-region-switch get-plan-execution \
  --plan-arn <plan-arn> \
  --execution-id <execution-id> \
  --region us-west-2

# Get detailed events (shows real errors)
aws arc-region-switch list-plan-execution-events \
  --plan-arn <plan-arn> \
  --execution-id <execution-id> \
  --region us-west-2

# Cancel a stuck execution
aws arc-region-switch cancel-plan-execution \
  --plan-arn <plan-arn> \
  --execution-id <execution-id> \
  --region us-west-2
```

## Troubleshooting Flow

```
1. CloudTrail: Look for UpdateRoutingControlState(s) events
   ├── Has event with errorCode → read the error
   ├── Has event without error but state didn't change → propagation issue
   └── NO event → the API was never called → workflow problem

2. list-plan-execution-events: Get the REAL error
   ├── stepFailed → read the error field
   ├── planEvaluationWarning → IAM issues detected before execution
   └── stepUpdate → shows what the step was about to do

3. If "does not have endpoints" or "not authorized":
   → IAM problem, not ARC problem
   → Add self-inspection permissions
```

## References

- [ARC Region Switch](https://docs.aws.amazon.com/r53recovery/latest/dg/arc-region-switch.html)
- [Region Switch IAM permissions](https://docs.aws.amazon.com/r53recovery/latest/dg/security_iam_region_switch_aurora.html)
- [Identity-based policy examples](https://docs.aws.amazon.com/r53recovery/latest/dg/security_iam_id-based-policy-examples-region-switch.html)
- [Best practices for Region Switch](https://docs.aws.amazon.com/r53recovery/latest/dg/best-practices.region-switch.html)
- [Aurora Global Database block](https://docs.aws.amazon.com/r53recovery/latest/dg/aurora-global-database-block.html)
- [Introducing ARC Region Switch (blog)](https://aws.amazon.com/blogs/aws/introducing-amazon-application-recovery-controller-region-switch-a-multi-region-application-recovery-service/)
- [Warm Standby DR with ARC + Aurora (Medium)](https://medium.com/@irinazarzu/implementing-warm-standby-for-aws-disaster-recovery-route-53-arc-aurora-global-database-s3-e72eb5af2037)
