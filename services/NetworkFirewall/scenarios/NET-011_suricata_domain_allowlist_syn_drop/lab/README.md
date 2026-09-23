# NET-011 Lab — Centralized Inspection Replica

Reproduces the case `CASE-13` failure: a `pass tls ... tls.sni` allowlist rule that can never
match because a catch-all `drop ip any any -> any any (flow:to_server;)` at strict-order priority 999
terminates the connection on the TCP SYN.

## Topology

```
  spoke VPC 10.212.0.0/16                        inspection VPC 10.208.0.0/16
  +--------------------------+                   +--------------------------------------+
  |  wl-a 10.212.2.0/24      |                   |  tgw-a 10.208.11.0/24                |
  |    t3.micro test host    |                   |    0.0.0.0/0 -> NFW endpoint AZ-a    |
  |    ssm/ssmmessages/      |                   |                                      |
  |    ec2messages VPCEs     |     Transit       |  fw-a  10.208.1.0/24  (NFW endpoint) |
  |  wl-b 10.212.3.0/24      |==== Gateway ====> |    0.0.0.0/0    -> NAT GW AZ-a       |
  |                          |    appliance      |    10.212.0.0/16 -> TGW              |
  |  0.0.0.0/0 -> TGW        |      mode         |                                      |
  +--------------------------+                   |  pub-a 10.208.21.0/24  (NAT GW, EIP) |
                                                 |    0.0.0.0/0    -> IGW               |
                                                 |    10.212.0.0/16 -> NFW endpoint     |
                                                 +--------------------------------------+
                                                        (same three subnets in AZ-b)
```

Two design points that matter for fidelity:

- **The firewall sits before the NAT gateways.** Traffic order is TGW subnet → firewall endpoint →
  NAT gateway → IGW. That way the stateful engine sees the spoke's private address `10.212.2.x` as
  the source, which is what the customer's alert log showed. Putting the NAT first would present the
  NAT's address and `$HOME_NET` matching would behave differently.
- **SSM reaches the host over PrivateLink, not through the firewall.** Three interface endpoints live
  in the spoke VPC, so `ssm start-session` keeps working even while the firewall is dropping every
  outbound flow. This also mirrors the customer's own design, which used PrivateLink for regional API
  access.

TGW route tables are explicit — default association and propagation are disabled. The spoke route
table has a static `0.0.0.0/0` to the inspection attachment; the inspection route table has a static
`10.212.0.0/16` back to the spoke. Appliance mode on the inspection attachment pins each flow to one
firewall endpoint in both directions.

## Policy as deployed (broken on purpose)

`STRICT_ORDER`, `statelessDefaultActions ["aws:forward_to_sfe"]`,
`statefulDefaultActions ["aws:drop_established"]`, no TLS inspection.

Rule groups by priority:

- **10 — `net011-010Probes`** (alert only). `HOME_NET` overridden to `10.0.0.0/8`.
  ```
  alert ip 10.212.0.0/16 any -> any any (msg:"PROBE ip any packet"; sid:10003; rev:1;)
  alert tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:"amazonaws"; nocase; msg:"PROBE sni homenet"; sid:10001; rev:1;)
  alert tls 10.212.0.0/16 any -> any any (tls.sni; content:"amazonaws"; nocase; msg:"PROBE sni hardcoded src"; sid:10002; rev:1;)
  ```
  `sid:10003` is the control. It fires on the SYN because it needs no application layer. The two
  `tls.sni` probes stay silent. That contrast is the diagnosis.

- **99 — `net011-099Amazonaws`.** `HOME_NET` overridden to `10.0.0.0/8`.
  ```
  alert tls $HOME_NET any -> $EXTERNAL_NET any (tls.sni; content:".amazonaws.com"; startswith; nocase; endswith; msg:"ROUND1 BUG impossible anchors"; flow:to_server,established; sid:20090; rev:1;)
  alert tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; content:".amazonaws.com"; endswith; msg:"MATCH amazonaws sni"; flow:to_server,established; sid:20091; rev:1;)
  pass  tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; content:".amazonaws.com"; endswith; msg:"Allow AWS service endpoints"; flow:to_server,established; sid:20000; rev:1;)
  ```
  `sid:20090` is the customer's round-1 rule, kept as an `alert` so it can be shown never firing even
  once TLS works. It sits *before* the `pass` because `pass` is terminating in strict order and would
  otherwise pre-empt it. `sid:20091` mirrors the corrected `pass` as an alert so a successful match is
  visible in the log.

