# Network Firewall — Cases Worked (2025-2026)

## Enterprise Perspective

Provided Network Firewall solutions for clients across multiple industries:

- **Financial Services / Banking** — PCI-DSS compliant CDE isolation, egress filtering for CI/CD build clusters, multi-tenant segmentation (Chinese Wall between business lines), trading systems with low-latency allowlists
- **Healthcare** — TLS Inspection for Epic Systems vendor services; HTTP header parsing limitation identified across TCP boundaries
- **Energy** — VMware Cloud to AWS production traffic inspection; centralized inspection across multiple environments
- **Enterprise / Multi-Account** — Firewall Manager adoption at scale (90+ spoke VPCs, 6 regions); VPC-type to TGW-type migration with session continuity planning
- **Technology / SaaS** — SSE (Server-Sent Events) streaming compatibility; Suricata rule optimization for allowlist patterns; domain list performance at scale (400+ domains)
- **Telecommunications** — Oracle connectivity through NF (TNS timeouts); Kerberos/LDAP port requirements for Active Directory integration
- **Capital Markets / Fintech** — TLS Inspection certificate requirements for inbound inspection of trading APIs; cross-account certificate management patterns

## Summary by Category

| Category | Cases | % |
|---|---|---|
| Deployment / Architecture | ~12 | 30% |
| Rules (Stateful, Suricata, Domain Lists) | ~10 | 25% |
| Connectivity / Traffic Flow | ~9 | 22% |
| TLS Inspection | 1 | 3% |
| Logging / Monitoring | 2 | 5% |
| Performance / Latency | 2 | 5% |
| Quotas / Limits | 1 | 3% |

---

## Deployment / Architecture (~12 cases)

### Centralized Inspection (TGW)
- 2025-06-16 | Intermittent connectivity after adding Inspection VPC; routing loop identified in subnet with both TGW ENI and NFW ENI in same subnet
- 2025-07-21 | Enterprise migration from standalone NF to centralized Firewall Manager; comprehensive import process guide
- 2026-04-16 | Migration from VPC-type to TGW-type (Network Function model) across 6 regions, 90+ spokes; session continuity challenges

### Single VPC Deployments
- 2025-04-22 | Routing verification between CRS router and EC2 with NF in path
- 2025-04-28 | Configuring policies to allow new CIDR 10.104.0.0/14 from on-prem via Direct Connect
- 2025-05-07 | NF to inspect all traffic between VMC environment and production instances
- 2025-05-15 | Implementation guidance for single-AZ with private/public subnets + auto-scaling
- 2025-05-15 | NF with NAT functionality for custom routing
- 2025-07-07 | Unable to remove subnet from NF due to existing mapping and route table references

### NF Proxy (Preview)
- 2026-03-31 | Customer exploring AWS Network Firewall Proxy (preview, us-east-2 only)

### Multi-Endpoint
- 2025-12-18 | Cannot point multiple endpoints of firewall to one firewall resource

---

## Rules — Stateful, Suricata, Domain Lists (~10 cases)

### Domain List Issues
- 2025-01-29 | 400+ domains configuration using pcre vs domain list rule groups; performance comparison
- 2025-10-06 | NF drops SMTP (port 465) to email-smtp despite allow list; **domain list rules only inspect HTTP/HTTPS (ports 80/443)** — critical limitation
- 2026-04-21 | Suricata rule for github.com not matching; needs correct `http.host` and `tls.sni` syntax
- 2026-01-19 | Domain whitelist configuration

### Rule Evaluation Order
- 2025-06-26 | Transitioning from Action Order (blacklist) to Strict Order (allowlist); Stream Exception Policy discussion
- 2025-07-03 | Stream Exception Policy traffic not generating alert logs; only CloudWatch metric `StreamExceptionPolicyPackets` tracks invocations

### Specific Rule Problems
- 2025-04-14 | Cannot allow Workspaces to access Outlook Web Access through custom OWA_NET rules
- 2025-07-07 | Verification if shared rule group is blocking port 443 egress (was NOT blocking)
- 2025-09-19 | RDS/FSx Kerberos integration requires port 389 (LDAP) open despite preference for 636
- 2026-01-19 | Block IoCs for Ransomware "Devman"

