# VPC — Cases Worked (2024-2026)

## Enterprise Perspective

Provided VPC networking solutions for clients across multiple industries:

- **Financial Services / Banking** — PrivateLink cross-account connectivity for Databricks and Protegrity ESA data security platforms; Amazon Connect CCP latency optimization through proxy architectures; centralized egress billing for multi-account Landing Zones
- **Energy** — Cloud WAN performance optimization for Exadata databases; PDB clone operations bottleneck analysis across AZs
- **Media / Broadcasting** — S3 Gateway endpoint connectivity for Dynamic Packager fleets; Aurora RDS latency investigation for live broadcast ad-revenue systems
- **Technology / SaaS** — EKS-to-RDS intermittent connectivity; VPC deletion with orphaned EKS dependencies; Datadog DescribeVpcEndpointServices throttling; IBM PureScale/Valkey endpoint quota limits
- **Automotive** — Lambda in VPC DNS resolution failure due to Private Hosted Zone overlap with public domains
- **Sports / Data Analytics** — Cross-region PrivateLink for S3-to-Snowflake data pipelines
- **Education** — DMS to Oracle through VPC endpoints with failover routing
- **Logistics** — Cross-region latency analysis after regional migration (ME to EU)
- **Telecommunications** — Citrix VDI packet loss to POPs; Charter/Spectrum DNS resolution failures
- **Enterprise (Multi-Account)** — Transit Gateway static route failover limitations; NF VPC-type to TGW-type migration across 90+ spoke VPCs; Security Group 1,000-rule architectural limit impacting large-scale filtering

## Summary by Category

| Category | Cases | Key Patterns |
|---|---|---|
| PrivateLink / VPC Endpoints | 9 | Cross-region, cross-account, AZ alignment, quota, DNS |
| VPC Connectivity / Routing | 5 | Peering, TGW, internet access, latency |
| Transit Gateway | 3 | Static route failover, NF migration, egress billing |
| NAT Gateway | 1 | Cost optimization methodology |
| Security Groups / NACLs | 1 | 1,000-rule architectural limit |
| DNS in VPC context | 3 | PHZ overlap, VPC endpoint DNS, DNS hostnames |
| VPC CIDR / Subnets | 2 | Terraform eventual consistency, secondary CIDR |
| Network Performance | 2 | Latency (Cloud WAN, cross-region) |
| BYOIP / EIP / Port 25 | 3 | Quota increases, BYOIP signing, SMTP restrictions |

---

## PrivateLink / VPC Endpoints

### Cross-Zone Load Balancing Required for PrivateLink
- **Date:** 2026-05-12
- **Industry:** Enterprise (SOC systems, Informatica workloads)
- **Problem:** VPC Endpoint connectivity failure between Consumer and Provider accounts. Connection only established after enabling Cross-Zone Load Balancing on the NLB — PrivateLink AZ affinity was routing traffic to an NLB node with no local target.
- **Key Lesson:** When provider NLB targets are not in all AZs, enable cross-zone LB or ensure consumer endpoint is in same AZ ID as provider targets.

### AZ ID Shuffling Between Accounts
- **Date:** 2026-04-08
- **Industry:** Enterprise
- **Problem:** VPC Endpoint creation failure due to AZ ID shuffling between provider and consumer accounts. AWS maps AZ names to different physical zones per account.
- **Solution:** Expand provider's endpoint service to all AZs, or create subnets in consumer VPC matching provider's AZ IDs.

### Cross-Region S3 Access via PrivateLink
- **Date:** 2026-02-27 / 2026-04-07
- **Industry:** Sports/Data (data pipeline RDS → S3 → Snowflake)
- **Problem:** Cross-region and cross-account PrivateLink for S3 access. Error "S3 bucket does not exist." Configuration gaps in VPC endpoints, DNS resolution, and third-party network rules.
- **Key Lesson:** Use `--service-region` flag. Requires `vpce:AllowMultiRegion` in IAM/SCP.