- **100 — `AttackInfrastructureStrictOrder`** (AWS managed). Present to demonstrate that a `pass` at
  priority 99 bypasses it entirely.

- **999 — `net011-999DefaultDeny`.** Verbatim from the customer, including the missing `rev` and the
  tight spacing:
  ```
  drop ip any any -> any any (sid:30000;msg:"Default drop"; flow:to_server;)
  ```

ALERT and FLOW logging go to `/aws/network-firewall/net011/alert` and `.../flow`, retention 1 day.

## Running it

```bash
export AWS_PROFILE=lab   # credentials for your own lab account
python3 deploy_lab.py deploy
python3 deploy_lab.py status
python3 deploy_lab.py teardown
```

State is written to `resources.json`; `deploy` is resumable and re-reads that file.

Connect to the test host once the SSM agent registers (about two minutes after the instance is
running):

```bash
aws ssm start-session --target <instance-id> --profile lab338 --region us-east-1
```

## Demo phases

### Phase A — reproduce the failure

```bash
curl -v --max-time 15 https://iam.amazonaws.com
```

Expected: the connection hangs and times out at `Trying <ip>:443...`. It never reaches
`TLS handshake`.

Alert log evidence:

- `sid:10003` fires — `PROBE ip any packet`. Packets do reach the engine.
- `sid:30000` fires — `Default drop`, action `blocked`.
- Both events carry `proto: TCP`, `dest_port: 443`, **no `tls` object, no `app_proto`**.
- `sid:10001`, `sid:10002`, `sid:20090`, `sid:20091` are all silent. No SNI buffer was ever built.

That is the exact shape of the customer's log.

Query it:

```bash
aws logs filter-log-events --log-group-name /aws/network-firewall/net011/alert \
  --start-time $(python3 -c 'import time;print(int((time.time()-600)*1000))') \
  --profile lab338 --region us-east-1 --query 'events[].message' --output text
```

### Phase B — fix option 2, scope the catch-all to established flows

Replace the priority-999 rule group content with:

```
drop ip any any -> any any (msg:"Default drop"; flow:to_server,established; sid:30000; rev:1;)
```

```bash
aws network-firewall update-rule-group --rule-group-name net011-999DefaultDeny --type STATEFUL \
  --rule-group '{"RulesSource":{"RulesString":"drop ip any any -> any any (msg:\"Default drop\"; flow:to_server,established; sid:30000; rev:1;)\n"},"StatefulRuleOptions":{"RuleOrder":"STRICT_ORDER"}}' \
  --update-token <token> --profile lab338 --region us-east-1
```

Wait for `ConfigurationSyncStateSummary: IN_SYNC`, then re-run the curl.

Expected: `curl https://iam.amazonaws.com` now completes the handshake and returns an HTTP response.
`sid:20091` fires with the SNI present in the event. `sid:20090` stays silent — proof that the
round-1 anchors were unsatisfiable, independent of the SYN problem. A non-allowlisted domain
(`curl https://example.com`) is dropped by `sid:30000` *after* the handshake, so its alert event
carries the `tls` object.

### Phase C — fix option 1, delete the catch-all and use the default action

Remove the priority-999 group from the policy and set:

```
statefulDefaultActions = ["aws:drop_established", "aws:alert_established"]
```

Expected: allowlisted traffic passes; a non-allowlisted domain is dropped by the default action and
still produces a logged deny event, satisfying the compliance requirement that motivated the explicit
rule. This is the cleaner contract — no explicit rule pre-empting the default action.

## Cost

Roughly $1.02/hour: two firewall endpoints at $0.395/hr each, two NAT gateways, two TGW attachments,
three interface endpoints, one t3.micro. Run `teardown` as soon as the phases are captured.

Teardown order matters and the script handles it: delete any `VpcEndpointAssociation` before the
firewall (otherwise `DeleteFirewall` returns `InvalidOperationException ... still in use`), then the
firewall, then the policy, then the rule groups — and poll between the policy and the rule groups,
because policy deletion is asynchronous and the groups report "still in use" until it finishes.
