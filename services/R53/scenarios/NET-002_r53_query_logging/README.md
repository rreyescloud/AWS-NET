# NET-002: Route 53 Resolver Query Logging — Common Issues and Troubleshooting

**Tier:** Case analysis — no deploy script
**Status:** Four problem categories documented, labs not yet built

## Problem Statement

Route 53 Resolver Query Logging is a critical feature for DNS visibility, compliance, and security monitoring. However, customers frequently encounter issues during configuration, particularly in multi-account environments. This case covers the 4 most common problem categories identified from real support cases.


## Business Use Cases

1. **Centralized Security Monitoring:** A SOC team needs all DNS query logs from 50+ accounts aggregated into a central logging account for threat detection (e.g., detecting C2 domain queries).

2. **Compliance Auditing:** A financial institution must demonstrate DNS query audit trails for PCI-DSS or SOX compliance across all VPCs.

3. **DNS Hygiene / Record Cleanup:** An operations team wants to identify unused DNS records that can be safely removed by analyzing query history.

4. **Incident Response:** After a security incident, the IR team needs to trace what DNS queries a compromised instance made (lateral movement, data exfiltration domains).

5. **Cost Allocation:** A shared-services team needs to identify which teams/VPCs generate the most DNS queries for cost attribution.

6. **Multi-Account Landing Zone:** An enterprise deploys query logging as part of their AWS Control Tower baseline across all accounts via RAM sharing.


## Problem Categories


### Category 1: Cross-Account Query Logging — Permissions and Configuration

**Symptoms:**
- `ACCESS_DENIED` when creating query log config with cross-account destination
- RAM-shared query log config shows status `FAILED` in member accounts
- `logs:CreateLogDelivery permission is missing` error
- Terraform intermittent failures on `aws_route53_resolver_query_log_config`

**Root Cause:**

Route 53 Resolver Query Logging uses **AWS Vended Logs V2** mechanism. The key misunderstanding is that it's NOT the customer's IAM role that writes logs — it's a **Service-Linked Role** (`AWSServiceRoleForLogDelivery`) managed by AWS. The destination (S3, CloudWatch Logs, Firehose) needs a **resource policy** granting this service permission.

**Required Configuration:**

1. Resource Policy on destination (CloudWatch Logs example):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowLogDelivery",
      "Effect": "Allow",
      "Principal": {
        "Service": "delivery.logs.amazonaws.com"
      },
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:REGION:ACCOUNT-ID:log-group:LOG-GROUP-NAME:*",
      "Condition": {
        "StringEquals": {
          "aws:SourceAccount": ["SOURCE-ACCOUNT-ID"]
        },
        "ArnLike": {
          "aws:SourceArn": "arn:aws:logs:REGION:SOURCE-ACCOUNT-ID:*"
        }
      }
    }
  ]
}
```

2. Resource Policy on destination (S3 example):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AWSLogDeliveryWrite",
      "Effect": "Allow",
      "Principal": {
        "Service": "delivery.logs.amazonaws.com"
      },
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::BUCKET-NAME/AWSLogs/ACCOUNT-ID/*",
      "Condition": {
        "StringEquals": {
          "s3:x-amz-acl": "bucket-owner-full-control",
          "aws:SourceAccount": ["SOURCE-ACCOUNT-ID"]
        }
      }
    },
    {
      "Sid": "AWSLogDeliveryCheck",
      "Effect": "Allow",
      "Principal": {
        "Service": "delivery.logs.amazonaws.com"
      },
      "Action": ["s3:GetBucketAcl", "s3:ListBucket"],
      "Resource": "arn:aws:s3:::BUCKET-NAME"
    }
  ]
}
```

3. Service-Linked Role must exist:
```bash
aws iam create-service-linked-role --aws-service-name route53resolver.amazonaws.com
aws iam create-service-linked-role --aws-service-name delivery.logs.amazonaws.com
```

4. For RAM sharing: each member account must also have the SLR and the destination must allow the member account as source.

**Key Facts:**
- Destination MUST be in the same region as the query log config
- Cross-region logging is NOT supported natively
- Console does NOT support cross-account setup — must use CLI or API
- IAM eventual consistency can cause intermittent failures (Terraform race condition)


### Category 2: Query Logs Not Appearing / Not Generated

**Symptoms:**
- Query logging enabled but no logs appear
- Specific queries known to occur are missing from logs
- CloudWatch Log Group exists but remains empty

**Root Causes:**

**A. Confusion between two types of DNS logging:**

**Public Hosted Zone Query Logging**
- Captures queries **to** your public hosted zone
- Scoped per hosted zone
- CloudWatch Logs only
- Does not capture private DNS, and does not capture queries for names outside your zone

**Resolver Query Logging**
- Captures **all** queries **from** instances in a VPC
- Scoped per VPC
- S3, CloudWatch Logs, or Firehose
- Captures private DNS and external DNS alike

A customer asking "why isn't my DNS logging working" has usually enabled the one that does not
answer their question. Establish which direction they care about — queries arriving at their zone,
or queries leaving their instances — before looking at any configuration.

