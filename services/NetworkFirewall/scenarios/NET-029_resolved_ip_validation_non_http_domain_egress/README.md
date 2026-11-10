# NET-029 — Network Firewall Resolved IP Validation: Domain Egress Control Beyond HTTP/HTTPS and SNI Spoofing

**Tier:** Case analysis — design scenario, no lab yet
**Status:** Design written on 2026-11-10 from the Route 53 resolved IP validation launch (2026-10-21). The CLI-first replication method is defined below. It hasn't been run in a lab yet.
**Services:** AWS Network Firewall (firewall policy, stateful rule groups, Suricata) · Route 53 Resolver · Route 53 Resolver DNS Firewall · CloudWatch Logs · EC2 · Systems Manager

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FNetworkFirewall%2Fscenarios%2FNET-029_resolved_ip_validation_non_http_domain_egress%2Farchitecture.drawio) · [Interactive explainer](explainers/explainer.html)

Related: [NET-011](../NET-011_suricata_domain_allowlist_syn_drop/) (a Suricata domain allowlist where the catch-all drop kills the SYN). Resolved-domain rules match on the destination IP, not on the SNI, so they avoid that whole class of problem.

## Business Context — Regulated Financial Services / Partner File Exchange

A regulated financial services company exchanges batch files with external partners over SFTP. It also calls partner APIs over HTTPS. All egress leaves through a centralized inspection VPC with Network Firewall.

Requirements:
- **Domain-based egress for every protocol.** SSH/SFTP may only reach the partners' published hostnames, never an arbitrary IP.
- **No IP allowlists.** Partners move hosts behind DNS without notice.
- **SNI/Host spoofing resistance.** The security team flagged that a process can set an allowed SNI and connect to any IP.
- **Gradual rollout.** Observe first, then enforce, with no surprise outages.

## Problem Statement

- The existing domain allowlist (`TLS_SNI`, `HTTP_HOST`) only inspects HTTP and HTTPS. SFTP on port 22 carries no domain, so the team could only filter it by CIDR, and the CIDRs keep changing.
- A red-team test opened a TLS connection with an allowed SNI to an unrelated IP address. The SNI rule passed it.
- **Considered and rejected:** TLS inspection for the spoofing gap. It doesn't help SSH, and it adds certificate operations for every partner.

## Architecture

- **Accounts:** `<ACCOUNT_NETWORK>` (inspection VPC, Network Firewall, DNS Firewall rule group) and `<ACCOUNT_APP>` (workload spokes).
- **Network:** spoke VPCs `10.10.0.0/16` and `10.20.0.0/16` → Transit Gateway → inspection VPC `10.0.0.0/16` (firewall endpoints + NAT) → internet.
- **DNS:** workloads resolve through the Route 53 Resolver of their own VPC (`.2`). A Route 53 Resolver DNS Firewall rule group `<rslvr-frg-anchor>` is associated with **every spoke VPC**.
- **Firewall policy:** `Route53ResolvedIPValidation` with `DNSFirewallRuleGroupId = <rslvr-frg-anchor>`, plus a stateful rule group with resolved-domain rules.

### Flow — allowed SFTP

1. The workload queries `sftp.partner.example.com` through its VPC Resolver.
2. The Resolver answers `198.51.100.20`. Network Firewall records `198.51.100.20 ↔ sftp.partner.example.com` in its resolved IP cache.
3. The workload opens TCP 22 to `198.51.100.20`, and the traffic reaches the firewall through the TGW.
4. **Policy level:** the source is in `SourceIPRanges` and the destination IP is in the cache, so the check passes.
5. **Rule level:** `pass tcp … 22 (aws_route_53_resolved_domain:".partner.example.com")` matches → PASS.

### Flow — blocked (no DNS, external DNS or spoofed SNI)

1. A process connects to `203.0.113.99`. It skipped DNS, or it used a public resolver or DoH.
2. The IP isn't in the cache, so the policy-level check fails and `Action` applies (`ALERT` / `DROP` / `REJECT`).
3. A spoofed TLS connection (`SNI = api.partner.example.com` toward `203.0.113.99`) also fails the rule that requires SNI **and** resolution to match.

## Root Cause / Design Recommendation

The gap is structural. SNI and Host are values the client controls, and non-HTTP protocols carry no domain at all. Resolved IP validation checks where the destination IP came from: did the VPC's Route 53 Resolver return it in a DNS answer? That closes both gaps without TLS inspection.

**How the feature works (from the documentation):**

| Level | Question | Where |
|---|---|---|
| Policy (required) | Was this IP resolved for *any* domain? | `Route53ResolvedIPValidation` on the firewall policy |
| Rule (optional) | Was this IP resolved for *this* domain? | Domain list target type `AWS_ROUTE_53_RESOLVED_DOMAIN`, or Suricata keyword `aws_route_53_resolved_domain` |

