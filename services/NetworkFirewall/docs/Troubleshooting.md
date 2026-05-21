# AWS Network Firewall - Troubleshooting

## Full Layer 3-7 Troubleshooting Checklist

### Layer 3 - Network Connectivity
- [ ] Ping/telnet to destination server from EC2
- [ ] Traceroute shows complete path
- [ ] VPN/Direct Connect status = UP
- [ ] BGP routes propagated correctly
- [ ] VPC route table has route toward destination (on-premises, internet)
- [ ] VPN/DX virtual interface state = available

### Layer 4 - Transport
- [ ] Target port accessible with telnet/nc
- [ ] Security Group allows egress on required port
- [ ] Network ACL allows outbound on required port + ephemeral ports inbound (TCP 1024-65535)
- [ ] Network Firewall (if present) allows the required TCP/UDP port
- [ ] VPC Flow Logs show ACCEPT (not REJECT)

### Layer 5-7 - Application
- [ ] DNS resolves hostname correctly
- [ ] Application-specific connectivity test (e.g., tnsping for Oracle)
- [ ] Configuration files correct (e.g., tnsnames.ora, sqlnet.ora)
- [ ] Appropriate timeouts configured
- [ ] Service/listener running on destination server
- [ ] Credentials correct

### AWS-Specific Checks
- [ ] VPC route table has route to firewall endpoint
- [ ] Firewall endpoints in state READY
- [ ] CloudWatch metrics show traffic (Packets > 0)
- [ ] VPC Flow Logs show ACCEPT at firewall endpoint ENIs
- [ ] Alert logs show no unexpected drops for the flow in question
- [ ] Stateful DroppedPackets correlated with specific denied traffic

### On-Premises (Requires coordination with customer)
- [ ] Corporate firewall allows AWS CIDR to destination port
- [ ] Server firewall (iptables/firewalld) allows inbound on port
- [ ] Service configured to accept remote connections (e.g., Oracle Listener accepting remote)

---

## Asymmetric Routing

### Problem

Network Firewall is **stateful** - it maintains connection state. If request and response take different paths through different firewall endpoints, the return endpoint has no connection state and **drops the traffic**.

### Symptoms

- Intermittent connectivity issues
- Connections work in one direction but fail in the other
- DroppedPackets metrics increase
- Stream exception policy triggers

### Root Causes

- Multi-AZ deployment without TGW Appliance Mode enabled
- Transit Gateway routing return traffic to a different AZ
- Cross-AZ communication between spoke VPCs
- Failover scenarios where traffic shifts AZs
- Multiple paths available (ECMP) without flow pinning
- Incorrect route table pointing to wrong AZ's firewall endpoint

### Solutions

1. **Enable TGW Appliance Mode** on the Inspection VPC attachment
   - Ensures both directions of a flow use the same AZ
2. Ensure route tables direct traffic to the **local AZ** firewall endpoint
3. Use separate route tables per AZ pointing to the respective AZ endpoint
4. Verify return traffic follows the same path
5. Place NAT Gateway in the same AZ as its corresponding firewall endpoint

---

## Long-Running TCP Connections

### Problem

Long-lived TCP connections (hours/days) can be interrupted by:
- TCP idle timeout (default: 350 seconds, configurable: 60-6000 seconds)
- NAT Gateway idle timeout (350 seconds)
- State table cleanup in the firewall
- Stream exception policy triggers on midstream connections

### Symptoms

- Connections drop after period of inactivity
- Application gets "connection reset" errors after idle period
- Intermittent failures on persistent connections (DB connections, WebSockets, gRPC)

### Solutions

- Configure appropriate **TCP idle timeout** in firewall policy (up to 6000 seconds)
- Implement TCP keepalive at the application level (interval < idle timeout)
- Use connection pooling with health checks
- Monitor for stream exception events in logs
- Network Firewall has session state sharing enabled - synchronizes flow data across instances in the same AZ (eliminates midstream flow issues during scale-in/patching)

### Reference

