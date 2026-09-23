# NET-001: S3 Private Cross-Account Cross-Region Connectivity

**Tier:** Lab — reproducible end to end
**Status:** Resolved — validated in the lab account

## Objective

Design and validate a fully private S3 connectivity solution between two AWS accounts in different regions, ensuring data never traverses the public internet. The solution must meet the strict network security requirements of a regulated fintech platform.


## Business Context — Financial Technology (Banking Software)

Client in the fintech industry providing core banking, lending, and wealth management platforms to financial institutions worldwide. Their AWS architecture spans multiple accounts and regions for:

- **Data residency compliance** — Customer banking data stored in region-specific buckets per jurisdiction (EU data in eu-west-1, US data in us-east-1)
- **Cross-region analytics** — Central analytics platform in one account/region needs private access to transaction data in multiple regional buckets
- **Audit trail replication** — Regulatory requirement to replicate financial transaction logs across regions without internet exposure
- **PCI-DSS compliance** — Cardholder data must travel only over private network paths with encryption in transit
- **Multi-account isolation** — Production, analytics, and DR environments in separate accounts with strict network boundaries


## Use Cases

1. **Core Banking Cross-Region DR** — Production account (us-east-1) replicates transaction journals to DR account (us-east-2) via private-only paths. Regulators require proof that no data exits the AWS backbone.

2. **Anti-Money Laundering (AML) Analytics** — AML detection platform in a central analytics account needs access to transaction data stored in regional production buckets without creating internet-facing paths.

3. **Cross-Border Regulatory Reporting** — A banking platform operating in US and EU must transfer aggregated reports between regions privately for consolidated regulatory submissions.

4. **Third-Party Fintech Integration** — A payment processor (Account A) shares settlement files with a core banking system (Account B) in a different region. Both parties require private-only connectivity per their security policies.

5. **Disaster Recovery Validation** — DR drills require reading production S3 data from a DR account/region to verify backup integrity — all without exposing traffic to public networks.


## Solution: Cross-Region PrivateLink for S3

After evaluating 7 options (documented below), Cross-Region PrivateLink provides the best balance of security, simplicity, and cost for private cross-region S3 access.


## Architecture

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FVPC%2Fscenarios%2FNET-001_s3_crossaccount%2Farchitecture.drawio)

```
VPC (us-east-1) — No IGW, No NAT, No Internet
│
├── Private Subnet (10.0.1.0/24)
│   └── EC2 Instance (10.0.1.84)
│       - No public IP
│       - Zero internet access
│       - IAM Role: S3 full access
│
├── S3 Gateway Endpoint (com.amazonaws.us-east-1.s3)
│   - Type: Gateway (free)
│   - Scope: S3 us-east-1 ONLY (same region)
│
├── S3 Cross-Region PrivateLink Endpoint (com.amazonaws.us-east-2.s3)
│   - Type: Interface
│   - Flag: --service-region us-east-2
│   - Mechanism: Private DNS override
│   - Scope: S3 us-east-2 (cross-region, private)
│
├── SSM Interface Endpoints (3x, for remote access without internet)
│   - com.amazonaws.us-east-1.ssm
│   - com.amazonaws.us-east-1.ssmmessages
│   - com.amazonaws.us-east-1.ec2messages
│
└── Route Table:
    - 10.0.0.0/16 → local
    - pl-63a5400a → Gateway Endpoint (S3 us-east-1 only)
    - (no 0.0.0.0/0 route — no internet)
```


## Implementation — Step by Step

### Step 1: Create VPC with No Internet Access

Create a VPC with private subnets only. No IGW, no NAT Gateway. This guarantees all traffic stays private.

```bash
aws ec2 create-vpc --cidr-block 10.0.0.0/16 --region us-east-1
aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.0.1.0/24 --az us-east-1a
```

### Step 2: S3 Gateway Endpoint (Same-Region Access)

Free, uses prefix list routes. Only works for S3 in the same region.

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id <vpc-id> \
  --service-name com.amazonaws.us-east-1.s3 \
  --route-table-ids <rt-id> \
  --vpc-endpoint-type Gateway
```

### Step 3: S3 Cross-Region PrivateLink Endpoint

This is the key — creates a private ENI in your VPC that tunnels traffic to S3 in another region via AWS backbone.

```bash
aws ec2 create-vpc-endpoint \
  --vpc-id <vpc-id> \
  --service-name com.amazonaws.us-east-2.s3 \
  --service-region us-east-2 \
  --subnet-ids <subnet-id> \
  --vpc-endpoint-type Interface \
  --private-dns-enabled
```

### Step 4: SSM Endpoints (for Instance Access Without Internet)

```bash
for svc in ssm ssmmessages ec2messages; do
  aws ec2 create-vpc-endpoint \
    --vpc-id <vpc-id> \
    --service-name com.amazonaws.us-east-1.$svc \
    --subnet-ids <subnet-id> \
    --vpc-endpoint-type Interface \
    --private-dns-enabled
done
```

### Step 5: EC2 Instance with IAM Role

Launch in private subnet, no public IP, with an IAM role granting S3 access.

### Step 6: Cross-Account Bucket Policy

On the destination bucket (Account B, us-east-2):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCrossAccountAccess",
      "Effect": "Allow",
      "Principal": {"AWS": "arn:aws:iam::ACCOUNT-A-ID:root"},
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::bucket-b", "arn:aws:s3:::bucket-b/*"]
    },
    {
      "Sid": "DenyNonSSL",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": ["arn:aws:s3:::bucket-b", "arn:aws:s3:::bucket-b/*"],
      "Condition": {"Bool": {"aws:SecureTransport": "false"}}
    }
  ]
}
```

