# AWS Network Firewall - Rules

## Stateless Rules

### Overview
- Evaluate each packet in **isolation** (no connection tracking)
- Process based on standard network attributes
- Applied to all network packets immediately
- Default action: Forward to stateful rule groups

### Capacity Calculation

Stateless rule capacity is calculated by multiplying match settings:

| Match Settings | Example | Capacity |
|---|---|---|
| 2 protocols + 3 sources | UDP, TCP + 3 IPs | 6 |
| 30 protocols + 3 sources | 30 + 3 IPs | 90 |
| 30 protocols + 3 sources + 5 destinations | 30 + 3 + 5 | 450 |

- No criteria = Value of 1
- All/Any settings = Value of 1
- Maximum capacity: 30,000

### Fragmented Packet Handling
- Configurable for UDP fragments
- Auto-drop for other protocol fragments

---

## Stateful Rules

### Types of Stateful Rule Groups

1. **Suricata compatible rule strings** - Full Suricata syntax for maximum flexibility
2. **Domain list** - Allowlist/denylist of domain names (HTTP/HTTPS)
3. **Standard stateful rules** - Standard 5-tuple network connection attributes

### Stateful Rule Actions (Priority Order)
1. **pass** - Allow traffic (stops further scanning of flow)
2. **drop** - Silently discard
3. **reject** - Discard and send TCP RST
4. **alert** - Log the traffic and continue

### Default Stateful Action
- `pass` is the default action for default action order firewall policy

---

## Rule Evaluation Order

### A. Action Order (Default)

- Suricata action priority (highest to lowest): pass > drop > reject > alert
- Uses Suricata `priority` keyword (range: 1-255, lowest number = highest priority)
- Pass action stops further scanning of flow
- Protocol layer does NOT impact evaluation order
- `flow` keyword recommended for protocol identification

### B. Strict Evaluation Order

- Rule groups evaluated by priority (lowest number first)
- Rules processed in defined order within each group
- Requires established connection for domain lists
- Default actions options:
  - **Drop actions** (choose one): Drop all, Drop established
  - **Alert actions** (can choose both): Alert all, Alert established

---

## Suricata Syntax

### Rule Format

