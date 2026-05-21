# VPC Endpoints

## Types Comparison

| Feature | Gateway Endpoint | Interface Endpoint (PrivateLink) |
|---|---|---|
| Services | S3, DynamoDB only | 100+ AWS services |
| Mechanism | Prefix list route in route table | ENI in subnet + private DNS |
| Cost | Free | $0.01/hr per AZ + $0.01/GB |
| Cross-region | Same region ONLY | Yes (with --service-region) |
| DNS | No override | Overrides service DNS to private IP |
| HA | Automatic | One ENI per AZ (deploy in multiple AZs) |
| Cross-account | N/A | Yes (via Endpoint Services) |

---

## Gateway Endpoint

- Uses a prefix list (pl-xxxxx) added to route tables
- Prefix list contains S3/DynamoDB public IPs for the LOCAL region only
- Traffic to S3 in another region does NOT match the prefix list
- Free, no bandwidth limits
- Cannot be extended cross-region or cross-account

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxx \
  --service-name com.amazonaws.us-east-1.s3 \
  --route-table-ids rtb-xxx \
  --vpc-endpoint-type Gateway
```

---

## Interface Endpoint (PrivateLink) — Same Region

- Creates an ENI with a private IP in your subnet
- Private DNS override: service.region.amazonaws.com resolves to private IP
- Traffic stays on AWS backbone (never goes to internet)
- Requires `enableDnsSupport` and `enableDnsHostnames` on the VPC

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxx \
  --service-name com.amazonaws.us-east-1.ssm \
  --subnet-ids subnet-xxx \
  --vpc-endpoint-type Interface \
  --private-dns-enabled
```

---

## Cross-Region PrivateLink

### What it is

Cross-region PrivateLink allows you to access AWS services in ANOTHER region entirely over private connectivity (AWS backbone). Launched late 2024/early 2025.

### How it works

```
Your VPC (us-east-1)
  └── Interface Endpoint with --service-region us-east-2
       └── Creates ENI in your local subnet
       └── DNS override: s3.us-east-2.amazonaws.com → local private IP
       └── Traffic routes through AWS backbone to S3 in us-east-2
       └── Never touches the internet
```

### CLI

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id vpc-xxx \
  --service-name com.amazonaws.us-east-2.s3 \
  --service-region us-east-2 \
  --subnet-ids subnet-xxx \
  --vpc-endpoint-type Interface \
  --private-dns-enabled
```

### IAM / SCP Requirements

Cross-region endpoints require the `vpce:AllowMultiRegion` condition key. If your org uses SCPs, this must be explicitly allowed:

```json
{
  "Effect": "Allow",
  "Action": "ec2:CreateVpcEndpoint",
  "Resource": "*",
  "Condition": {
    "StringEquals": {
      "ec2:VpceServiceRegion": ["us-east-2", "eu-west-1"]
    }
  }
}
```

### Limitations

- Only works within the SAME partition (Commercial ↔ Commercial). Cannot cross partitions (e.g., Hong Kong to Ningxia/China)
- Not all services support cross-region — check the supported services list
- Gateway endpoints (S3/DynamoDB) do NOT support cross-region — must use Interface type
- Adds ~$0.01/GB data processing + cross-region transfer costs

### Common Misconception

Gateway endpoint does NOT provide cross-region access. The prefix list only contains IPs for the local region. Without a 0.0.0.0/0 route, cross-region S3 traffic is simply dropped. You MUST use an Interface endpoint with `--service-region` for cross-region.

### Use Cases from Production

1. **Private-only VPC accessing S3 cross-region** — No IGW, no NAT. Gateway endpoint covers same-region S3. Cross-region PrivateLink covers remote S3.
2. **Snowflake integration** — S3 in us-east-1 accessed from Snowflake in us-west-1 via PrivateLink.
3. **Disaster Recovery** — DR account in another region needs private access to production S3 buckets.

---

## Cross-Account PrivateLink (Endpoint Services)

### How it works

Provider creates an NLB + Endpoint Service. Consumer creates a VPC endpoint connecting to the service.

```
Provider Account:
  Application → NLB → VPC Endpoint Service (shared via ARN or allowlisted accounts)

Consumer Account:
  VPC Endpoint → connects to Provider's Endpoint Service → reaches Provider's NLB