### Step 7: Validate

```bash
# From EC2 instance via SSM Session Manager:

# Same-region (Gateway Endpoint)
aws s3 ls s3://bucket-a-us-east-1/ --region us-east-1

# Cross-region (PrivateLink)
aws s3 ls s3://bucket-b-us-east-2/ --region us-east-2

# Verify no internet
curl -s --connect-timeout 3 https://www.google.com || echo "NO INTERNET"

# Verify DNS resolves to private IP
dig s3.us-east-2.amazonaws.com +short
# Should return 10.0.1.x (private ENI IP)
```


## Test Results

| Test | Result |
|---|---|
| DNS resolution: s3.us-east-2.amazonaws.com | 10.0.1.94 (private IP inside VPC) |
| List Bucket B (us-east-2) cross-region | SUCCESS |
| Write to Bucket B cross-region | SUCCESS |
| Read from Bucket B cross-region | SUCCESS |
| List Bucket A (us-east-1) same region | SUCCESS |
| Write/Read Bucket A same region | SUCCESS |
| curl https://www.google.com | FAILED — NO INTERNET (expected) |


## Key Findings

### 1. Gateway Endpoint = Same Region Only

The S3 Gateway Endpoint uses a prefix list containing only S3 public IPs for the local region. Traffic to S3 in another region resolves to different IPs not in the prefix list. Without a default route (0.0.0.0/0), the packet is dropped.

### 2. Standard Interface Endpoint = Same Region Only

A regular Interface Endpoint for `com.amazonaws.us-east-1.s3` with private-dns-enabled only overrides DNS for `s3.us-east-1.amazonaws.com`. It does NOT override DNS for `s3.us-east-2.amazonaws.com`.

### 3. Cross-Region PrivateLink = The Solution

Creating an Interface Endpoint with `--service-name com.amazonaws.us-east-2.s3 --service-region us-east-2` creates:
- A private ENI in your local subnet
- A private hosted zone that overrides `s3.us-east-2.amazonaws.com` → private IP of the ENI
- PrivateLink routes traffic internally through AWS backbone to S3 in us-east-2

Fully private, no internet, no peering, no Transit Gateway.


## All 7 Options Compared

Cost estimates based on 1,000 GB/month transfer:

| # | Option | Monthly Cost | Private | Data Perimeter | Complexity |
|---|---|---|---|---|---|
| 1 | EIGW (IPv6) | $20 | No | Weak | Low |
| 2 | IGW + Public IP | $24 | No | Weak | Low |
| 3 | VPC Peering + Interface EP | $37 | Yes | Strong | Medium |
| 4 | **Cross-Region PrivateLink** | **$37** | **Yes** | **Strong** | **Low** |
| 5 | S3 Multi-Region Access Points | $41 | Yes | Strong | Medium |
| 6 | Transit Gateway + Interface EP | $130 | Yes | Strong | High |
| 7 | NAT Gateway | $102 | Partial | Weak | Low |

**Recommendation:** Option 4 (Cross-Region PrivateLink) — best balance of security, simplicity, and cost.


## Things NOT to Do

- **DO NOT assume a Gateway Endpoint provides cross-region access.** It only works for S3 in the same region. This is the most common misconception.

- **DO NOT forget `--service-region` when creating the cross-region endpoint.** Without it, the CLI will try to create an endpoint for the local region's S3 and the cross-region access won't work.

- **DO NOT use NAT Gateway for "private" access.** NAT Gateway routes through the internet — it's not private, just NATted. Auditors and compliance teams will flag this.

- **DO NOT skip the TLS enforcement bucket policy.** Even over PrivateLink, enforce `aws:SecureTransport` to guarantee encryption in transit for regulatory compliance.

- **DO NOT forget to create the Interface Endpoint in every subnet that needs access.** PrivateLink endpoints are per-subnet (AZ-specific).

- **DO NOT mix up the two S3 endpoint types.** Gateway = free, same region, route-based. Interface = paid, supports cross-region with `--service-region`, DNS-based.


## Cost Breakdown

| Component | Cost |
|---|---|
| PrivateLink Interface Endpoint | $0.01/hr per AZ (~$7.30/month) |
| Data processing (PrivateLink) | $0.01/GB |
| Cross-region data transfer | $0.02/GB |
| Gateway Endpoint (same-region) | Free |
| SSM Endpoints (3x) | $0.01/hr each (~$22/month) |


## Files

| File | Purpose |
|---|---|
| README.md | This document |
| deploy.py | Creates all resources (VPC, endpoints, buckets, policies) |
| cleanup.py | Destroys all resources to avoid costs |
| architecture.drawio | Visual diagram of the architecture |


## References

[1] Gateway endpoints for Amazon S3
https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-s3.html

[2] Interface endpoints for Amazon S3 (PrivateLink)
https://docs.aws.amazon.com/AmazonS3/latest/userguide/privatelink-interface-endpoints.html

[3] Cross-Region connectivity for AWS PrivateLink
https://aws.amazon.com/blogs/networking-and-content-delivery/introducing-cross-region-connectivity-for-aws-privatelink/

[4] Cost-effective methods for accessing S3 buckets cross-region
https://repost.aws/articles/ARjzluyMS8RbeOOK4MGXRG6Q/cost-effective-methods-for-accessing-s3-buckets-cross-region

[5] S3 Cross-Account Access
https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-walkthroughs-managing-access-example2.html

[6] Enforcing encryption in transit for S3
https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html

[7] S3 Multi-Region Access Points
https://docs.aws.amazon.com/AmazonS3/latest/userguide/MultiRegionAccessPoints.html

[8] AWS PrivateLink pricing
https://aws.amazon.com/privatelink/pricing/
