# AWS Network Firewall - Labs

## Lab 1: Configure Domain List for 400 Domains

**Objective:** Replicate a high-volume domain filtering scenario with a large domain allowlist.

### Steps

1. Create a `domains.txt` file with 400 domains (one per line)
2. Create a Firewall Policy with `RulesSourceList`
3. Associate to VPC and configure route tables to direct traffic through the firewall
4. Validate connectivity with curl/openssl from EC2

### Validation Commands

```bash
# Verify firewall endpoint status
aws network-firewall describe-firewall --firewall-name my-firewall

# Check endpoint sync state (must be READY)
aws network-firewall describe-firewall --firewall-name my-firewall \
  --query 'FirewallStatus.SyncStates'

# Test HTTP/HTTPS connectivity to an allowed domain
curl -v https://www.cisco.com

# Test TLS connectivity and verify SNI is sent correctly
openssl s_client -connect www.google.es:443 -servername www.google.es

# Test a domain that should be blocked
curl -v https://www.blocked-domain.com
# Expected: connection timeout or reset

# Test domain resolution
nslookup www.example.com

# Verify from firewall ALERT logs (CloudWatch)
aws logs filter-log-events \
  --log-group-name /aws/network-firewall/alerts \
  --filter-pattern "blocked"
```

### How Domain Filtering Works (Flow)

```
Client                        Firewall                      Server
  |                               |                              |
  |---- TCP SYN --------------->|                              |
  |    (only IPs, no domain)   | "I don't know the domain     |
  |                               |  yet, let handshake pass"   |
  |                               |------- TCP SYN ------------>|
  |<------------------------------|<---- TCP SYN-ACK ------------|
  |---- TCP ACK --------------->|------- TCP ACK ------------->|
  |                               |                              |
  |-- TLS ClientHello -------->|                              |
  |   SNI: "www.google.com"    | "Now I know the domain!       |
  |                               |  Check my rules..."          |
  |                               |  OK: domain allowed          |
  |                               |--- TLS ClientHello -------->|
  |                               |                              |
  |<==============================|<=== encrypted from here ====|
  |=== encrypted traffic =======>|=== onwards ================>|
```

### Key Observations

- Domain list rules inspect the **SNI field** in TLS ClientHello for HTTPS
- HTTP traffic uses the **Host header** for domain matching
- TCP handshake completes BEFORE domain inspection (NF needs to see the ClientHello with SNI first)
- The 400-domain list tests rule group capacity (each domain consumes approximately 1 capacity unit)
- Large domain lists (400+) work but verify capacity limits of the rule group

---

## Lab 2: Compare Performance - Domain List vs PCRE

**Objective:** Measure the performance impact of using domain lists vs PCRE (regex) for domain filtering.

### Scenario

Create two rule groups:
- **Rule Group A**: `RulesSourceList` with 100 domains (native domain list - optimized internally)
- **Rule Group B**: Suricata rules with `pcre` keyword matching the same 100 domains

### Traffic Generation with Apache Bench

```bash
# Install Apache Bench
sudo yum install -y httpd-tools

# Generate 1000 requests, 10 concurrent, to an allowed domain
ab -n 1000 -c 10 https://www.allowed-domain.com/

# Generate traffic to multiple domains (script)
for domain in $(cat test-domains.txt); do
  ab -n 100 -c 5 https://$domain/ &
done
wait

# Measure individual request latency
time curl -o /dev/null -s -w "%{time_total}\n" https://www.example.com

# Run 100 iterations and calculate average
for i in $(seq 1 100); do
  curl -o /dev/null -s -w "%{time_total}\n" https://www.example.com
done | awk '{sum+=$1} END {print "Average:", sum/NR, "seconds"}'
```

### CloudWatch Metrics to Observe

| Metric | What to Compare |
|--------|----------------|
| `Packets` | Total packets processed by each rule group config |
| `DroppedPackets` | Packets dropped (should be similar if rules are equivalent) |
| `PassedPackets` | Packets passed through |
| Latency (application-measured) | Average response time from EC2 perspective |

### Monitoring Commands

```bash
# Get metrics from CloudWatch
aws cloudwatch get-metric-statistics \
  --namespace AWS/NetworkFirewall \
  --metric-name Packets \
  --dimensions Name=FirewallName,Value=my-firewall \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 \
  --statistics Sum
```

### Expected Results

- **RulesSourceList** (Domain List): Optimized internally for domain matching, lower CPU overhead per packet
- **PCRE rules**: Higher per-packet CPU cost due to regex evaluation
- For large domain sets (100+), native domain list significantly outperforms pcre-based matching
- Domain lists are the recommended approach for simple domain allow/deny scenarios

---

## Lab 3: Advanced Suricata Rules - IDS/IPS

**Objective:** Detect and block attacks using Suricata stateful rules in IDS/IPS mode.

### Rules to Implement