### Cross-Region PrivateLink Between Partitions Not Supported
- **Date:** 2026-03-06
- **Problem:** Customer asked if PrivateLink works between Hong Kong and Ningxia regions. Answer: No — cross-region PrivateLink only works within the same partition (Commercial ↔ Commercial, not Commercial ↔ China).

### Cross-Account PrivateLink Inconsistency (Databricks)
- **Date:** 2026-02-20
- **Industry:** Financial/Data (Databricks, Protegrity ESA)
- **Problem:** Inconsistent PrivateLink connectivity — Account B works, Account C fails with "policy shared memory is empty." Root causes: AZ ID mismatch, DNS resolution differences, security group configs.

### VPC Endpoint Quota Limit (ElastiCache)
- **Date:** 2026-02-03
- **Industry:** Technology (IBM PureScale/Valkey)
- **Problem:** Failed to create ElastiCache Valkey due to VPC endpoint quota (50 per VPC). Guided to self-service quota increase.

### Private API Gateway 403 Through VPC Endpoint
- **Date:** 2026-01-23
- **Problem:** 403 Forbidden accessing Private API Gateway from F5 through VPC Endpoint. Root cause: private DNS enabled on endpoint causes ALL API Gateway requests in VPC to route through the interface endpoint, blocking access to public APIs.

### WAF Association Blocked by VPC Endpoint Policy
- **Date:** 2026-01-05
- **Problem:** Cannot associate WAFv2 WebACL to ALB. Undocumented action `elasticloadbalancing:CreateWebACLAssociation` must be allowed in VPC endpoint policy.

### Endpoint Service Stuck in Pending State
- **Date:** 2026-03-03
- **Problem:** VPC PrivateLink Endpoint Services stuck in "Pending" despite verified private DNS names and auto-acceptance. Escalated to internal PrivateLink team.

---

## VPC Connectivity / Routing

### VPC Peering One-Directional Failure
- **Date:** 2026-03-02
- **Problem:** Can connect from VPC-A to RDS in VPC-B but reverse fails. Root cause: DNS resolution disabled on accepter peering options, plus route table and SG misconfigurations.

### VPC Internet Access Failure
- **Date:** 2026-03-10
- **Problem:** Unable to access internet from VPC. Standard connectivity troubleshooting (route tables, IGW, NAT, SG, NACL).

### DNS Resolution Failure in VPC (Lambda + Private Hosted Zone)
- **Date:** 2026-02-03
- **Industry:** Automotive
- **Problem:** Lambda in VPC cannot resolve external domain. Root cause: Private Hosted Zone for same domain associated to VPC but missing the subdomain record. VPC resolver returns "No answer" without consulting public DNS.
- **Key Lesson:** If a PHZ is associated to a VPC, it takes precedence. All subdomains that need public resolution must be explicitly defined in the PHZ.

### Intermittent EKS to RDS Connectivity
- **Date:** 2026-03-02
- **Industry:** Technology (EKS/Kubernetes)
- **Problem:** Sudden intermittent failures between EKS pods and RDS. No packet loss at ENI level. Under investigation with Resolver health metrics and VPC flow logs.

### Cross-Region Latency After Migration
- **Date:** 2026-03-06
- **Industry:** Logistics (PDI Technologies, ADNOC)
- **Problem:** Increased latency for UAE users after migrating from me-central-1 to eu-central-1. Issue is public internet path, not AWS infrastructure. Requested MTR reports.

---

## Transit Gateway

### TGW Static Routes Don't Failover
- **Date:** 2026-04-08
- **Problem:** TGW routes traffic to blackhole when primary VPN goes down instead of failing over to backup. Static routes are NOT health-aware. Solution: use BGP for automatic failover, or CloudWatch + Lambda automation.

### Network Firewall VPC-Type to TGW-Type Migration
- **Date:** 2026-04-16
- **Industry:** Enterprise (6 regions, 90+ spoke VPCs)
- **Problem:** Planning migration from Inspection VPC model to TGW Network Function model. No mechanism to preserve TCP session state during migration. Recommended staged rollout with temporary Suricata reject rule for graceful session reset.