Policy fields:
- `Action` (required): `ALERT` \| `DROP` \| `REJECT`. `ALERT` only tags the connection in flow logs. `REJECT` acts as a drop on non-TCP traffic.
- `DNSFirewallRuleGroupId` (required, `^rslvr-frg-[0-9a-f]+$`): only resolutions in VPCs associated with this rule group count. There is one managed association per policy, and changing it is asynchronous.
- `DNSExpiryExceptionPolicy` (required): `CONTINUE` \| `DROP` \| `REJECT` for traffic to an IP whose cache entry has expired. Entries are kept longer than the record TTL.
- `SourceIPRanges` (optional, up to 100): sources to validate. If you omit the field, it covers the firewall VPC CIDR and any additional VPCs attached to the firewall. An **empty list validates nothing**.
- `BypassDestinationValidationIPRanges` (optional, up to 100): destinations exempt from the policy-level check.
- Both range fields apply to the policy level only. Resolved-domain rules always evaluate.

**Restart warning:** enabling or disabling the feature, or changing `DNSFirewallRuleGroupId`, may restart the stateful engine. Existing flows then follow the stream exception policy. Changing `Action`, the expiry policy or the IP ranges doesn't restart the engine.

## Resolution

Recommended rollout:

1. Enable FLOW and ALERT logging on the firewall.
2. Create (or reuse) a DNS Firewall rule group and associate it with every spoke VPC. It can be empty, because it only serves as the anchor.
3. Add the policy block with `Action = ALERT` and `DNSExpiryExceptionPolicy = CONTINUE` in a maintenance window. In the centralized TGW model, set `SourceIPRanges` explicitly to the spoke CIDRs (see the open questions).
4. Observe for one to two weeks. Exempt legitimate fixed-IP destinations with `BypassDestinationValidationIPRanges`.
5. Switch `Action` to `REJECT`. TCP clients then get a fast, clear failure.
6. Add the resolved-domain rules:

```
pass tcp $HOME_NET any -> $EXTERNAL_NET 22 (aws_route_53_resolved_domain:".partner.example.com"; flow:to_server, established; sid:100;)
drop tcp $HOME_NET any -> $EXTERNAL_NET 22 (flow:to_server, established; sid:101;)
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; content:"api.partner.example.com"; nocase; startswith; endswith; aws_route_53_resolved_domain:"api.partner.example.com"; flow:to_server, established; sid:110;)
```

Syntax rules: wrap the domain in double quotes. Use one keyword per rule, with commas for several domains. A leading dot is the wildcard (`.example.com`, not `*.example.com`). `reject` requires `tcp`, not `ip`.

## Troubleshooting Path

1. **Resolved-domain rules have no effect** → `describe-firewall-policy`: the policy-level block is missing.
2. **All traffic from one spoke fails validation** → `route53resolver list-firewall-rule-group-associations --firewall-rule-group-id <rslvr-frg-anchor>`: the VPC isn't associated.
3. **`ALERT` produces nothing** → `describe-logging-configuration`: FLOW logs are off.
4. **Nothing is validated** → `SourceIPRanges: []` was set instead of omitting the field.
5. **Long sessions or JVM clients fail after hours** → `DNSExpiryExceptionPolicy` is `DROP`/`REJECT`. Use `CONTINUE`.
6. **Sessions dropped when the feature was enabled** → engine restart plus a stream exception policy of `DROP`/`REJECT`.
7. **"Change rejected" on the policy** → a DNS Firewall association swap is still running. Wait for `FirewallPolicyStatus = ACTIVE`.
8. **Clients on premises fail** → they resolve with on-premises DNS, so the AWS Resolver never saw the answer. Forward their DNS to an inbound endpoint in an associated VPC (unverified), or exclude them from `SourceIPRanges`.

## Lab Reproduction Notes

Minimum lab: one account and one VPC with Network Firewall already in the egress path (distributed model). Add one EC2 test instance managed by SSM and a lab firewall policy with default action order. Cost drivers: firewall endpoint hours, NAT Gateway and CloudWatch Logs ingestion. Tear down the same day.

### Replication Method — CLI-first

Placeholders: `AWS_PROFILE=lab`, `VPC_ID=<vpc-app>`, `FW_NAME=riv-lab-fw`, `POLICY=riv-lab-policy`, `INSTANCE=<i-test>`.

**Step 0 — Check that the CLI supports the field and find its parent key.**
```bash
aws --version
aws network-firewall update-firewall-policy help | grep -n -i -B2 -A14 'Route53ResolvedIPValidation'
```
Expected: the structure appears. ⚠️ If the parent key isn't `Route53ResolvedIPValidation`, adjust the `jq` filters in steps 4, 7 and 9.