```suricata
# Detect SQL Injection attempt in URI
alert http any any -> any any (msg:"SQL Injection Attempt"; flow:to_server; content:"SELECT"; http_uri; content:"FROM"; http_uri; sid:2000001; rev:1;)

# Block malicious User-Agent (e.g., sqlmap)
drop http any any -> any any (msg:"Malicious User-Agent"; flow:to_server; content:"User-Agent|3a| sqlmap"; sid:2000002; rev:1;)

# Detect potential data exfiltration (large POST body > 1MB)
alert http any any -> any any (msg:"Large POST - Possible Data Exfiltration"; flow:to_server; content:"POST"; http_method; dsize:>1000000; sid:2000003; rev:1;)
```

### Testing the Rules

```bash
# Test SQL injection detection (should trigger ALERT, traffic passes)
curl "http://target-server/page?id=1+SELECT+name+FROM+users"

# Test malicious user-agent (should be DROPPED, connection reset)
curl -H "User-Agent: sqlmap/1.0" http://target-server/

# Test large POST (should trigger ALERT, traffic passes)
dd if=/dev/zero bs=1M count=2 | curl -X POST -d @- http://target-server/upload
```

### Verification

```bash
# Check ALERT logs in CloudWatch for SQL injection
aws logs filter-log-events \
  --log-group-name /aws/network-firewall/alerts \
  --filter-pattern "SQL Injection"

# Check for malicious user-agent drops
aws logs filter-log-events \
  --log-group-name /aws/network-firewall/alerts \
  --filter-pattern "Malicious User-Agent"

# Check for large POST alerts
aws logs filter-log-events \
  --log-group-name /aws/network-firewall/alerts \
  --filter-pattern "Large POST"

# Verify DroppedPackets metric increases for drop rules
aws cloudwatch get-metric-statistics \
  --namespace AWS/NetworkFirewall \
  --metric-name DroppedPackets \
  --dimensions Name=FirewallName,Value=my-firewall \
  --start-time $(date -u -d '10 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum
```

### Key Learning Points

| Action | Behavior | Mode |
|--------|----------|------|
| `alert` | Logs but allows traffic | IDS (detection only) |
| `drop` | Silently drops the packet | IPS (prevention) |
| `reject` | Drops and sends RST/ICMP unreachable | IPS (prevention with notification) |
| `pass` | Allows traffic (stops further rule evaluation) | Allow |

- `flow:to_server` ensures rule only matches client-to-server direction
- `http_uri`, `http_method`, `http_header` are sticky buffers for HTTP inspection
- `dsize` checks payload size (useful for exfiltration detection)
- Rules are processed in order - first match wins (in strict evaluation order)

### Additional IDS/IPS Rules Examples

```suricata
# Detect directory traversal
alert http any any -> any any (msg:"Directory Traversal Attempt"; flow:to_server; content:"../"; http_uri; sid:2000004; rev:1;)

# Block known malware download by file extension
drop http any any -> any any (msg:"Executable Download Blocked"; flow:to_client; content:".exe"; http_uri; sid:2000005; rev:1;)

# Detect SSH brute force (threshold-based)
alert tcp any any -> any 22 (msg:"Possible SSH Brute Force"; flow:to_server; flags:S; threshold:type both, track by_src, count 5, seconds 60; sid:2000006; rev:1;)
```

---

## Lab 4: Multi-AZ Architecture with TGW Centralized Inspection

**Objective:** Design and deploy a resilient Multi-AZ architecture with centralized traffic inspection using Transit Gateway.

### Components

- 3 AZs with firewall endpoints
- Inspection VPC with Transit Gateway
- Centralized inspection of multiple spoke VPCs

### Architecture

```
+-------------------+     +-------------------+     +-------------------+
|   Spoke VPC 1     |     |   Spoke VPC 2     |     |   Spoke VPC 3     |
|   10.1.0.0/16     |     |   10.2.0.0/16     |     |   10.3.0.0/16     |
+--------+----------+     +--------+----------+     +--------+----------+
         |                          |                          |
         +-------------+------------+-------------+------------+
                       |
               +-------v--------+
               | Transit Gateway|
               | (Appliance     |
               |  Mode ON)      |
               +-------+--------+
                       |
          +------------v--------------+
          |      Inspection VPC       |
          |      10.100.0.0/16        |
          |                           |
          |  AZ-a    AZ-b    AZ-c    |
          |  +---+   +---+   +---+   |
          |  |NF |   |NF |   |NF |   |
          |  |EP |   |EP |   |EP |   |
          |  +---+   +---+   +---+   |
          |  +---+   +---+   +---+   |
          |  |TGW|   |TGW|   |TGW|   |
          |  |Sub|   |Sub|   |Sub|   |
          |  +---+   +---+   +---+   |
          |  +---+   +---+   +---+   |
          |  |NAT|   |NAT|   |NAT|   |
          |  |GW |   |GW |   |GW |   |
          |  +---+   +---+   +---+   |
          +---------------------------+
                       |
               +-------v--------+
               |  Internet GW   |
               +----------------+
```