### Centralized Egress Billing Responsibility
- **Date:** 2026-02-26
- **Problem:** Which account pays DTO fees? EC2 in Account A → TGW in Account B → NAT GW + IGW in Account C. Answer: Account C (IGW owner) is billed for Data Transfer Out.

---

## NAT Gateway

### NAT Gateway Cost Optimization
- **Date:** 2026-03-10
- **Problem:** High NAT Gateway costs, unable to identify traffic source. Methodology: Cost Explorer analysis, VPC Flow Logs on NAT GW ENI, Athena queries. Optimization: S3 Gateway endpoint, Interface endpoints, per-AZ NAT Gateways.

---

## Security Groups / NACLs

### 1,000 Rules Architectural Limit
- **Date:** 2026-02-20
- **Problem:** Customer needs to filter 10,000 IPs but constrained by 1,000 rules/interface limit. Confirmed: limit is architectural (VPC data plane cannot compile beyond 1,000). NLB is exception (16,000). Cannot be increased for standard EC2.

---

## VPC CIDR / Subnets

### Terraform Eventual Consistency with CIDR Disassociation
- **Date:** 2026-01-05
- **Problem:** `InvalidCidrBlock.InUse` error when disassociating secondary CIDR immediately after deleting subnets in same Terraform apply. Expected behavior due to AWS eventual consistency. No built-in waiter. Solution: delays, retry with exponential backoff, split operations.

### VPC Deletion Failing (EKS Dependencies)
- **Date:** 2026-03-09
- **Industry:** Technology (EKS/Kubernetes)
- **Problem:** VPC deletion failing due to DependencyViolation. Remaining dependencies from EKS cluster removed without cleanup. Deletion order: VPC Endpoints → Security Groups → Network ACL → Route Table → VPC.

---

## Network Performance

### Cloud WAN Routing Bottleneck (Exadata)
- **Date:** 2026-02-18
- **Industry:** Energy
- **Problem:** Severe network performance — PDB clone taking 4 days for 3.5TB vs 4 hours in non-prod. Potential bottleneck related to Cloud WAN routing in production AZ.

### Aurora RDS Latency Spikes
- **Date:** 2026-05-12
- **Industry:** Media/Advertising (ad revenue, live broadcasts)
- **Problem:** Intermittent latency spikes on Aurora queries. Query execution normal (~0.0001s) but total round-trip spiking to 0.28s+. Network-level delay, not database.

---

## BYOIP / EIP / Miscellaneous

### BYOIP RequestExpired Error
- **Date:** 2026-01-28
- **Problem:** CidrAuthZ signing fails with RequestExpired. Timestamp outside 15-minute window. Common causes: wrong private key, cert format issues in RDAP, expired cert, key/cert mismatch.

### VPC DNS Hostnames Setting
- **Date:** 2026-03-17
- **Problem:** Customer asking about impact of enabling "Enable DNS Hostnames." Confirmed: non-breaking change that adds public DNS hostname functionality.

### PNI Down (Apple)
- **Date:** 2026-01-24
- **Industry:** Technology (Apple)
- **Problem:** Private Network Interconnect down between AS714 (Apple) and AS16509 (Amazon).

---

## References

All public documentation links have been distributed to the corresponding topic files:
- [[Endpoints]] — PrivateLink, cross-region, AZ alignment, S3 access
- [[Connectivity]] — TGW, Peering, VPN, pricing
- [[Components]] — NAT Gateway costs, Security Groups limits, CIDR, BYOIP
- [[Networking/Services/VPC/Integrations]] — Lambda DNS, DMS, API Gateway
- [[Networking/Services/VPC/Troubleshooting]] — Flow Logs, Reachability Analyzer, quotas

---

## 2024 Cases (Sep - Dec)

### PrivateLink / Endpoints (6)
- 2024-09-24 | EC2 Instance Connect Endpoint only supports ports 22/3389, cannot access RDS
- 2024-09-30 | No limit on VPC endpoint services per VPC; each NLB can only be associated with one VPCE service
- 2024-12-03 | Endpoint service not found cross-account; verification needed
- 2024-12-04 | Error modifying VPC endpoint service supported regions; only specific regions for cross-region PrivateLink
- 2024-12-24 | CloudFormation failing to create cross-region VPC endpoint (not supported at that time)
- 2024-12-30 | VPC Endpoint for Secrets Manager not resolving; cross-VPC resolver group misconfiguration

