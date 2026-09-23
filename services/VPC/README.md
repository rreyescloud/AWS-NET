# VPC — Virtual Private Cloud

## Enterprise Experience

Designed and implemented VPC networking solutions for enterprise customers across multiple industries — financial services, energy, media, automotive, education, logistics, and technology. Delivered private connectivity architectures, cross-region access patterns, and multi-account networking strategies for organizations ranging from startups to Fortune 500 companies.

Key highlights:
- Deployed **cross-region PrivateLink** solutions for private S3 access across accounts and regions
- Resolved **PrivateLink AZ alignment** issues affecting cross-account service connectivity
- Architected **Transit Gateway centralized inspection** patterns for 90+ spoke VPCs
- Identified and documented the **1,000-rule Security Group architectural limit** impacting large-scale filtering
- Optimized **NAT Gateway costs** through traffic analysis and endpoint migration strategies
- Troubleshot **hybrid connectivity** (VPN, Direct Connect) for on-premises database access patterns

95+ cases across 10+ industries | Top category: PrivateLink/Endpoints (22%)

---

## Documentation

| Topic | Description |
|---|---|
| [VPC Core Concepts](docs/VPC.md) | Subnets, route tables, IGW, NAT, NACL vs SG, CIDR planning |
| [Endpoints](docs/Endpoints.md) | Gateway vs Interface, PrivateLink, cross-region, AZ alignment |
| [Connectivity](docs/Connectivity.md) | VPC Peering, VPN, Direct Connect, Transit Gateway |
| [Components](docs/Components.md) | ENI, EIP, IPAM, MTU, BYOIP, NAT Gateway |
| [Troubleshooting](docs/Troubleshooting.md) | Layer 3-7 checklist, Flow Logs, common issues |
| [Integrations](docs/Integrations.md) | ELB, ECS, Lambda, RDS, API Gateway |
| [Commands](docs/Commands.md) | CLI reference for VPC operations |
| [Cases Worked](docs/Cases-Worked.md) | Enterprise patterns by industry |

## Scenarios

| ID | Title | Industry |
|---|---|---|
| [NET-001](scenarios/NET-001_s3_crossaccount/) | S3 Private Cross-Account Cross-Region Connectivity | Fintech |

## Industries Served

- **Financial Services / Banking** — PrivateLink for Data & Analytics SaaS security platforms, Amazon Connect latency optimization, centralized egress billing
- **Energy** — Cloud WAN performance for Exadata databases, cross-AZ bottleneck analysis
- **Media / Broadcasting** — S3 Gateway endpoint for Dynamic Packager fleets, Aurora latency investigation
- **Technology / SaaS** — EKS-to-RDS connectivity, VPC deletion with orphaned dependencies, API throttling resolution
- **Automotive** — Lambda DNS resolution failure from Private Hosted Zone overlap
- **Sports / Data Analytics** — Cross-region PrivateLink for S3-to-Snowflake pipelines
- **Education** — DMS to Oracle through VPC endpoints with failover routing
- **Logistics** — Cross-region latency analysis after regional migration
- **Enterprise (Multi-Account)** — Transit Gateway migration, Security Group limits, centralized inspection patterns
