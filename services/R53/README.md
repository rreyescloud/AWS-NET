# Route 53 — DNS & Application Recovery

## Enterprise Experience

Delivered DNS architecture, failover automation, and disaster recovery solutions for enterprise customers across financial services, healthcare, technology, telecommunications, government, and e-commerce sectors. Specialized in multi-region active-passive failover with ARC Region Switch, hybrid DNS resolution, and complex routing policies at scale.

Key highlights:
- Designed **ARC Region Switch + Aurora Global Database** automated failover (full lab with deploy/cleanup IaC)
- Resolved **IAM self-inspection requirement** for Region Switch execution roles — critical undocumented finding
- Architected **centralized DNS for 1,000+ spoke VPCs** using Route 53 Profiles vs Resolver Endpoints (cost/scalability analysis)
- Implemented **Route 53 Global Resolver** for secure anycast DNS across remote offices and mobile devices
- Troubleshot **Private Hosted Zone shadowing** causing production DNS failures (Lambda, ECS, VPC)
- Supported **90+ domain migrations** between AWS accounts with zero-downtime cutover
- Configured **DNSSEC** signing, chain of trust, and resolved dual hosted zone validation failures

50+ cases across 8+ industries | Top category: DNS Resolution / Hosted Zones (50%)

---

## Documentation

- **[R53 Core](docs/R53.md)** — record types, routing policies, hosted zones, alias vs CNAME
- **[Resolver](docs/Resolver.md)** — VPC DNS, inbound/outbound endpoints, forwarding, query logging
- **[Health Checks](docs/Healthchecks.md)** — types, failover configs, cross-account, calculated
- **[Domains](docs/Domains.md)** — registration, transfers, DNSSEC, ccTLDs
- **[Global Resolver](docs/Global%20Resolver.md)** — anycast DNS, DoH/DoT, token auth, troubleshooting
- **[Integrations](docs/Integrations.md)** — ACM, CloudFront, ECS, EKS, SES, Lambda
- **[Troubleshooting](docs/Troubleshooting.md)** — common issues, SERVFAIL, propagation, TTL
- **[ARC Overview](docs/ARC/ARC.md)** — clusters, routing controls, safety rules, health checks
- **[ARC Region Switch](docs/ARC/Region-Switch.md)** — plans, execution blocks, IAM, bidirectional mapping
- **[Cases Worked](docs/Cases-Worked.md)** — enterprise patterns by industry

## Scenarios

- **[NET-003](ApplicationRecoveryController/scenarios/NET-003_arc_region_switch_off_fails/)** — ARC Region Switch + Aurora Global Database failover · Financial Services · *Lab*
- **[NET-002](scenarios/NET-002_r53_query_logging/)** — Resolver query logging, four recurring problem categories · General · *Case analysis*
- **[NET-008](scenarios/NET-008_global_resolver_troubleshooting/)** — Global Resolver failure modes · General · *Case analysis*
- **[NET-009](scenarios/NET-009_eks_zonal_shift_recovery_delay/)** — EKS zonal shift recovery delay after expiry · Financial Services · *Case analysis*

## Industries Served

- **Financial Services / Banking** — ARC Region Switch for automated multi-region failover; cross-account health check orchestration; split-horizon DNS
- **Healthcare** — DNS failover architecture for Mimecast SMTP via Direct Connect
- **Enterprise (Multi-Account)** — 90+ domain migration with standby support; PHZ conflicts from suspended accounts; centralized DNS for 1,000+ VPCs
- **Technology / Kubernetes** — ExternalDNS in EKS with health checks; weighted routing between API Gateway and ALB
- **Telecommunications** — Upstream DNS servers blocking R53 Resolver IPs; TTL capping behavior
- **Government** — .gov domain transfer to Route 53
- **E-commerce** — Domain redirection via S3 + CloudFront + Route 53 alias
- **Capital Markets** — ARC routing controls for active-active geographic load balancing with automatic region shutdown
