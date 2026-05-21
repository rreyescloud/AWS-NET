# Route 53 ARC — Application Recovery Controller

## What is ARC

ARC provides manual control over failover decisions. Instead of relying on automatic health checks (which can have false positives), you decide when and where to move traffic.

- What is ARC? https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html
- Introducing ARC: https://aws.amazon.com/blogs/aws/amazon-route-53-application-recovery-controller/
- Sample code: https://github.com/aws-samples/arc-iad


## Components

```
Cluster (data plane, 5 endpoints in 5 regions)
  └── Control Panel (logical grouping)
       ├── Routing Controls (ON/OFF switches)
       │    └── Health Checks (bridge to Route 53 DNS)
       │         └── Failover Records (actual DNS routing)
       └── Safety Rules (prevent dangerous operations)
```


## How Routing Controls Connect to DNS

```
Routing Control: ON/OFF
       ↓
Health Check: Healthy/Unhealthy (driven by RC state)
       ↓
Route 53 Failover Record: Resolves/Doesn't resolve
       ↓
Client traffic goes or doesn't go to that region
```


## Cluster

- Data plane with **5 endpoints** in 5 different AWS regions
- Ultra-redundant: if one region goes down, use another endpoint
- Operations on routing controls go through cluster endpoints
- Must specify `--endpoint-url` when using CLI (not the standard regional endpoint)

```bash
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries '[...]' \
  --region us-west-2 \
  --endpoint-url "https://XXXXX.route53-recovery-cluster.us-west-2.amazonaws.com/v1"
```


## Control Panels

A control panel groups routing controls. Every cluster has a default control panel, and you can create custom ones for organization.


## Routing Controls

Simple ON/OFF switches:
- ON --> associated health check = Healthy --> DNS resolves to that region
- OFF --> associated health check = Unhealthy --> DNS stops resolving there

Can change individually or in batch (atomic):
```bash
aws route53-recovery-cluster update-routing-control-states \
  --update-routing-control-state-entries \
  '[{"RoutingControlArn":"<arn-1>","RoutingControlState":"On"},
    {"RoutingControlArn":"<arn-2>","RoutingControlState":"Off"}]'
```


## Safety Rules

Prevent dangerous operations:

### Assertion Rules

"At least N controls must be ON"
- Prevents all-off = no traffic anywhere
- Example: threshold=1, type=ATLEAST
- Ensures you never shut down all regions simultaneously

### Gating Rules

"Don't change these controls unless this other control is in X state"
- Used for approval workflows or dependencies
- Example: operator-approval gate must be ON before changing regional controls

### Override

Safety rules can be overridden with `--safety-rules-to-override '["<safety-rule-arn>"]'`
- Use with extreme caution -- only in true emergency scenarios


## Health Checks (Recovery Control Type)

```bash
aws route53 create-health-check \
  --caller-reference "hc-primary" \
  --health-check-config '{
    "Type": "RECOVERY_CONTROL",
    "RoutingControlArn": "<routing-control-arn>"
  }'
```

- Type: RECOVERY_CONTROL
- Status driven entirely by the routing control state (not by polling an endpoint)
- Cannot use `get-health-check-status` API (use console or CloudWatch instead)


## Cross-Account Health Check Association

**Problem:** ARC routing control health check is in Account A but the Route 53 record is in Account B. Error: "health check id does not belong to account B."

**Solution:** Use `change-resource-record-sets` in Account B referencing the health check ID from Account A. The health check ID is globally unique.

```bash
aws route53 change-resource-record-sets --hosted-zone-id <zone-id> --change-batch '{
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "example.com",
      "Type": "A",
      "SetIdentifier": "primary",
      "Failover": "PRIMARY",
      "HealthCheckId": "<health-check-id-from-account-A>",
      "AliasTarget": {
        "HostedZoneId": "<target-zone-id>",
        "DNSName": "<target-dns>",
        "EvaluateTargetHealth": true
      }
    }
  }]
}'
```

- Reference: https://repost.aws/knowledge-center/route-53-cross-account-health-checks
- change-resource-record-sets: https://docs.aws.amazon.com/cli/latest/reference/route53/change-resource-record-sets.html


## Automating Failover with CloudWatch Alarms

### Use Case

"Can we integrate CloudWatch alarms with ARC to automatically trigger failover when an alarm fires?"

### Options

1. **CloudWatch Alarm --> SNS --> Lambda --> `UpdateRoutingControlState` API**
2. Use ARC Readiness Check + Zonal Autoshift (for zonal failures)
3. Use Region Switch plans with triggers (for regional failures)

### Architecture

```
CloudWatch Alarm (ALARM state)
    --> SNS Topic
        --> Lambda Function
            --> UpdateRoutingControlState (OFF for unhealthy region)
                --> ARC Health Check (Unhealthy)
                    --> Route 53 removes region from DNS
```

### Key Considerations

- Safety rules prevent turning OFF all routing controls simultaneously
- Use calculated health checks or composite alarms for deep health signals
- Lambda function should call the ARC cluster endpoint to update routing control state
- Implement idempotency in the automation
- **Caution:** Fully automatic region failover should be carefully considered -- false positives can cause unnecessary failovers


## Multi-Region Active-Active Example

**Scenario:** Application runs in us-east-1 and eu-west-1 with geographic load balancing (active-active). Goal: detect unhealthy region and automatically shut down traffic while never shutting down both.

**Setup:**
1. Create ARC cluster (5 endpoints)
2. Create routing controls: RC-us-east-1 and RC-eu-west-1
3. Create assertion safety rule: "At least 1 of 2 routing controls must be ON"
4. Associate ARC health checks with Route 53 geolocation/failover records
5. Automate with CloudWatch alarms + Lambda to toggle routing controls


## Monitoring and Logging

- ARC information in CloudTrail: https://docs.aws.amazon.com/r53recovery/latest/dg/cloudtrail-routing.html#service-name-info-in-cloudtrail
- View CloudWatch metrics in ARC: https://docs.aws.amazon.com/r53recovery/latest/dg/cloudwatch-readiness.html#view-metric-data
- Logging and monitoring for readiness check: https://docs.aws.amazon.com/r53recovery/latest/dg/monitoring-readiness.html


## References

- ARC Routing Controls: https://docs.aws.amazon.com/r53recovery/latest/dg/routing-control.html
- Safety Rules: https://docs.aws.amazon.com/r53recovery/latest/dg/routing-control.safety-rules.html
- Cross-account health checks: https://repost.aws/knowledge-center/route-53-cross-account-health-checks
- ARC samples (GitHub): https://github.com/aws-samples/arc-iad
- Introducing ARC (blog): https://aws.amazon.com/blogs/aws/amazon-route-53-application-recovery-controller/