---

## Connectivity / Traffic Flow (~9 cases)

### Timeout / Drop Issues
- 2025-02-18 | Oracle ORA-12170 TNS timeout; NF suspected of dropping connection between AWS and on-prem
- 2025-02-25 | Intermittent failures through NF; stream timeout and geneve encapsulation investigation
- 2025-06-20 | **NF caused timeouts when routing production traffic through it; dropped packets due to firewall endpoint scaling time (10-15 min); pre-warming recommended**
- 2025-07-02 | Frequent DNS resolution changes for latency-based records causing FQDN rule failures
- 2025-09-17 | SSE (Server-Sent Events) streaming works in dev but not production; NF interference suspected

### Application Connectivity
- 2025-07-08 | Unable to connect to Oracle OIC application through NF
- 2025-07-30 | Palo Alto on EC2 unreachable via Direct Connect; active/active firewalls causing asymmetric issues
- 2025-09-24 | **Critical:** Multiple applications affected in China region; external firewall blocking inbound

### Diagnostics
- 2026-01-27 | Packet capture support; flow logs and port mirroring for NF traffic analysis

---

## TLS Inspection (1 case + lab)

- 2025-03-18 | **NF blocking requests to vendorservices.epic.com when TLS Inspection enabled; HTTP host header not parsed due to packet segmentation across TCP boundaries** (Suricata limitation with fragmented HTTP headers after TLS decryption)
- 2026 (lab NET-004) | Cross-signed certificate rejection for imported certs; ACM public (AMAZON_ISSUED) works as exception

---

## Logging / Monitoring (2 cases)

- 2025-05-16 | **Logs not delivered because CloudWatch log group not included in resource policy** (delivery.logs.amazonaws.com must be allowed)
- 2025-09-03 | Performance issues causing packet drops; requested firewall logs and ARN for investigation

---

## Performance / Latency (2 cases)

- 2025-06-12 | Packet loss between VPCs via TGW; TCP retransmissions causing latency spikes
- 2025-06-20 | **Firewall endpoint scaling takes 10-15 minutes; dropped packets during scaling — pre-warming recommended for traffic spikes**

---

## Quotas / Limits (1 case)

- 2025-02-20 | Stateful rule limit increase from 30,000 to 50,000 via Service Quotas console

---

## Key Lessons from Cases

1. **Domain list rules only inspect HTTP/HTTPS** — they will NOT match SMTP (465), custom TLS ports, or non-HTTP protocols. Use Suricata `tls.sni` rules for non-HTTP TLS traffic.

2. **Firewall endpoint scaling takes 10-15 minutes** — sudden traffic spikes will cause packet drops. Pre-warm by gradually increasing traffic before cutover.

3. **CloudWatch resource policy is mandatory for logging** — `delivery.logs.amazonaws.com` must be explicitly allowed in the log group resource policy.

4. **Stream Exception Policy packets are invisible in alert logs** — only tracked via the `StreamExceptionPolicyPackets` CloudWatch metric.

5. **Routing loop risk with TGW** — never put TGW ENI and NF ENI in the same subnet; traffic will loop.

6. **TLS Inspection + packet segmentation** — Suricata may not parse HTTP headers fragmented across TCP boundaries after TLS decryption. Known limitation.

7. **AZ alignment matters for Firewall Manager** — when importing existing NF resources, endpoint mappings must be consistent.

---

## References

All public links distributed to topic files:
- [[Networking/Services/NetworkFirewall/Rules]] — Suricata syntax, domain filtering, strict vs action order
- [[Deployment-Models]] — TGW patterns, Firewall Manager, import/export
- [[TLS-Inspection]] — Certificate requirements, post-quantum algorithms
- [[Networking/Services/NetworkFirewall/Logging]] — CloudWatch policy, Athena queries, alert vs flow logs
- [[Networking/Services/NetworkFirewall/Troubleshooting]] — Stream exception, asymmetric routing, scaling