**Step 1 — Logging (one change per call).**
```bash
aws logs create-log-group --log-group-name /nfw/riv-lab/flow
aws logs create-log-group --log-group-name /nfw/riv-lab/alert
aws network-firewall update-logging-configuration --firewall-name $FW_NAME --logging-configuration \
  '{"LogDestinationConfigs":[{"LogType":"FLOW","LogDestinationType":"CloudWatchLogs","LogDestination":{"logGroup":"/nfw/riv-lab/flow"}}]}'
aws network-firewall update-logging-configuration --firewall-name $FW_NAME --logging-configuration \
  '{"LogDestinationConfigs":[{"LogType":"FLOW","LogDestinationType":"CloudWatchLogs","LogDestination":{"logGroup":"/nfw/riv-lab/flow"}},{"LogType":"ALERT","LogDestinationType":"CloudWatchLogs","LogDestination":{"logGroup":"/nfw/riv-lab/alert"}}]}'
aws network-firewall describe-logging-configuration --firewall-name $FW_NAME \
  --query 'LoggingConfiguration.LogDestinationConfigs[].LogType' --output table
```
Expected: `FLOW` and `ALERT`.

**Step 2 — DNS Firewall anchor rule group and VPC association.**
```bash
RG_ID=$(aws route53resolver create-firewall-rule-group --name riv-lab-anchor \
  --creator-request-id riv-$(date +%s) --query FirewallRuleGroup.Id --output text)
aws route53resolver associate-firewall-rule-group --firewall-rule-group-id $RG_ID --vpc-id $VPC_ID \
  --priority 101 --name riv-lab-assoc --creator-request-id riva-$(date +%s)
aws route53resolver list-firewall-rule-group-associations --firewall-rule-group-id $RG_ID \
  --query 'FirewallRuleGroupAssociations[].{vpc:VpcId,status:Status,managedBy:ManagedOwnerName}' --output table
```
Expected: `rslvr-frg-…` and the association in `COMPLETE`.

**Step 3 — Stateful rule group with SNI anti-spoofing.**
```bash
cat > /tmp/riv-rules.suricata <<'EOF'
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; content:"example.com"; nocase; startswith; endswith; aws_route_53_resolved_domain:"example.com"; flow:to_server, established; sid:1;)
drop tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; content:"example.com"; nocase; startswith; endswith; flow:to_server, established; sid:2;)
EOF
RULES_ARN=$(aws network-firewall create-rule-group --rule-group-name riv-lab-rules --type STATEFUL \
  --capacity 50 --rules file:///tmp/riv-rules.suricata --query RuleGroupResponse.RuleGroupArn --output text)
```

**Step 4 — Enable validation in `ALERT`.** ⚠️ This step may restart the stateful engine.
```bash
aws network-firewall describe-firewall-policy --firewall-policy-name $POLICY \
  --query '{t:UpdateToken,p:FirewallPolicy}' > /tmp/pol.json
jq --arg rg "$RG_ID" --arg arn "$RULES_ARN" '.p
  | .Route53ResolvedIPValidation = {"Action":"ALERT","DNSFirewallRuleGroupId":$rg,"DNSExpiryExceptionPolicy":"CONTINUE"}
  | .StatefulRuleGroupReferences = ((.StatefulRuleGroupReferences // []) + [{"ResourceArn":$arn}])' \
  /tmp/pol.json > /tmp/newpol.json
aws network-firewall update-firewall-policy --firewall-policy-name $POLICY \
  --update-token $(jq -r .t /tmp/pol.json) --firewall-policy file:///tmp/newpol.json
```
Expected: `FirewallPolicyStatus` reaches `ACTIVE`.

**Step 5 — Break: trigger the symptom from the instance.**
```bash
export SPOOF_IP=$(dig +short example.org | head -1)   # an IP the VPC Resolver didn't return for example.com
cat > /tmp/riv-test.json <<EOF
{"commands":[
 "dig +short example.com",
 "curl -s -o /dev/null -w 'legit=%{http_code}' --max-time 5 https://example.com; echo",
 "curl -s -o /dev/null -w 'spoof=%{http_code}' --max-time 5 --resolve example.com:443:$SPOOF_IP https://example.com; echo",
 "timeout 5 bash -c '</dev/tcp/$SPOOF_IP/443' && echo raw=OPEN || echo raw=BLOCKED"
]}
EOF
CMD=$(aws ssm send-command --instance-ids $INSTANCE --document-name AWS-RunShellScript \
  --parameters file:///tmp/riv-test.json --query Command.CommandId --output text)
sleep 15; aws ssm get-command-invocation --command-id $CMD --instance-id $INSTANCE \
  --query StandardOutputContent --output text
```
Expected with `ALERT`: `legit=200`. `spoof=000`, because rule sid:2 blocks the spoofed SNI. `raw=OPEN`, because the policy only alerts.

