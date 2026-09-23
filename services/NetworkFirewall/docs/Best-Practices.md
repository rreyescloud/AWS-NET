# AWS Network Firewall — Best Practices

Source: https://aws.github.io/aws-security-services-best-practices/guides/network-firewall/

---

## Architecture & Deployment

### Three Deployment Patterns:
1. **Distributed** — NFW in each individual VPC
2. **Centralized** — NFW in inspection VPC attached to TGW (East-West + North-South)
3. **Combined** — Centralized for E-W + egress; distributed for internet ingress

### Multi-AZ:
> "For resiliency we highly recommend a firewall endpoint/subnet be deployed for each AZ that you have workloads in."

### Routing:
> "Network Firewall does not support Asymmetric routing so you will need to ensure symmetric routing is configured in your VPC."

- Route tables must account for flows in BOTH directions
- Ensure traffic goes to LOCAL AZ endpoint (avoids cross-AZ charges)

---

## TGW Appliance Mode (Critical for Centralized)

> When using TGW in centralized deployment for E-W inspection, "the TGW's appliance mode option needs to be enabled for the attachments in the Inspection VPC."

Without appliance mode: "return path traffic could land on an endpoint in a different AZ, which will prevent the Network Firewall from correctly evaluating the traffic."

---

## Asymmetric Routing

### Root Causes:
- Route tables not accounting for bidirectional flows
- TGW appliance mode not enabled
- Stateless rules causing only one side of flow to reach stateful engine

### Stateless Rule Analyzer:
Use built-in analyzer (Console "Analyze" button, API, or CLI with `DescribeRuleGroup --AnalyzeRuleGroup`) to detect asymmetric forwarding issues.

### Fix:
Add rules matching return traffic. Re-run analyzer to confirm.

### Guidance:
> "Stateless rules should be used very sparingly because they can easily cause asymmetric flow forwarding issues."

Best practice: set stateless default action to "Forward to stateful rule groups" with no stateless rules.

---

## Stateful Engine Behavior

### Flow State Table:
- Once a flow is allowed, Suricata places it in state table — no more DPI on that flow
- New rules do NOT apply to existing flows in the state table

### Clear State Table:
Edit "Stream exception policy" to different value, save, change back. Forces re-evaluation.

### Stream Exception Policy:
- **Continue** or **Reject** recommended for production
- **Drop** is disruptive (silently blocks mid-stream, no TCP Reset)

### Strict vs Action Order:
- **Strict** (recommended): rules processed in defined order; for firewall use cases
- **Action Order**: Suricata default; for IDS only

### Default Actions:
- **Drop established** — drops unmatched established connections; allows L7 inspection for domain filtering
- **Application drop established** — waits for app-layer data before dropping (handles post-quantum fragmented TLS ClientHello)

---

## TLS Inspection

- TLS SNI filtering: standard for egress domain filtering without decryption
- TLS decryption: blocks forged SNI by validating against server cert
- JA3/JA4 filtering: TLS fingerprinting (like HTTP User-Agent)
- JA4 No-SNI rejection: blocks connections without server name
- Post-quantum: "Application drop established" handles fragmented ClientHello

---

## NAT Gateway Interaction

### Centralized Egress via TGW:
Use "Network Firewall's Native Transit Gateway Support" to reduce endpoint count.

### Multi-Endpoint Support:
Reduced cost for secondary endpoints when sharing across accounts/VPCs.

### VPC Endpoints to Bypass FW:
- Free S3/DynamoDB gateway endpoints → skip firewall
- PrivateLink for 3rd-party services that don't need inspection
- VPC peering for shared services access without firewall

---

## Cost Optimization

- Centralized via TGW Native Support → fewer endpoints
- Multi-endpoint support → reduced secondary endpoint cost
- S3/DynamoDB VPC endpoints → free, bypass FW
- PrivateLink → skip inspection for trusted 3rd parties
- TGW route tables → prevent unnecessary cross-VPC inspection
- Local AZ routing → avoid cross-AZ charges
- DNS Firewall → pre-filter before traffic hits NFW
- Flexible Cost Allocation for TGW → per-account/BU chargeback

---

## $HOME_NET Variable

Set at **firewall policy level** to ALL RFC 1918:
- 10.0.0.0/8
- 172.16.0.0/12
- 192.168.0.0/16

Default is only the VPC CIDR — insufficient for centralized deployments.

If set at rule group level, also set `$EXTERNAL_NET` at rule group level.

---

## Rule Design

- Use `flow:to_server` to prevent lower-layer rules (TCP) from overriding higher-layer (TLS/HTTP)
- Unique SIDs across ALL rule groups (not enforced cross-group)
- Capacity cannot be changed after creation
- Max 20 combined rule groups (managed + custom)
- Block QUIC to force TLS over TCP for inspection
- Use DNS Firewall to block at DNS layer ("closest to packet source")

---

## Logging

- **Alert logs**: Suricata IPS, L7 attributes, protocol detection
- **Flow logs**: 5-tuple, traffic volume, top producers/consumers
- Correlate flow + alert logs via `flow_id` in CloudWatch Logs Insights
- Use threshold directives to reduce log volume: `threshold: type limit, track by_both, seconds 600, count 1`
- Use `noalert` on rules that don't need logging
- Use built-in "Traffic Analysis Report" to find domains driving processing charges
