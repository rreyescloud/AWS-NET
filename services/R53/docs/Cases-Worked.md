# Route 53 — Cases Worked (2024-2026)

## Enterprise Perspective

Provided Route 53 DNS and recovery solutions for clients across multiple industries:

- **Financial Services / Banking** — ARC Region Switch for automated multi-region failover with Aurora Global Database; cross-account health check association for centralized DR orchestration; split-horizon DNS architecture for internal vs external resolution
- **Healthcare** — DNS failover architecture for Mimecast SMTP via Direct Connect; FQDN-based routing for medical vendor services
- **Enterprise / Multi-Account** — 90+ domain migration between accounts with standby support during cutover; Private Hosted Zone conflicts from suspended accounts; VPC association authorization across Organizations
- **Technology / Kubernetes** — ExternalDNS in EKS with health check association; weighted routing between private API Gateway and ALB ingress; latency-based routing optimization
- **Telecommunications** — DNS resolution failures for upstream providers blocking R53 Resolver IPs; TTL capping behavior (300s) by distributed resolver
- **Government** — .gov domain transfer to Route 53
- **E-commerce / Retail** — Domain redirection via S3 + CloudFront + Route 53 alias; CloudFront distribution integration with custom domains
- **Capital Markets** — ARC routing controls for active-active geographic load balancing with automatic region shutdown via CloudWatch alarms

## Summary by Category

| Category | Cases | % |
|---|---|---|
| DNS Resolution / Hosted Zones | ~25 | 50% |
| Resolver (Endpoints, Forwarding, Query Logging) | ~10 | 20% |
| Domain Registration / DNSSEC | ~6 | 12% |
| Health Checks / Failover | ~4 | 8% |
| Integrations (CloudFront, ELB, S3) | ~4 | 8% |
| DNS Firewall | 1 | 2% |
| ARC Region Switch | 1 | 2% |

---

## DNS Resolution / Hosted Zones (~25 cases)

### Private Hosted Zone Conflicts
- 2024-10-30 | ConflictingDomainExists when associating VPC with hosted zone; suspended account had conflicting zone
- 2025-03-24 | VPC not authorized to make hosted zone association
- 2025-03-26 | Creating a PHZ without an associated VPC (not possible)
- 2026-01-20 | Split-horizon DNS architecture considerations with private hosted zones

### Resolution Failures / SERVFAIL
- 2024-12-09 | Domain collectd.sa migrated to R53 but believed inactive; was resolving correctly
- 2024-12-11 | SERVFAIL resolving bestevaerbv.nl; upstream nameservers blocking R53 Resolver public IPs from eu-west-1
- 2024-12-17 | ECS in af-south-1 UnknownHostException while VMs in same VPC resolve fine
- 2025-01-14 | Intermittent SERVFAIL for Network Solutions-hosted domains; 3rd party DNS issue
- 2025-09-01 | Domain resolving to public IPs instead of expected private

### Routing Policies
- 2024-12-24 | Weighted routing between private API Gateway and private ALB in EKS
- 2025-04-23 | Difficulty configuring weighted routing records
- 2026-02-24 | Latency-based routing for super- and subnets

### Propagation / TTL
- 2025-01-28 | Propagation delay of DNS changes up to 22 minutes (vs normal <60s)
- 2024-12-12 | TTL discrepancies; Amazon Provided DNS caps at 300 seconds with distributed caching

### DNS Migration
- 2025-02-27 | DNS migration from Route 53 to Infoblox
- 2025-06-04 | Migrating hosted zone to another AWS account
- 2026-03-02 | DNS zone transfer request

### Miscellaneous
- 2024-11-01 | SPF TXT record configuration for Amazon SES
- 2025-03-31 | Pen test finding: DNS recursive query cache poisoning (explained as non-issue for managed resolver)
- 2025-01-17 | Cannot add records using "@" symbol (InvalidChangeBatch)

---

## Resolver (Endpoints, Forwarding, Query Logging) (~10 cases)

- 2024-12-05 | SERVFAIL from inbound endpoint; no record in hosted zone, recommended query logging
- 2024-12-12 | TTL capping at 300s by distributed resolver caching
- 2024-12-30 | VPC Endpoint for Secrets Manager not resolving; resolver group pointing to IPs in another VPC
- 2025-02-12 | Resolver rules not associating with VPC; throttling
- 2025-02-24 | Resolver query logging costs clarification (R53 free, destination charges apply)
- 2025-04-07 | Cross-region DNS forwarding configuration
- 2025-07-07 | Access blocked from certain Azure regions; geolocation/latency investigation
- 2025-11-18 | Hosted zones not configured with query logging; setup assistance
- 2025-12-08 | Resolver IP for DNS global forwarding
- 2026-03-10 | PHZ and Resolver Rule interaction queries

---

## Domain Registration / DNSSEC (~6 cases)

- 2024-11-06 | Domains suspended for not responding to ICANN verification email
- 2024-11-12 | Standby support during DNS cutover migrating 90+ domain registrations between accounts
- 2024-11-28 | Transfer out blocked by clientTransferProhibited; needed to disable lock + get auth code
- 2024-12-18 | Domain renewal in closed/suspended account
- 2025-03-12 | Domain propagation problems for portatilesymoviles.com
- 2025-04-22 | Transfer .gov domain to AWS Route 53

---

## Health Checks / Failover (~4 cases)

- 2024-12-09 | InvalidChangeBatch creating failover record from EKS; ExternalDNS does not create health checks
- 2026-04-14 | Architectural validation: R53 Failover for Mimecast SMTP via Direct Connect
- 2026-04-15 | Records showing failed in Logix monitoring tool
- 2025-09-08 | New CNAME created but health check testing failing

---

## Integrations (CloudFront, ELB, S3) (~4 cases)

- 2024-11-04 | Domain redirection to S3 bucket via CloudFront + R53 alias
- 2025-04-02 | Hosted zone integration with CloudFront Distribution
- 2025-08-18 | CDN configuration in webhook setup
- 2025-10-06 | Custom DNS for OpenSearch

---

## DNS Firewall (1 case)

- 2024-09-17 | Resolver DNS Firewall rule groups not available in Malaysia region

---

## ARC Region Switch (1 case + lab)

- 2025-09-29 | Queries regarding ARC Region Switch functionality
- 2026 (lab) | Full lab: Region Switch + Aurora Global DB failover (see [[NET-003]])

---

## References

All public documentation links distributed to topic files:
- [[R53]] — Routing policies, hosted zones, SPF/SES
- [[Resolver]] — Query logging, forwarding, TTL behavior
- [[Healthchecks]] — Failover configs, ExternalDNS, EKS integration
- [[Domains]] — Registration, transfers, ICANN verification, DNSSEC
- [[Networking/Services/R53/Integrations]] — CloudFront, S3 redirect, ACM validation
- [[Networking/Services/R53/ARC/ARC]] — Routing controls, safety rules
- [[Region-Switch]] — Plans, IAM, troubleshooting
