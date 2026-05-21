# AWS Network Firewall - Business Cases (Financial Sector)

## 1. Context: Financial Sector Requirements

### Regulatory and Compliance Frameworks

Financial institutions operate under strict regulatory frameworks:

- **PCI-DSS** (Payment Card Industry Data Security Standard)
- **SOX** (Sarbanes-Oxley Act)
- **GLBA** (Gramm-Leach-Bliley Act)
- **FFIEC** (Federal Financial Institutions Examination Council)
- **GDPR/CCPA** for data protection
- **FINRA** (Financial Industry Regulatory Authority)
- **FCA** (Financial Conduct Authority - UK)

### Implications for Network Firewall

- Mandatory network segmentation
- Logging and auditing of all traffic
- Deep Packet Inspection (DPI)
- Data exfiltration prevention
- Log retention for 7+ years
- Demonstrable compliance controls

---

## 2. Case 1: Credit Card Data Protection (PCI-DSS Compliance)

### Business Problem

A client in the financial services industry processes millions of credit card transactions daily. PCI-DSS requires strict segmentation of the Cardholder Data Environment (CDE).

### Solution with Network Firewall

```
Internet --> Network Firewall --> CDE VPC (Isolated)
                |
         Stateful Rules:
         - Allow only HTTPS (443) to payment gateway
         - Block all outbound except to payment processors
         - IDS/IPS to detect data exfiltration
```

### Architecture

- **Stateless rules**: Block all non-HTTPS traffic toward CDE
- **Domain List**: Allowlist of payment processors (Visa, Mastercard, Amex endpoints)
- **Suricata rules**: Detect exfiltration patterns (POST requests with card data patterns)

### Compliance Mapping

| PCI-DSS Requirement | Network Firewall Capability |
|---------------------|---------------------------|
| Req 1: Install and maintain a firewall | NF provides managed firewall at CDE boundary |
| Req 1.2: Restrict connections | Stateless + stateful rules enforce least-privilege |
| Req 1.3: Prohibit direct public access to CDE | Domain allowlist, deny-by-default policy |
| Req 5: Anti-malware | IDS/IPS Suricata rules for threat detection |
| Req 10: Track and monitor all access | FLOW + ALERT logging (S3 for 7yr retention) |
| Req 11.4: IDS/IPS | Suricata-based detection and prevention rules |

### Key Rules

```suricata
# Allow only payment processors
pass tls $CDE_NET any -> $PAYMENT_PROCESSORS any (tls.sni; content:"api.visa.com"; endswith; sid:100001; rev:1;)
pass tls $CDE_NET any -> $PAYMENT_PROCESSORS any (tls.sni; content:"api.mastercard.com"; endswith; sid:100002; rev:1;)

# Detect potential card data exfiltration (16-digit patterns in POST)
alert http $CDE_NET any -> $EXTERNAL_NET any (msg:"Possible Card Data Exfiltration"; flow:to_server; content:"POST"; http_method; pcre:"/\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/"; sid:100003; rev:1;)

# Block all other outbound from CDE
drop ip $CDE_NET any -> $EXTERNAL_NET any (msg:"Unauthorized CDE Egress"; sid:100099; rev:1;)
```