### VPC Peering (3)
- 2024-09-26 | High latency Singapore-Mumbai; VPC peering explored to keep traffic on AWS backbone
- 2024-11-13 | Intermittent packet loss from EKS over peering connection
- 2024-12-05 | Transitive peering not possible; TGW recommended for extending on-prem across two VPCs

### DNS / Route 53 in VPC (6)
- 2024-09-17 | Route 53 Resolver DNS Firewall not available in Malaysia region
- 2024-10-30 | ConflictingDomainExists error from suspended account's hosted zone
- 2024-12-05 | SERVFAIL from R53 inbound endpoint; missing records in hosted zone
- 2024-12-11 | Domain not resolving because upstream nameservers blocked R53 Resolver public IPs
- 2024-12-12 | TTL discrepancies; Amazon Provided DNS caps TTL at 300s with distributed caching
- 2024-12-17 | ECS in af-south-1 UnknownHostException while VMs in same VPC resolve fine

### Connectivity (general) (8)
- 2024-09-10 | ENI stopped working; ENA driver resets on Palo Alto AMI (vendor issue)
- 2024-09-20 | NLB cannot route traffic not destined for it; GWLB recommended
- 2024-10-09 | Outbound traffic blocked despite correct SGs/NACLs; resolved after reboot
- 2024-10-10 | Cannot delete ENIs from App Runner; orphaned Fargate networking sessions
- 2024-11-14 | Intermittent packet drops Citrix VDI to POPs; MTR requested
- 2024-12-02 | Production architecture consultation (TGW/DX/NACL/VPN)
- 2024-12-06 | NFS mount disconnects over TGW + DXGW + VPC endpoints to on-prem
- 2024-12-16 | Intermittent connectivity app server to RDS

### Network Performance / Latency (4)
- 2024-10-08 | Slow download through ELB/proxy; application-side latency
- 2024-10-11 | 130ms latency increase in Singapore; Lumen BGP peering decommission, rerouted via Cogent
- 2024-11-11 | DMS CDC replication 10+ hours via VPN/TGW; bandwidth/TCP window suspected
- 2024-12-19 | Order Management delayed responses through PrivateLink/NLB
- 2024-12-23 | **Industry: Banking** — Connect CCP delay through proxy to eu-west-2

### NAT Gateway (2)
- 2024-10-29 | Lambda behind NAT GW intermittent timeouts; IdleTimeoutCount spikes
- 2024-12-23 | Identify traffic source through NAT GW to specific IP on port 443

### Security Groups / NACLs (3)
- 2024-11-14 | Prefix List IDs not supported in API Gateway resource policies
- 2024-11-27 | Quota increase to 200 rules across 59 accounts
- 2024-11-29 | Missing Bitbucket IP ranges in SG causing build failures

### VPN / Direct Connect (2)
- 2024-11-08 | VPN disruption; no AWS-side events found
- 2024-12-03 | DC/VPC connectivity for MediaConnect Zixi stream; missing SG egress rule

### Flow Logs (2)
- 2024-09-18 | "Unknown error" in console during Large Scale Event; logs still publishing
- 2024-10-04 | Flow log analysis for Aurora DB restart; no anomalies

### EIP / Public IP (2)
- 2024-09-19 | Requested dedicated /27 contiguous block as EIPs using IPAM
- 2024-10-08 | Needs pool of Elastic IPs (minimal)

### Quotas / Limits (2)
- 2024-09-12 | NAT Gateways per AZ increase from 5 to 20
- 2024-10-07 | VPC limit increase from 5 to 6

### Transit Gateway (1)
- 2024-12-02 | Architecture consultation (TGW/DX/VPN); transferred to NetDev

### VPC CIDR / Subnets (1)
- 2024-12-17 | ENI/EIP guidance and subnet sizing

