# Network Firewall — Managed Stateful/Stateless Inspection

## Enterprise Experience

Implemented AWS Network Firewall solutions for enterprise customers across financial services, healthcare, energy, technology, and telecommunications sectors. Specialized in centralized inspection architectures, TLS inspection, Suricata rule development, and multi-region deployments at scale.

Key highlights:
- Designed **centralized TGW inspection** architectures for 90+ spoke VPCs across 6 regions
- Planned **VPC-type to TGW-type (Network Function) migration** with session continuity strategy
- Resolved **TLS Inspection certificate requirements** — documented ACM public vs imported vs Private CA compatibility matrix
- Identified **firewall endpoint scaling time (10-15 min)** causing packet drops during traffic spikes — pre-warming strategy
- Built **Suricata IDS/IPS rules** for SQL injection, data exfiltration, and ransomware IoC blocking
- Discovered **domain list limitation** — only inspects HTTP/HTTPS, not SMTP or custom TLS ports
- Documented **Stream Exception Policy** behavior — packets not logged in alerts, only in CloudWatch metric

40+ cases across 7+ industries | Top category: Deployment / Architecture (30%)

---

## Documentation

| Topic | Description |
|---|---|
| [NF Overview](docs/NF.md) | Architecture, packet flow, stateless vs stateful, TCP/TLS diagram |
| [Rules](docs/Rules.md) | Stateless, stateful, Suricata syntax, domain lists, evaluation order |
| [Deployment Models](docs/Deployment-Models.md) | Single-AZ, Multi-AZ, TGW centralized, asymmetric routing |
| [TLS Inspection](docs/TLS-Inspection.md) | Inbound/outbound, certificate matrix, cross-account, NF Proxy |
| [Logging](docs/Logging.md) | FLOW/ALERT types, destinations, CloudWatch resource policy, checklist |
| [Troubleshooting](docs/Troubleshooting.md) | Layer 3-7 checklist, TCP idle timeout, stream exceptions |
| [Labs](docs/Labs.md) | Domain list, performance comparison, IDS/IPS, Multi-AZ TGW |
| [Business Cases](docs/Business-Cases.md) | Financial sector: PCI-DSS, segmentation, trading, CI/CD |
| [Cases Worked](docs/Cases-Worked.md) | Enterprise patterns by industry |

## Scenarios

| ID | Title | Industry |
|---|---|---|
| [NET-004](scenarios/NET-004_tls_inspection_cross_signed_cert/) | TLS Inspection — Cross-Signed Certificate Rejection | Capital Markets |

## Industries Served

- **Financial Services / Banking** — PCI-DSS CDE isolation, multi-tenant segmentation (Chinese Wall), trading systems allowlists, CI/CD egress filtering
- **Healthcare** — TLS Inspection for Epic Systems vendor services; HTTP header parsing limitation across TCP boundaries
- **Energy** — VMware Cloud to AWS production traffic inspection
- **Enterprise (Multi-Account)** — Firewall Manager adoption at scale; VPC-type to TGW-type migration with session continuity
- **Technology / SaaS** — SSE streaming compatibility; Suricata optimization for 400+ domain allowlists
- **Telecommunications** — Oracle TNS timeouts through NF; Kerberos/LDAP port requirements (389 vs 636)
- **Capital Markets / Fintech** — TLS Inspection certificate requirements for inbound inspection of trading APIs