Reference: [PCI Security Standards](https://www.pcisecuritystandards.org/)

---

## 3. Case 2: Multi-Tenant Segmentation for Business Lines (Chinese Wall)

### Business Problem

A client in the financial/banking sector has multiple business lines:

- Consumer Banking
- Investment Banking
- Asset Management
- Commercial Banking
- Treasury Services

Each requires strict isolation (Chinese Wall / information barrier) to avoid conflicts of interest and regulatory violations.

### Architecture with Network Firewall + Transit Gateway

```
+-------------------+  +-------------------+  +-------------------+
| Consumer Banking  |  | Investment Banking|  | Asset Management  |
|      VPC          |  |       VPC         |  |      VPC          |
+--------+----------+  +--------+----------+  +--------+----------+
         |                       |                       |
         +-----------+-----------+-----------+-----------+
                     |
             +-------v--------+
             | Transit Gateway|
             | (Segmented RT) |
             +-------+--------+
                     |
         +-----------v-----------+
         |    Inspection VPC     |
         |   Network Firewall    |
         |                       |
         |  Rules:               |
         |  - Deny cross-LoB     |
         |  - Allow shared svcs  |
         |  - Log all cross-VPC  |
         |  - Alert on violations|
         +-----------------------+
```

### Segmentation Strategy

| Source | Destination | Policy |
|--------|------------|--------|
| Consumer Banking | Investment Banking | BLOCKED (Chinese Wall) |
| Consumer Banking | Shared Services | ALLOWED (specific ports only) |
| Investment Banking | Asset Management | BLOCKED (Chinese Wall) |
| Any Business Line | Internet | Via NF with egress filtering |
| Any Business Line | Compliance/Audit | ALLOWED (read-only) |
| Treasury | All Lines | ALLOWED (specific approved flows for funding) |

### Key Controls

- **TGW Route Tables**: Separate route tables per business line
- **Network Firewall**: Enforces inter-business-line policies at the Inspection VPC
- **Deny all** inter-business-line traffic by default
- **Allow** only specific approved data flows
- **Full logging**: All cross-VPC communication attempts logged (even blocked ones) for compliance audit
- **Alert rules**: Any attempted cross-boundary communication triggers security alert

---

## 4. Case 3: Trading Systems Protection (Ultra-Low Latency)

### Business Problem

A client in the financial services industry operates high-frequency trading (HFT) systems that require:

- Latency < 1ms
- DDoS protection
- Strict allowlist of exchanges (NYSE, NASDAQ, CME)
- No unauthorized outbound connections

### Architecture Considerations

| Concern | Decision | Rationale |
|---------|----------|-----------|
| Latency | Stateless rules only for exchange traffic | Stateless = faster processing (microseconds vs milliseconds) |
| Security | Domain allowlist for non-exchange traffic | Limit egress attack surface |
| Availability | Multi-AZ with appliance mode | No single point of failure |
| Monitoring | FLOW logs only (no inline ALERT for latency-sensitive path) | Reduce processing overhead |

### Architecture Approach

- **Trading path**: Minimal stateless rules (IP allowlist of exchanges only)
- **Management path**: Full stateful inspection with domain filtering
- **Monitoring path**: FLOW logs for compliance, minimal inline inspection on critical path
- **Dedicated firewall**: Not shared with other workloads

### Allowlist Configuration

```suricata
# Allow connections to NYSE (stateless IP-based for lowest latency)
pass tcp $TRADING_NET any -> $NYSE_IPS 443 (msg:"NYSE Trading Allowed"; sid:200001; rev:1;)

# Allow connections to NASDAQ
pass tcp $TRADING_NET any -> $NASDAQ_IPS 443 (msg:"NASDAQ Trading Allowed"; sid:200002; rev:1;)

# Allow connections to CME
pass tcp $TRADING_NET any -> $CME_IPS 443 (msg:"CME Trading Allowed"; sid:200003; rev:1;)

# Alert on unusual patterns (potential market manipulation signals)
alert tcp $TRADING_NET any -> any any (msg:"Unexpected Trading Destination"; flow:to_server; sid:200050; rev:1;)

# Block everything else
drop ip $TRADING_NET any -> any any (msg:"Unauthorized Trading Egress"; sid:200099; rev:1;)
```

---

## 5. Case 4: Build Cluster in Financial Sector (CI/CD Egress Filtering)

### Business Problem

A client in the financial/banking sector operates a build cluster (CI/CD infrastructure) for developing financial applications. The cluster needs to access external package repositories while maintaining security and compliance.

### Architecture

```
Developer Workstations --> VPC (Build Cluster)
                                |
                        Network Firewall
                                |
                      Internet (External Repos)
                      - npm registry
                      - Maven Central
                      - PyPI
                      - Docker Hub
                      - GitHub/GitLab
```

### Requirements

- Compliance with financial regulations (FCA, PCI-DSS)
- Egress filtering to prevent data exfiltration
- Allowlist of authorized repositories only
- Complete logging for audit
- High availability (builds 24/7)

### Implementation

**Domain Allowlist (RulesSourceList):**

```
.npmjs.org
.registry.npmjs.org
.maven.org
.repo1.maven.org
.pypi.org
.files.pythonhosted.org
.docker.io
.registry-1.docker.io
.production.cloudflare.docker.com
.github.com
.gitlab.com
.githubusercontent.com
.amazonaws.com
```

**Suricata Rules (additional controls):**

```suricata
# Allow npm registry
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; dotprefix; content:".npmjs.org"; nocase; endswith; msg:"Allow npm registry"; flow:to_server, established; sid:300001; rev:1;)

# Allow Maven Central
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; dotprefix; content:".maven.org"; nocase; endswith; msg:"Allow Maven Central"; flow:to_server, established; sid:300002; rev:1;)

# Allow PyPI
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:"pypi.org"; endswith; msg:"Allow PyPI"; flow:to_server, established; sid:300003; rev:1;)

# Allow Docker Hub
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:"registry-1.docker.io"; endswith; msg:"Allow Docker Hub"; flow:to_server, established; sid:300004; rev:1;)

# Allow AWS services
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; dotprefix; content:".amazonaws.com"; nocase; endswith; msg:"Allow AWS services"; flow:to_server, established; sid:300005; rev:1;)

# Block unauthorized POST to non-approved destinations (prevent code exfiltration)
drop http $HOME_NET any -> $EXTERNAL_NET any (msg:"Block unauthorized POST"; flow:to_server, established; content:"POST"; http_method; sid:300098; rev:1;)

# Block all other TLS egress
drop tls $HOME_NET any -> $EXTERNAL_NET any (msg:"Block non-allowlisted TLS egress"; flow:to_server, established; sid:300099; rev:1;)
```

### Common Issues in Build Clusters

| Issue | Root Cause | Solution |
|-------|-----------|----------|
| npm install fails | CDN domain not in allowlist | Add CDN domains (e.g., objects.githubusercontent.com) |
| Docker pull timeout | Multiple domains needed for auth + CDN | Add all Docker Hub auth and CDN domains |
| Maven snapshot fails | Internal repo not in allowlist | Add internal Nexus/Artifactory domain |
| SSL errors | TLS inspection breaking pinned certs | Add exception for repos with cert pinning |
| pip install timeout | files.pythonhosted.org missing | Add both pypi.org and files.pythonhosted.org |

### Monitoring and Compliance

- **ALERT logs**: Capture all blocked egress attempts (potential exfiltration or misconfiguration)
- **FLOW logs**: Full traffic record for audit trail
- **CloudWatch Alarms**: Alert on unusual DroppedPackets spikes
- **S3 archival**: Long-term log retention for regulatory compliance
- Track volume of downloads per repository

---

## 6. Business Value Summary

| Capability | Business Value |
|-----------|---------------|
| Stateful inspection | Comply with network segmentation requirements |
| Domain filtering | Limit attack surface, prevent C2 communication |
| IDS/IPS (Suricata) | Detect and block known attack patterns |
| Centralized logging | Meet 7+ year retention requirements for audit |
| Multi-AZ HA | Meet uptime SLAs for critical financial systems |
| TGW integration | Centralized security for multi-account organizations |
| Egress filtering | Supply chain security for build infrastructure |
| Managed service | Reduce operational overhead vs. self-managed firewalls |

| Factor | Without NF | With NF |
|--------|-----------|---------|
| Compliance audit | Manual, costly | Automated logging and evidence |
| Incident response | Reactive | Proactive detection via IDS/IPS |
| Network segmentation | Complex SG/NACL management | Centralized policy engine |
| Regulatory fines risk | Higher | Reduced through demonstrable controls |
| Operational overhead | Multiple disparate tools | Single managed service |

---

## References

- [PCI Security Standards Council](https://www.pcisecuritystandards.org/)
- [AWS Network Firewall pricing](https://aws.amazon.com/network-firewall/pricing/)
- [Deployment models for AWS Network Firewall](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/)
- [Suricata examples - domain filtering](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-examples.html#suricata-example-domain-filtering)
