# Route 53 Global Resolver

## What is Global Resolver

Internet-reachable DNS resolver that provides secure DNS resolution for authorized clients globally — remote offices, branch locations, on-premises, and mobile devices. Provides anycast public IPs for DNS queries.

## Key Characteristics

- **Global, internet-accessible** — not tied to a VPC
- **Anycast IPs** — static public IPs assigned on creation
- **Resolves:** Private Hosted Zones + public internet domains
- **Does NOT:** Forward queries to external resolvers (no outbound forwarding)
- **Protocols:** Do53, DoH (DNS over HTTPS), DoT (DNS over TLS)
- **Control plane region:** us-east-2 (all API calls go here)
- **IPv6:** NOT supported yet (despite blog mention)

## Architecture

```
On-prem / Mobile / Branch Office
        │
        ▼ (DoH/DoT encrypted, or Do53)
Internet
        │
        ▼
Route 53 Global Resolver (anycast IPs)
        │
        ├── Resolves Private Hosted Zones (associated via DNS Views)
        └── Resolves public internet domains
```

## Components

```
Global Resolver
  └── DNS Views (segment clients)
       ├── Access Sources (who can query — public IPs or tokens)
       ├── Hosted Zone Associations (which PHZs to resolve)
       └── Firewall Rules (DNS filtering)
```

## Authentication Methods

| Method | Use Case | Requirement |
|---|---|---|
| Access Source (IP-based) | Fixed offices with static public IPs | Public IPs only (/17 to /32) |
| Token-based | Mobile/remote users with dynamic IPs | DoH or DoT only (not Do53) |

- Private IPs (VPC CIDRs) are NOT allowed as Access Sources
- 0.0.0.0/0 is NOT allowed — use token-based auth for "any source"
- Access Source quota: 60K individual IPs (soft limit)

## Common Issues & Troubleshooting

### Corporate Firewall Intercepting DNS (Most Common)
- **Symptom:** dig works for google.com but PHZ domain times out; no query logs in CloudWatch
- **Cause:** Corporate firewall/VPN intercepts Do53 (port 53) traffic and redirects to internal resolver
- **Resolution:** Use DoH (port 443) or DoT (port 853) to bypass firewall interception
- **Best practice:** Always test from non-VPN connection first

### PHZ Shadows Public Hosted Zone → NXDOMAIN
- **Symptom:** Public domain returns NXDOMAIN through GR
- **Cause:** If PHZ with same parent domain exists, it takes precedence (standard R53 behavior)
- **Resolution:** Add record to PHZ, or use different parent domain for GR

### Cross-Account PHZ Association NOT Supported
- **Symptom:** GR-ERR03603 / GR-ERR03102
- **Cause:** PHZ and GR must be in same account (by design)
- **Workaround:** Duplicate PHZ in GR account, or sync with script
- **Note:** Profiles + RAM does NOT work as bridge (tested and confirmed)

### DoH Fails with TLS Error Using Raw IP
- **Symptom:** `tlsv1 internal alert error`
- **Cause:** DoH/DoT require DNS name (SNI), not raw IP address
- **Resolution:** Use the `dnsName` from the Global Resolver config
```bash
kdig @<dns-name> +https="/dns-query?token=<token>" <domain> +tls-hostname=<dns-name>
```

### GR Does NOT Forward to External Resolvers
- **Symptom:** corp.local queries fail
- **Cause:** GR resolves PHZ + public DNS only; no forwarding capability
- **Resolution:** Keep VPC Resolver outbound endpoints for hybrid DNS (corp.local, on-prem domains)

### Query Logs Not Appearing in S3
- **Symptom:** Empty S3 bucket
- **Cause:** Incorrect bucket policy — use `"Service": "delivery.logs.amazonaws.com"` (not `"PrincipalGroup"`)
- **Resolution:** Let console auto-create policy; remove conflicting Deny statements

### Console Loading Indefinitely for Log Config
- **Symptom:** "Log delivery" option spins forever
- **Cause:** Browser-specific (Chrome issue)
- **Resolution:** Use Safari or different browser

### Unexpected Billing
- GR charges per-resolver per-region per-hour
- 2 regions = 2x hourly charges
- Delete GR when not in use

## Error Code Reference

| Error | Cause | Resolution |
|---|---|---|
| GR-ERR03603 | PHZ not found in caller's account | Move PHZ to GR account |
| GR-ERR03102 | Cannot target dns-view in another account | Same as above |
| "non-routable IPs" | Private CIDR in Access Source | Use public IPs or tokens |
| NXDOMAIN (unexpected) | PHZ shadows public zone | Add record to PHZ |
| TLS handshake error | Using raw IP for DoH/DoT | Use DNS name with SNI |

## S3 Bucket Policy for Logging

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AWSLogDeliveryWrite1",
    "Effect": "Allow",
    "Principal": {
      "Service": "delivery.logs.amazonaws.com"
    },
    "Action": "s3:PutObject",
    "Resource": "arn:aws:s3:::<bucket>/AWSLogs/<account-id>/Route53GlobalResolverLogs/*",
    "Condition": {
      "StringEquals": {
        "s3:x-amz-acl": "bucket-owner-full-control",
        "aws:SourceAccount": "<account-id>"
      }
    }
  }]
}
```

S3 path: `<bucket>/AWSLogs/<account-id>/Route53GlobalResolverLogs/<resolver-id>/dnsv-<random>/*`

## CLI Commands

```bash
# List Global Resolvers
aws route53globalresolver list-global-resolvers --region us-east-2

# List DNS Views
aws route53globalresolver list-dns-views --global-resolver-id <gr-id> --region us-east-2

# List PHZ Associations
aws route53globalresolver list-hosted-zone-associations --resource-arn <dns-view-arn> --region us-east-2

# List Access Sources
aws route53globalresolver list-access-sources --region us-east-2 --filters dnsViewId=<dnsv-id>

# List Firewall Rules
aws route53globalresolver list-firewall-rules --dns-view-id <dnsv-id> --region us-east-2

# Test resolution
dig @<anycast-ip> <domain>
curl ifconfig.me  # verify your public IP is in Access Source
```

## Cross-Account PHZ — Tested Workaround (Does NOT Work)

Attempted via Profile + RAM sharing:
1. Create R53 Profile in Account B (PHZ owner)
2. Associate PHZ to Profile
3. Share Profile via RAM to Account A (GR owner)
4. Accept RAM share in Account A
5. Attempt associate-hosted-zone → **Fails with GR-ERR03603**

Confirmed by service team: cross-account PHZ association is a current limitation, being worked on.

## Best Practices

- Keep PHZ and GR in the same account
- Use tokens for mobile/remote (dynamic IPs), Access Sources for fixed offices
- Prefer DoH/DoT over Do53 (encrypted, bypasses firewalls)
- Enable DNSSEC validation on DNS Views
- DNS Firewall rules: lower priority = higher precedence
- Monitor CloudWatch logs for denied/failed queries
- Test from non-VPN first when troubleshooting

## References

- [What is Global Resolver](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/gr-what-is-global-resolver.html)
- [Getting started with Global Resolver](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/gr-getting-started.html)
- [Global Resolver limits](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/gr-load-balancer-limits.html)
- [Introducing Global Resolver (blog)](https://aws.amazon.com/blogs/aws/introducing-amazon-route-53-global-resolver-for-secure-anycast-dns-resolution-preview/)
- [Unexpected billing discussion](https://www.repost.aws/questions/QUKuI850kgQS-DfoweiZGXjw/huge-unexpected-bill-from-route-53-global-resolver-free-trial-6-069-in-one-month)