Reference: [Suricata Rules Format](https://docs.suricata.io/en/suricata-6.0.9/rules/intro.html)

```
action protocol source_ip source_port -> destination_ip destination_port (options;)
```

### Key Keywords
- `ip_proto` - Match IP protocol
- `content` - Match content in payload
- `startswith` / `endswith` - Content position modifiers
- `dotprefix` - Match domain with dot prefix
- `flow` - Match flow direction and state
- `http.host` - Match HTTP Host header
- `tls.sni` - Match TLS SNI field
- `http_uri` - Match HTTP URI
- `http_method` - Match HTTP method
- `dsize` - Match payload size

### Example: SQL Injection Detection

```suricata
alert http any any -> any any (msg:"SQL Injection Attempt"; flow:to_server; content:"SELECT"; http_uri; content:"FROM"; http_uri; sid:2000001;)
```

### Example: Malicious User-Agent Block

```suricata
drop http any any -> any any (msg:"Malicious User-Agent"; flow:to_server; content:"User-Agent|3a| sqlmap"; sid:2000002;)
```

### Example: Data Exfiltration Detection (Large POST)

```suricata
alert http any any -> any any (msg:"Large POST - Possible Data Exfiltration"; flow:to_server; content:"POST"; http_method; dsize:>1000000; sid:2000003;)
```

### Example: Domain Allowlist with Suricata Rules

```suricata
pass http $HOME_NET any -> $EXTERNAL_NET any (http.host; dotprefix; content:".amazonaws.com"; endswith; msg:"matching HTTP allowlisted FQDNs"; flow:to_server, established; sid:1; rev:1;)
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; dotprefix; content:".amazonaws.com"; nocase; endswith; msg:"matching TLS allowlisted FQDNs"; flow:to_server, established; sid:2; rev:1;)
drop http $HOME_NET any -> $EXTERNAL_NET any (http.header_names; content:"|0d 0a|"; startswith; msg:"not matching any HTTP allowlisted FQDNs"; flow:to_server, established; sid:3; rev:1;)
drop tls $HOME_NET any -> $EXTERNAL_NET any (msg:"not matching any TLS allowlisted FQDNs"; flow:to_server, established; sid:4; rev:1;)
```

---

## Domain List Filtering

- Define allowlist or denylist of domain names
- Supports HTTP and HTTPS protocols
- For HTTPS: matches on TLS SNI field in ClientHello
- Requires established connection
- Capacity based on expected number of domains

### How Domain Filtering Works

1. TCP 3-way handshake is allowed (NF cannot see the domain yet)
2. TLS ClientHello contains the SNI field with the domain name
3. NF inspects the SNI and applies domain rules
4. If domain is blocked, connection is dropped/rejected

---

## Rule Group Capacity Management

- Fixed at creation time - **cannot be changed later**
- Must leave room for growth
- Maximum: 30,000 for both stateless and stateful
- Viewable in console, tracks consumption per rule group

### Check Capacity via CLI

```bash
aws network-firewall describe-rule-group --type STATEFUL --rule-group-name "stateful-rg-name" --profile <yourAWSCLIProfile> --region <yourAWSRegion>
```

### Change Propagation
- Changes propagated within seconds
- Brief inconsistency period possible
- Stateful rules: applied only to **new** traffic flows
- Stateless rules: applied to **all** packets immediately
- TLS inspection changes: interrupts matching traffic flows

---

## References

- [Suricata Rules Format](https://docs.suricata.io/en/suricata-6.0.9/rules/intro.html)
- [Suricata Flow Keywords](https://docs.suricata.io/en/suricata-6.0.9/rules/flow-keywords.html#flow)
- [Suricata Meta Keywords](https://docs.suricata.io/en/suricata-6.0.9/rules/meta.html)
- [Managing Evaluation Order](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-rule-evaluation-order.html)
- [Stateful Domain List Rule Groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html)
- [Stateful Rules Examples](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-examples.html)
- [Domain Filtering Examples](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-examples.html#suricata-example-domain-filtering)
- [How to Configure Domain Rules](https://repost.aws/knowledge-center/network-firewall-configure-domain-rules)
- [Domain Allowlist Discussion](https://repost.aws/questions/QUXe6wOQg3QWiv064YfXtPxA/domain-allowlist-aws-network-firewall)
- [Allow Access Only to Specific Domains](https://repost.aws/questions/QUv0efLqqQQeWjOv4OdYU2Ug/aws-network-firewall-allow-access-only-to-specific-domains)
- [Suricata Rules Allowing Non-Allowlisted IPs](https://repost.aws/questions/QUVmmGWikSSjS9Uog4p0DV_w/issue-with-aws-network-firewall-suricata-rules-allowing-connections-to-non-whitelisted-public-ips)
- [Analyzing Stateless Rule Groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateless-rule-group-analyzer.html)
- [Troubleshooting Rules](https://docs.aws.amazon.com/network-firewall/latest/developerguide/troubleshooting-rules.html)
- [Setting Rule Group Capacity](https://docs.aws.amazon.com/network-firewall/latest/developerguide/rule-group-managing.html#nwfw-rule-group-capacity)
- [NF Quotas](https://docs.aws.amazon.com/network-firewall/latest/developerguide/quotas.html)
- [Limitations and Caveats for Stateful Rules](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-limitations-caveats.html)
- [IP Set References in Rule Groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/rule-groups-ip-set-references.html)
- [AWS re:Inforce 2023 - Policy and Suricata Rule Creation (NIS308)](https://www.youtube.com/watch?v=67pVOv3lPlk&t=1756s)

## Additional References (from cases)

- [Basic stateful rules](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-basic.html)
- [Domain list rule groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html)
- [MatchAttributes API](https://docs.aws.amazon.com/network-firewall/latest/APIReference/API_MatchAttributes.html)
- [Troubleshoot rule issues](https://repost.aws/knowledge-center/network-firewall-troubleshoot-rule-issue)
- [Configure domain rules](https://repost.aws/knowledge-center/network-firewall-configure-domain-rules)
- [Strict order evaluation with Stream Exception](https://repost.aws/questions/QURCm6F7jDQ9qlyZuxYRJaOA/network-firewall-shows-aws-alert-strict-action-when-it-set-with-strict-order-stateful-engine-option)
- [Allow only specific domains](https://repost.aws/questions/QUv0efLqqQQeWjOv4OdYU2Ug/aws-network-firewall-allow-access-only-to-specific-domains)
- [HTTP host matching across TCP boundaries (Suricata forum)](https://forum.suricata.io/t/http-host-matching-not-working-across-tcp-packet-boundaries/921/3)
- [Stream Exception Policy explained](https://repost.aws/questions/QUmDTTM3BERNydzEMiMDJt9w/explain-how-stream-exception-policy-works-in-a-anfw-policy)
- [Stream Exception Policy docs](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stream-exception-policy.html)