```

### AZ ID Alignment (Critical)

AWS shuffles AZ names between accounts (us-east-1a in Account A may be a different physical zone than us-east-1a in Account B). Use AZ IDs (use1-az1, use1-az2) to ensure alignment.

**Problem:** Consumer creates endpoint but traffic goes to an NLB node with no targets (different physical AZ).

**Solutions:**
1. Provider expands endpoint service to ALL AZs
2. Consumer creates subnets matching provider's AZ IDs
3. Enable cross-zone load balancing on provider's NLB

### Cross-Zone Load Balancing

When PrivateLink routes traffic to an NLB node in the consumer's AZ, but the NLB has no targets in that AZ:
- **Without cross-zone LB:** Connection fails (no targets in that AZ)
- **With cross-zone LB:** NLB distributes to targets in other AZs

---

## Endpoint Policies

Restrict what actions are allowed through the endpoint:

```json
{
  "Statement": [{
    "Effect": "Allow",
    "Principal": "*",
    "Action": ["s3:GetObject", "s3:PutObject"],
    "Resource": "arn:aws:s3:::my-bucket/*"
  }]
}
```

**Key gotcha from cases:** When WAF association goes through a VPC endpoint, an undocumented internal action (`elasticloadbalancing:CreateWebACLAssociation`) must be allowed in the endpoint policy.

---

## Private API Gateway Through VPC Endpoints

- Private APIs are only accessible from within VPC via Interface Endpoint for `execute-api`
- **Gotcha:** Enabling private DNS on the execute-api endpoint causes ALL API Gateway requests in the VPC to route through the interface endpoint — blocking access to public APIs
- Solution: Use Route 53 aliases or disable private DNS and use endpoint-specific DNS names

---

## Quotas

- VPC endpoints per VPC: 50 (can request increase to 500+)
- VPC endpoint services per region: no hard limit
- Each NLB can only be associated with ONE endpoint service

---

## Troubleshooting

### Endpoint Not Resolving
- Check `enableDnsHostnames` and `enableDnsSupport` on VPC
- Verify private DNS is enabled on the endpoint
- Check for conflicting Private Hosted Zones

### Cross-Account Connection Failing
- Verify AZ ID alignment (not AZ name)
- Check endpoint service allowlist includes consumer account
- Verify NLB target health in provider account
- Enable cross-zone LB if targets not in all AZs

### Cross-Region Not Working
- Must use Interface type (not Gateway)
- Must include `--service-region` flag
- Check IAM/SCP allows `vpce:AllowMultiRegion`
- Not supported between different partitions

### 503 Errors Through Endpoint
- Check NLB target health
- Verify security groups allow traffic from endpoint ENI
- Check if issue is on the service side (not networking)

---

## References

### Core Documentation
- [Gateway endpoints for S3](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-s3.html)
- [Interface endpoints (PrivateLink)](https://docs.aws.amazon.com/vpc/latest/privatelink/create-interface-endpoint.html)
- [Endpoint policies](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-access.html)
- [VPC endpoint limits](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-limits-endpoints.html)
- [PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/)

### Cross-Region
- [Introducing cross-region connectivity for PrivateLink](https://aws.amazon.com/blogs/networking-and-content-delivery/introducing-cross-region-connectivity-for-aws-privatelink/)
- [Cross-region PrivateLink extends connectivity](https://aws.amazon.com/blogs/networking-and-content-delivery/aws-privatelink-extends-cross-region-connectivity-to-aws-services/)
- [Cross-region PrivateLink supported services](https://docs.aws.amazon.com/vpc/latest/privatelink/aws-services-cross-region-privatelink-support.html)
- [Enabling cross-region private access to S3](https://aws.amazon.com/blogs/networking-and-content-delivery/enabling-cross-region-private-access-to-amazon-s3-with-existing-application-configuration/)
- [Cross-region PrivateLink announcement](https://aws.amazon.com/about-aws/whats-new/2025/11/aws-privatelink-cross-region-connectivity-aws-services/)

### Cross-Account / AZ Alignment
- [AZ IDs documentation](https://docs.aws.amazon.com/global-infrastructure/latest/regions/az-ids.html)
- [Interface endpoint availability zone issues](https://repost.aws/knowledge-center/interface-endpoint-availability-zone)
- [Consistent AZ IDs across accounts](https://docs.aws.amazon.com/prescriptive-guidance/latest/patterns/use-consistent-availability-zones-in-vpcs-across-different-aws-accounts.html)
- [Cross-zone load balancing for NLB](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/target-group-cross-zone.html)
- [RAM working with AZ IDs](https://docs.aws.amazon.com/ram/latest/userguide/working-with-az-ids.html)
- [Anti-patterns for SaaS network access](https://docs.aws.amazon.com/prescriptive-guidance/latest/saas-network-access-options/anti-patterns.html)

### DNS / Failover
- [DNS mechanisms for PrivateLink failover](https://aws.amazon.com/blogs/apn/reviewing-dns-mechanisms-for-routing-traffic-and-enabling-failover-for-aws-privatelink-deployments/)
- [S3 PrivateLink for Snowflake](https://aws.amazon.com/blogs/apn/using-aws-privatelink-for-amazon-s3-for-private-connectivity-between-snowflake-and-amazon-s3/)

### Troubleshooting
- [VPC endpoint interface creation errors](https://repost.aws/knowledge-center/vpc-interface-endpoint-creation-errors)
- [Private API Gateway connections](https://repost.aws/knowledge-center/api-gateway-private-endpoint-connection)
- [Private API Gateway VPC connections](https://repost.aws/knowledge-center/api-gateway-vpc-connections)