### Traffic Flow

```
Spoke VPC --> TGW --> Inspection VPC (Firewall Subnet) --> TGW --> Internet/On-premises
```

### Routing Tables

**TGW Route Table - Spoke Association:**

| Destination | Target |
|-------------|--------|
| 0.0.0.0/0 | Inspection VPC attachment |
| 10.1.0.0/16 | Spoke VPC 1 attachment |
| 10.2.0.0/16 | Spoke VPC 2 attachment |
| 10.3.0.0/16 | Spoke VPC 3 attachment |

**Inspection VPC - TGW Subnet Route Table (per AZ):**

| Destination | Target |
|-------------|--------|
| 0.0.0.0/0 | Network Firewall endpoint (local AZ) |

**Inspection VPC - Firewall Subnet Route Table (per AZ):**

| Destination | Target |
|-------------|--------|
| 0.0.0.0/0 | NAT Gateway (local AZ) |
| 10.1.0.0/16 | Transit Gateway |
| 10.2.0.0/16 | Transit Gateway |
| 10.3.0.0/16 | Transit Gateway |

**Inspection VPC - NAT GW Subnet Route Table (per AZ):**

| Destination | Target |
|-------------|--------|
| 0.0.0.0/0 | Internet Gateway |
| 10.0.0.0/8 | Network Firewall endpoint (local AZ) |

### Critical Configuration: TGW Appliance Mode

**MUST enable Appliance Mode** on the Inspection VPC TGW attachment:

```bash
aws ec2 modify-transit-gateway-vpc-attachment \
  --transit-gateway-attachment-id tgw-attach-xxxxx \
  --options "ApplianceModeSupport=enable"
```

Without Appliance Mode:
- Cross-AZ flows may have asymmetric routing
- Return traffic could go to a different AZ's firewall endpoint
- That endpoint has no state for the connection = **DROPPED**

### Deployment Steps

1. Create Inspection VPC with 3 AZ subnets (Firewall, TGW, NAT GW per AZ)
2. Deploy Network Firewall with endpoints in each AZ
3. Create Transit Gateway
4. Attach Spoke VPCs and Inspection VPC to TGW
5. **Enable Appliance Mode** on Inspection VPC attachment
6. Configure TGW route tables (spoke and inspection associations)
7. Configure Inspection VPC route tables per AZ
8. Deploy NAT Gateways in each AZ
9. Create and attach firewall policy with desired rules
10. Test connectivity from each spoke VPC

### Validation

```bash
# Verify firewall endpoints are READY in all AZs
aws network-firewall describe-firewall --firewall-name centralized-fw \
  --query 'FirewallStatus.SyncStates'

# Verify TGW appliance mode is enabled
aws ec2 describe-transit-gateway-vpc-attachments \
  --transit-gateway-attachment-ids tgw-attach-xxxxx \
  --query 'TransitGatewayVpcAttachments[].Options'

# Test from Spoke VPC 1 to internet
curl -v https://www.google.com
traceroute www.google.com

# Test cross-spoke connectivity (Spoke 1 to Spoke 2)
ping 10.2.0.10

# Verify FLOW logs show inspected traffic
aws logs filter-log-events \
  --log-group-name /aws/network-firewall/flow

# Verify metrics show traffic being processed
aws cloudwatch get-metric-statistics \
  --namespace AWS/NetworkFirewall \
  --metric-name Packets \
  --dimensions Name=FirewallName,Value=centralized-fw \
  --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60 --statistics Sum
```

---

## General Lab Tips

- Always check `aws network-firewall describe-firewall` output for endpoint status (must be READY)
- Allow **5-10 minutes** for logs to appear after generating test traffic
- Use VPC Flow Logs on firewall endpoint ENIs to confirm traffic reaches the firewall
- Monitor CloudWatch metrics during tests for real-time visibility
- Use `openssl s_client` to verify TLS SNI is being sent correctly

---

## References

- [Suricata rule examples for Network Firewall](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-examples.html#suricata-example-domain-filtering)
- [Suricata rules documentation](https://docs.suricata.io/en/suricata-7.0.3/rules/intro.html)
- [Deployment models for AWS Network Firewall](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/)
- [AWS Network Firewall Developer Guide](https://docs.aws.amazon.com/network-firewall/latest/developerguide/)
- [TGW Multi-AZ configuration for NF](https://docs.aws.amazon.com/network-firewall/latest/developerguide/vpc-config-tgw-multi-az.html)
- [Stateful domain list rule groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html)
- [NF CloudWatch metrics](https://docs.aws.amazon.com/network-firewall/latest/developerguide/monitoring-cloudwatch.html#metrics)