**B. Custom DNS servers bypass VPC Resolver:**

If instances use custom DNS (e.g., Active Directory Domain Controllers) via DHCP Options Set, queries go directly to those servers and NEVER pass through the VPC DNS resolver. Resolver Query Logging only captures queries that hit the VPC resolver (the .2 address).

```
CAPTURED by Query Logging:
  EC2 → VPC DNS Resolver (10.0.0.2) → forward/resolve → destination
              ↑ logged here

NOT CAPTURED:
  EC2 → AD DNS Server (10.0.1.50) → resolve → destination
              ↑ bypasses VPC resolver entirely
```

**Solution for AD environments:** Configure conditional forwarding from AD to the VPC resolver for specific zones, or use Route 53 Resolver Endpoints to forward specific domains.

**C. VPC association in FAILED state:**

Check the association status:
```bash
aws route53resolver list-resolver-query-log-config-associations \
  --filters Name=ResolverQueryLogConfigId,Values=rqlc-XXXXXXX
```

If status is `FAILED`, check the `Error` field for the specific reason (usually permissions).


### Category 3: DNS Audit and Record Cleanup

**Symptoms:**
- Customer wants to know when a DNS record was last queried
- Need to identify unused records for safe removal

**Solution:**

1. Enable query logging (Resolver for private, Public Zone for public)
2. Wait a representative period (minimum 30 days recommended)
3. Query the logs:

CloudWatch Logs Insights:
```
fields @timestamp, query_name, query_type
| filter query_name = "old-record.example.com."
| stats count() as query_count by query_name
| sort query_count desc
```

S3 (Athena):
```sql
SELECT query_name, COUNT(*) as hits, MAX(query_timestamp) as last_seen
FROM dns_query_logs
WHERE query_name = 'old-record.example.com.'
GROUP BY query_name
```

**Limitation:** No historical data exists before logging is enabled. Must plan ahead.


### Category 4: Costs

**Symptoms:**
- Customer unsure about pricing impact of enabling query logging

**Cost Breakdown:**

- **Enabling Resolver Query Logging** — no charge for the feature itself
- **Route 53 per-query charge** — none additional
- **Delivery to S3** — Vended Logs ingest charge per GB, plus S3 storage (cheapest option)
- **Delivery to CloudWatch Logs** — Vended Logs ingest charge per GB, roughly double the S3 rate,
  plus CloudWatch Logs storage
- **Delivery to Firehose** — standard Firehose pricing

Check current published rates before quoting figures; the ratio between destinations is the stable
part, the absolute numbers are not.

**Volume estimate:** A moderately active VPC (~100 instances) can generate 1-10 GB of DNS logs per day depending on workload.

**Cost optimization tips:**
- Use S3 as destination (cheaper than CloudWatch Logs)
- Apply S3 lifecycle policies to move old logs to Glacier
- Filter logs with Firehose transformations if only specific queries matter
- Use Athena for ad-hoc analysis instead of keeping logs in CW Logs long-term


## Labs to Reproduce

### Lab 1: Basic Resolver Query Logging (same account)
- Create query log config → CW Logs
- Associate VPC
- Generate DNS queries
- Verify logs appear

### Lab 2: Cross-Account to S3
- Create S3 bucket in Account B with resource policy
- Create query log config in Account A pointing to Account B bucket
- Verify logs arrive cross-account

### Lab 3: RAM Sharing across Organization
- Create query log config in central account
- Share via RAM to org
- Associate VPCs in member accounts
- Verify centralized logging

### Lab 4: Custom DNS (AD) bypass scenario
- Create VPC with custom DHCP options (AD DNS)
- Enable Resolver Query Logging
- Show that queries to AD are NOT captured
- Configure Resolver forwarding as fix

### Lab 5: Terraform with SLR race condition
- Deploy query log config without pre-creating SLR
- Observe intermittent failure
- Fix with depends_on and SLR pre-creation


## Files

- `README.md` — this document
- `architecture.drawio` — the two logging planes, the delivery path, and where each failure mode sits
- `deploy.py` / `cleanup.py` — not yet written; the labs below are the plan for them


## References

- Route 53 Resolver Query Logging: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html
- Managing Resolver Query Logging Configurations: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logging-configurations-managing.html
- Public Hosted Zone Query Logging: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/query-logs.html
- Vended Logs (CloudWatch): https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/AWS-logs-and-resource-policy.html
- RAM Sharing for Route 53: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs-sharing.html
- Service-Linked Role for Log Delivery: https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/using-service-linked-roles-cwl.html
- Route 53 Profiles (multi-account management): https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/profiles.html
- IAM Eventual Consistency: https://docs.aws.amazon.com/IAM/latest/UserGuide/troubleshoot_general.html#troubleshoot_general_eventual-consistency
- Terraform aws_route53_resolver_query_log_config: https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/route53_resolver_query_log_config
- CloudWatch Logs Insights Query Syntax: https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/CWL_QuerySyntax.html
