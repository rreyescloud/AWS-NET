# Route 53 Resolver

## VPC DNS Resolver (.2 Address)

The Route 53 Resolver is located at:
- `169.254.169.253` (IPv4)
- `fd00:ec2::253` (IPv6)
- Primary private IPv4 CIDR range + 2 (e.g., for VPC CIDR `10.0.0.0/16`, the resolver is at `10.0.0.2`)

## Resolver Endpoints

### Inbound Endpoints
- Allow DNS queries from on-premises or other VPCs to resolve records in Route 53 private hosted zones
- On-premises DNS resolvers forward queries to the inbound endpoint IP addresses
- Each IP address can process up to 10,000 UDP DNS queries per second (QPS)
- If connection tracking is enforced (restrictive SGs or queries through NLB), maximum QPS can drop to 1,500 per IP

### Outbound Endpoints
- Forward DNS queries from your VPC to on-premises or other DNS resolvers
- Require forwarding rules to specify which domains to forward
- Each IP address can process up to 10,000 UDP DNS queries per second (QPS)

**Important:** VPC Flow logs will NOT log traffic between Resolver (VPC CIDR+2 or 169.254.169.253) and Inbound/Outbound ENIs.

### Capacity Recommendations
- If your maximum query rate exceeds 50% of the capacity for any network interface in the endpoint, add more network interfaces to increase capacity
- Use CloudWatch metrics to measure queries per network interface
- Connections through NLB and AWS Lambda are automatically tracked, reducing capacity

## Forwarding Rules

Forwarding rules specify which domains are forwarded from the VPC resolver to an outbound endpoint and then to target IP addresses (typically on-premises DNS servers).

### Rule Types
- **Forward** - Forwards DNS queries for specified domain to the IP addresses in the rule
- **System** - Selectively overrides forwarding rules (Route 53 Resolver resolves the domain)
- **Recursive** - Acts as a recursive resolver for queries that match no other rules

### Cross-Account Rule Sharing
Rules can be shared across accounts using AWS Resource Access Manager (RAM).

References:
- Forwarding outbound DNS queries to your network - https://docs.amazonaws.cn/en_us/Route53/latest/DeveloperGuide/resolver-forwarding-outbound-queries.html
- Managing forwarding rules - https://docs.amazonaws.cn/en_us/Route53/latest/DeveloperGuide/resolver-rules-managing.html#resolver-rules-managing-creating-rules

## Query Logging

### Two Types of Query Logging

#### 1. Route 53 Resolver Query Logging (VPC Query Logging)
- Logs DNS queries made by resources within a VPC
- Includes queries to Route 53 Resolver (VPC+2)
- Can be sent to CloudWatch Logs, S3, or Kinesis Data Firehose
- Reference: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html

#### 2. Route 53 Public DNS Query Logging
- Logs DNS queries received by Route 53 public hosted zones
- Only logs queries to public hosted zones
- Sent to CloudWatch Logs only
- Reference: https://repost.aws/knowledge-center/route53-log-queries

## DHCP Options and Custom DNS

When using custom DNS servers via DHCP options sets, instances in the VPC will use those DNS servers instead of the Route 53 Resolver. To use both:
- Configure your custom DNS servers to forward queries to the Route 53 Resolver for AWS-specific domains
- Use Route 53 outbound endpoints to forward specific domains to your custom DNS

## Route 53 Resolver DNS Firewall

- Filters and regulates outbound DNS traffic from your VPC
- Uses domain lists to define allowed/blocked domains
- Managed Domain Lists: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-managed-domain-lists.html
- Route 53 Resolver DNS Firewall domain lists: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-domain-lists.html

## CLI Reference

- CLI route53resolver: https://docs.aws.amazon.com/cli/latest/reference/route53resolver/
- API Reference: https://docs.aws.amazon.com/Route53/latest/APIReference/API_Operations_Amazon_Route_53_Resolver.html

## Key References

- How can I troubleshoot DNS resolution issues with my Route 53 private hosted zone? - https://repost.aws/knowledge-center/route-53-fix-dns-resolution-private-zone
- Quotas: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/DNSLimitations.html#limits-api-entities-resolver
- How do I troubleshoot DNS failures with Amazon EKS? - https://repost.aws/knowledge-center/eks-dns-failure

## Additional References (from cases)

- [Resolver query logs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html)
- [Log DNS queries](https://repost.aws/knowledge-center/route53-log-queries)
- [Outbound forwarding queries](https://docs.amazonaws.cn/en_us/Route53/latest/DeveloperGuide/resolver-forwarding-outbound-queries.html)
- [Resolver rules managing](https://docs.amazonaws.cn/en_us/Route53/latest/DeveloperGuide/resolver-rules-managing.html)
- [ECS unable to pull secrets (Resolver issue)](https://repost.aws/knowledge-center/ecs-unable-to-pull-secrets)
- [ECS verify connectivity](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/verify-connectivity.html)
- [ECS resource initialization error](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/resource-initialization-error.html)