---

## 2025 Cases (Jan)

### PrivateLink / Endpoints (5)
- 2025-01-10 | NLB flow count spike and TCP resets through VPCE between ECS services
- 2025-01-15 | Intermittent 503 from S3 via Nginx proxy + Gateway endpoint; S3-side LB issue
- 2025-01-28 | "Connection was closed" errors accessing Bedrock API through VPCE in ap-south-1
- 2024-12-30 | DocumentDB cross-account via VPCE + NLB (transferred)
- 2024-12-24 | Cross-region VPC endpoint not supported in CloudFormation

### DNS / Route 53 in VPC (4)
- 2025-01-17 | Cannot add DNS records using "@" symbol (InvalidChangeBatch)
- 2025-01-28 | DNS resolution failures for charter.net/spectrum.net endpoints
- 2025-01-30 | Terraform VPC association authorization failing (ExpiredToken)
- 2024-12-17 | ECS af-south-1 UnknownHostException

### NAT Gateway (2)
- 2025-01-06 | High costs from CloudFront endpoint downloads; custom flow log fields guided
- 2025-01-13 | MTU enforcement of 8500 bytes upcoming; proactive testing guidance

### Transit Gateway (1)
- 2025-01-06 | Traffic drop through TGW + GWLB + Checkpoint firewall; DNS resolution troubleshooting

### VPN / Direct Connect (1)
- 2025-01-06 | Unable to establish S2S VPN tunnel; IKE/IPSec config review

### Connectivity (general) (3)
- 2025-01-07 | EC2 S3 connectivity timeout via Gateway endpoint (BBC Dynamic Packager)
- 2025-01-09 | Inquiry about AWS maintenance/incidents in last 48 hours (none)
- 2025-01-28 | Network disruption Jan 19 affecting EKS in ap-southeast-2b

### Security Groups / NACLs (1)
- 2025-01-15 | Disable Multi-VPC ENI Attachment feature on specific account

### Quotas / Limits (1)
- 2025-01-13 | DescribeVpcEndpointServices API 503 errors from throttling (Datadog)

### VPC CIDR / Subnets (2)
- 2024-12-24 | Minimum IPv6 CIDR block size for BYOIP in IPAM
- 2025-01-13 | Cannot mark non-default VPC as default

### Flow Logs (1)
- 2025-01-29 | WAF logs cost estimation for CloudWatch

### Network Performance / Latency (1)
- 2024-12-11 | Intermittent packet loss requiring PCAP analysis

---

## Overall Statistics (Sep 2024 - May 2026)

| Category | Total Cases | % |
|---|---|---|
| PrivateLink / VPC Endpoints | ~20 | 22% |
| Connectivity (general) | ~14 | 15% |
| DNS / Route 53 in VPC | ~13 | 14% |
| Network Performance / Latency | ~8 | 9% |
| NAT Gateway | ~5 | 5% |
| Transit Gateway | ~5 | 5% |
| Security Groups / NACLs | ~5 | 5% |
| VPN / Direct Connect | ~5 | 5% |
| VPC Peering | ~5 | 5% |
| Flow Logs | ~5 | 5% |
| Quotas / Limits | ~5 | 5% |
| VPC CIDR / Subnets | ~5 | 5% |
| EIP / Public IP | ~3 | 3% |

### Industries Identified
- Banking/Financial Services (Barclays, trading systems)
- Media/Broadcasting (BBC, MediaConnect)
- Technology (EKS/Kubernetes, Datadog, App Runner)
- Energy (Exadata/Cloud WAN)
- Automotive
- Telecommunications (Citrix VDI, Charter/Spectrum)
- Logistics
- Sports/Data
- Education

### Top Patterns
1. **PrivateLink dominates** — cross-account, cross-region, AZ alignment, DNS resolution
2. **DNS in VPC** — PHZ conflicts, resolver behavior, TTL capping, service-specific resolution
3. **Connectivity troubleshooting** — requires systematic Layer 3-7 approach + Flow Logs
4. **Latency** — often NOT AWS-side; internet path, proxy, or application-layer issues