**Step 6 — Verify in the logs.** 📸 Checkpoint 1: the flow log entry tagged by validation.
```bash
aws logs filter-log-events --log-group-name /nfw/riv-lab/flow --filter-pattern "\"$SPOOF_IP\"" \
  --start-time $(( ($(date +%s)-900)*1000 )) --max-items 5 --query 'events[].message' --output text
screencapture -iW lab/evidence/01-alert-flow-log.png
```
Record the exact field name that marks the validation failure. The documentation doesn't give it.

**Step 7 — Fix: enforce with `DROP`** (no engine restart).
```bash
aws network-firewall describe-firewall-policy --firewall-policy-name $POLICY \
  --query '{t:UpdateToken,p:FirewallPolicy}' > /tmp/pol.json
jq '.p | .Route53ResolvedIPValidation.Action = "DROP"' /tmp/pol.json > /tmp/newpol.json
aws network-firewall update-firewall-policy --firewall-policy-name $POLICY \
  --update-token $(jq -r .t /tmp/pol.json) --firewall-policy file:///tmp/newpol.json
```

**Step 8 — Verify again.** 📸 Checkpoint 2: `raw=BLOCKED` together with the policy in `DROP`.
Re-run the step 5 `send-command`. Expected: `legit=200`, `spoof=000`, `raw=BLOCKED`.
```bash
screencapture -iW lab/evidence/02-enforced-drop.png
```

**Step 9 — Teardown (reverse order).** ⚠️ Run this in the lab account only. Confirm the account with `aws sts get-caller-identity` first.
```bash
aws network-firewall describe-firewall-policy --firewall-policy-name $POLICY \
  --query '{t:UpdateToken,p:FirewallPolicy}' > /tmp/pol.json
jq --arg arn "$RULES_ARN" '.p | del(.Route53ResolvedIPValidation)
  | .StatefulRuleGroupReferences |= map(select(.ResourceArn != $arn))' /tmp/pol.json > /tmp/newpol.json
aws network-firewall update-firewall-policy --firewall-policy-name $POLICY \
  --update-token $(jq -r .t /tmp/pol.json) --firewall-policy file:///tmp/newpol.json
aws network-firewall delete-rule-group --rule-group-arn $RULES_ARN
ASSOC=$(aws route53resolver list-firewall-rule-group-associations --firewall-rule-group-id $RG_ID \
  --query "FirewallRuleGroupAssociations[?VpcId=='$VPC_ID'].Id" --output text)
aws route53resolver disassociate-firewall-rule-group --firewall-rule-group-association-id $ASSOC
aws route53resolver delete-firewall-rule-group --firewall-rule-group-id $RG_ID
aws network-firewall update-logging-configuration --firewall-name $FW_NAME --logging-configuration \
  '{"LogDestinationConfigs":[{"LogType":"FLOW","LogDestinationType":"CloudWatchLogs","LogDestination":{"logGroup":"/nfw/riv-lab/flow"}}]}'
aws network-firewall update-logging-configuration --firewall-name $FW_NAME --logging-configuration '{"LogDestinationConfigs":[]}'
aws logs delete-log-group --log-group-name /nfw/riv-lab/flow
aws logs delete-log-group --log-group-name /nfw/riv-lab/alert
```
If `delete-firewall-rule-group` fails, list the associations again. The association that Network Firewall manages can take time to go away.

### Open questions to settle in the lab

- The exact parent key inside `FirewallPolicy`, and the flow log field that tags a validation failure.
- The evaluation order between the policy-level check and the stateful rule groups.
- CNAME chains: does the rule match the queried name, the CNAME target, or both?
- Cache duration beyond the TTL, multiple A records, and AAAA answers.
- Whether `SourceIPRanges` omitted covers TGW spoke CIDRs in the centralized model. The documentation names only the firewall VPC and "additional VPCs attached to the firewall".
- Whether answers served through a Resolver inbound endpoint (on-premises clients) count as resolved.

## Learning Exercises

1. **Empty list vs omitted field:** set `SourceIPRanges: []` and show that a hardcoded-IP connection passes even with `Action = DROP`.
2. **Bypass scope:** add the spoof IP to `BypassDestinationValidationIPRanges`. Show that the policy check skips it, but rule sid:2 still blocks the spoofed SNI.
3. **SSH by domain:** replace the TLS rules with the SFTP allowlist pattern. Test `nc -vz <partner-host> 22` against an IP-only connection.
4. **DoH bypass attempt:** resolve through a public DNS-over-HTTPS service from the instance and connect to the answer. Explain why validation fails, and what blocks the DoH request itself.
5. **Centralized model:** move the test instance into a TGW spoke and repeat with and without explicit `SourceIPRanges` to settle the open question.