- [Implementing Long-Running TCP Connections within VPC Networking](https://aws.amazon.com/blogs/networking-and-content-delivery/implementing-long-running-tcp-connections-within-vpc-networking/)

---

## VPC Flow Logs Verification

VPC Flow Logs are critical for NF troubleshooting. Use them to confirm traffic is reaching the firewall endpoint ENIs.

### Steps

1. **Identify firewall endpoint ENIs**:
   ```bash
   aws network-firewall describe-firewall --firewall-name <name> \
     --query 'FirewallStatus.SyncStates'
   ```

2. **Enable VPC Flow Logs** on the firewall endpoint ENIs (if not already enabled)

3. **Check for ACCEPT vs REJECT**:
   - ACCEPT on firewall ENI = traffic reaches firewall
   - REJECT on firewall ENI = NACL blocking at firewall subnet level
   - No entries = traffic never reaches firewall (routing issue)

4. **Compare ingress vs egress on the firewall ENI**:
   - Traffic IN but not OUT = firewall is dropping the traffic (check ALERT logs)
   - Traffic IN and OUT = firewall passes it; issue is downstream
   - Traffic OUT but not IN on return = asymmetric routing

5. **Correlate with NF Alert Logs**: Match flow log entries with firewall alert events

---

## Stream Exception Policy

- **Default:** Drop
- Triggered when NF encounters midstream traffic (connections that started before the firewall was in the path)
- Configurable in the firewall policy
- Options: Drop, Continue, Reject
- Common scenario: firewall replacement/scaling brings new instance that sees ongoing connections without initial handshake

---

## Common Errors and Solutions

### Traffic Dropped with No Apparent Rule Match

- Check `StatefulDefaultActions` - if set to `aws:drop_established`, unmatched traffic is dropped
- Verify rule evaluation order (strict order vs action order)
- Check if `established` flow keyword in rules is preventing match on new connections
- Review `StreamExceptionPolicy` - midstream traffic may be dropped
- Query Alert logs to see if there were drops for the flow in question

### Firewall Shows DroppedPackets in CloudWatch

- DroppedPackets showing non-zero values 24x7 is **normal** for blocked traffic
- Check ALERT logs for specific drop reasons
- Verify drops correspond to expected blocked traffic vs legitimate traffic
- Review stateful rules for unintended drop/reject actions

### Domain Filtering Not Working (HTTPS)

- Network Firewall inspects SNI in TLS ClientHello for domain filtering
- If SNI is missing or encrypted (ECH), domain rules cannot match
- TCP handshake completes BEFORE domain inspection (NF needs to see the ClientHello with SNI)
- Verify with: `openssl s_client -connect <host>:443 -servername <domain>`
- Check if application uses IP directly (bypasses SNI)

### Rules Not Matching

- Verify `flow` keyword is set correctly (`to_server`, `established`)
- Check rule group priority and evaluation order
- Confirm `HOME_NET` and `EXTERNAL_NET` variables are set correctly
- Test with a simple alert rule first to confirm traffic reaches the firewall

### Autoscaling and Session State

- Network Firewall has session state sharing enabled
- Synchronizes flow data across firewall instances in the same AZ
- This eliminates midstream flow issues during scale-in/patching
- No customer notification mechanism for scaling events (managed service)
- No scaling activity does not necessarily mean no issue - check other components

---

## Useful Suricata Rules for Allowlisting

```suricata
pass http $HOME_NET any -> $EXTERNAL_NET any (http.host; dotprefix; content:".amazonaws.com"; endswith; msg:"matching HTTP allowlisted FQDNs"; flow:to_server, established; sid:1; rev:1;)
pass tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; dotprefix; content:".amazonaws.com"; nocase; endswith; msg:"matching TLS allowlisted FQDNs"; flow:to_server, established; sid:2; rev:1;)
drop http $HOME_NET any -> $EXTERNAL_NET any (http.header_names; content:"|0d 0a|"; startswith; msg:"not matching any HTTP allowlisted FQDNs"; flow:to_server, established; sid:3; rev:1;)
drop tls $HOME_NET any -> $EXTERNAL_NET any (msg:"not matching any TLS allowlisted FQDNs"; flow:to_server, established; sid:4; rev:1;)
```

---

## Diagnostic Commands

```bash
# Describe firewall and check endpoint status
aws network-firewall describe-firewall --firewall-name <name>

# Check firewall policy
aws network-firewall describe-firewall-policy --firewall-policy-name <name>

# Check logging configuration
aws network-firewall describe-logging-configuration --firewall-name <name>

# List rule groups
aws network-firewall list-rule-groups

# Describe specific rule group
aws network-firewall describe-rule-group --rule-group-name <name> --type STATEFUL

# Test connectivity
curl -v https://www.example.com
openssl s_client -connect www.example.com:443 -servername www.example.com
telnet <destination-ip> <port>
nc -zv <destination-ip> <port>
```

---

## References

- [Troubleshooting general issues in AWS Network Firewall](https://docs.aws.amazon.com/network-firewall/latest/developerguide/troubleshooting-general-issues.html#troubleshoot-dropped-traffic-flows)
- [Troubleshooting rules](https://docs.aws.amazon.com/network-firewall/latest/developerguide/troubleshooting-rules.html)
- [Implementing long-running TCP Connections within VPC networking](https://aws.amazon.com/blogs/networking-and-content-delivery/implementing-long-running-tcp-connections-within-vpc-networking/)
- [Transit gateway asymmetric routing](https://docs.aws.amazon.com/prescriptive-guidance/latest/inline-traffic-inspection-third-party-appliances/transit-gateway-asymmetric-routing.html)
- [Best practices for deploying Gateway Load Balancer](https://aws.amazon.com/blogs/networking-and-content-delivery/best-practices-for-deploying-gateway-load-balancer/)
- [How do I configure my Network Firewall rules to block or allow specific domains?](https://repost.aws/knowledge-center/network-firewall-configure-domain-rules)
- [Issue with NF Suricata Rules Allowing Connections to Non-Allowlisted Public IPs](https://repost.aws/questions/QUVmmGWikSSjS9Uog4p0DV_w/issue-with-aws-network-firewall-suricata-rules-allowing-connections-to-non-whitelisted-public-ips)
- [How do I set up an AWS Network Firewall with a NAT gateway?](https://repost.aws/knowledge-center/network-firewall-set-up-with-nat-gateway)
- [AWS Network Firewall - allow access only to specific domains](https://repost.aws/questions/QUv0efLqqQQeWjOv4OdYU2Ug/aws-network-firewall-allow-access-only-to-specific-domains)
- [Stateful domain list rule groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html)
- [Domain allowlist AWS Network Firewall](https://repost.aws/questions/QUXe6wOQg3QWiv064YfXtPxA/domain-allowlist-aws-network-firewall)
- [Suricata examples - domain filtering](https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-examples.html#suricata-example-domain-filtering)
- [AWS Network Firewall pricing](https://aws.amazon.com/network-firewall/pricing/)

## Additional References (from cases)

- [General troubleshooting](https://docs.aws.amazon.com/network-firewall/latest/developerguide/troubleshooting-general-issues.html)
- [Asymmetric routing with TGW](https://docs.aws.amazon.com/network-firewall/latest/developerguide/troubleshooting-general-issues.html#troubleshoot-check-asymmetric-routing-tg)
- [Security services best practices](https://aws.github.io/aws-security-services-best-practices/guides/network-firewall/)
- [Flow timeouts API](https://docs.aws.amazon.com/network-firewall/latest/APIReference/API_FlowTimeouts.html)
- [Configurable TCP idle timeout (2024)](https://aws.amazon.com/about-aws/whats-new/2024/10/aws-network-firewall-configurable-tcp-idle-timeout/)
- [GWLB TCP idle timeout blog](https://aws.amazon.com/blogs/networking-and-content-delivery/introducing-configurable-tcp-idle-timeout-for-gateway-load-balancer/)
- [Max connections NF](https://guide.aws.dev/questions/QUTUE6vfkST4qWqBfSCOh3yg/max-number-of-connections-network-firewall)
- [Quotas](https://docs.aws.amazon.com/network-firewall/latest/developerguide/quotas)
- [Stateful rules quota increase (2024)](https://aws.amazon.com/about-aws/whats-new/2024/05/aws-network-firewall-increases-quota-stateful-rules/)
