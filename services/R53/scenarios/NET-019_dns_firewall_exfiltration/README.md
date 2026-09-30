# NET-019 — Route 53 Resolver DNS Firewall: Blocking Exfiltration, Allow Lists and the Bypass

**Tier:** Lab — reproducible end to end
**Status:** Deployed, exercises in progress (results recorded as they're run)
**Services:** Route 53 Resolver DNS Firewall · DNS Firewall Advanced · Route 53 Resolver query logging · Private Hosted Zones · VPC security groups · EC2 · SSM · CloudWatch Logs

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2Fscenarios%2FNET-019_dns_firewall_exfiltration%2Farchitecture.drawio)

## Business Context — Security Operations / Regulated Workloads

A security team found DNS exfiltration attempts from a compromised workload: sensitive data
encoded into subdomain labels and sent as queries to attacker-controlled domains
(`<base32-chunk>.<base32-chunk>.s12.attacker.example`). Nothing leaves over HTTP, so proxies and
egress firewalls see nothing. The data rides inside DNS queries that the VPC resolver forwards to
the attacker's authoritative server.

Requirements:
- **Block** known-bad domains: a custom list plus AWS managed threat lists
- **Allow** business-critical domains explicitly, ahead of every block rule
- **Detect** tunneling and DGA patterns that no domain list can predict
- **Log** every allowed, blocked and alerted query for the SOC
- **Close the bypass**: a workload that talks to a public resolver directly never touches DNS Firewall

## How DNS Firewall Evaluates a Query

```
EC2 ──query──► VPC resolver (VPC+2 / 169.254.169.253)
                  │
                  ▼
        Rule groups associated with the VPC, lowest association priority first (101–9900)
                  │
                  ▼  inside each group, lowest rule priority first; the first matching rule acts
        ┌─────────────────────────────────────────────────────────────┐
        │ 100  ALLOW  allow-business   *.amazonaws.com, company.internal│  → resolve normally
        │ 200  BLOCK  block-custom     c2-lab.example (+ subdomains)    │  → NXDOMAIN / NODATA / OVERRIDE
        │ 300  BLOCK  AWS managed      Malware domain list              │
        │ 310  BLOCK  AWS managed      Botnet command-and-control       │
        │ 400  ALERT  Advanced         DNS_TUNNELING (confidence LOW)   │  → allowed + logged
        │ 410  ALERT  Advanced         DGA (confidence LOW)             │
        └─────────────────────────────────────────────────────────────┘
                  │ no match → allowed
                  ▼
        Query log (CloudWatch): query, rcode, firewall_rule_action, firewall_domain_list_id, ...
```

**Rules that matter:**
- **First match wins, by priority.** ALLOW has to sit *above* the blocks it's meant to exempt.
  Exercise 2 moves it below and watches the exemption disappear.
- **BLOCK responses:**
  - `NXDOMAIN`: "the name doesn't exist"
  - `NODATA`: "the name exists, there's no record of that type"
  - `OVERRIDE`: answers with a CNAME you choose, a walled garden or sinkhole that can host a
    warning page or a honeypot
- **Redirection chains (CNAME/DNAME):**
  - `TRUST_REDIRECTION_DOMAIN`, the default, trusts the rest of the chain once the first name is
    allowed.
  - `INSPECT_REDIRECTION_DOMAIN` checks every domain in the chain.

  An allowed internal name that CNAMEs to a malicious domain is exactly the gap Exercise 3 tests.
- **DNS Firewall Advanced** detects behavior, not names: `DNS_TUNNELING`, `DGA` and
  `DICTIONARY_DGA`, with a `ConfidenceThreshold` of `LOW`, `MEDIUM` or `HIGH`. There's no domain
  list and no "label length" knob.
- **Fail open vs fail closed** is set per VPC (`FirewallFailOpen`). The default, `DISABLED`, is
  fail-closed: if DNS Firewall can't evaluate a query, it's blocked. That's secure, but DNS
  Firewall becomes part of your availability path.
- **Scope:** DNS Firewall only sees queries that go through the VPC resolver. A client that sends
  UDP 53 to `8.8.8.8`, or uses DNS over HTTPS on 443, never reaches it (Exercise 5).

## Lab Architecture

- **1 VPC** (`10.60.0.0/16`), one public subnet, internet gateway
- **1 EC2** (t3.micro, AL2023) with a public IP, **no inbound rules**, reached through SSM Session Manager
- **PHZ `company.internal`**:
  - `app` → `10.60.1.50`
  - `sinkhole` → `10.60.1.99` (the walled-garden target)
  - `legit` → CNAME to `cdn.c2-lab.example` (allowed name redirecting to a blocked one)
- **Domain lists:** `NET-019-allow` and `NET-019-block`, plus two AWS managed lists
- **Rule group** `NET-019-rules` with the six rules above, associated with the VPC at priority 101
- **Query logging** to `/aws/route53/net019` (1-day retention)

The public IP is deliberate. It's what lets the workload reach public resolvers directly, which
is the bypass Exercise 5 has to close.

## Exercises

Run the queries from the EC2 (Session Manager), then read the query log. The deciding fields are
`firewall_rule_action` and `firewall_domain_list_id`.

### Exercise 1 — Block and the three block responses

```
dig app.company.internal        # allow-business
dig aws.amazon.com              # no list matches → allowed by default
dig evil.c2-lab.example         # block-custom
```

- Compare `evil.c2-lab.example` under `NXDOMAIN`, `NODATA` and `OVERRIDE` (CNAME to
  `sinkhole.company.internal`).
- **The question to answer:** when the blocked domain doesn't exist on the internet either, how
  do you prove the firewall blocked it and not the real DNS? The query log's
  `firewall_rule_action` is the only reliable signal.

### Exercise 2 — Priority: first match wins

Add `safe.c2-lab.example` to the allow list. With the allow rule at priority 100 it resolves
normally; move the allow rule to priority 300, below `block-custom` (200), and it gets blocked.

Also verify: after an **ALERT** match, does evaluation continue to lower-priority rules, or does
it stop like ALLOW and BLOCK?

### Exercise 3 — CNAME chasing

`legit.company.internal` is allowed, but it CNAMEs to `cdn.c2-lab.example`, which is blocked.
- With `TRUST_REDIRECTION_DOMAIN`: does the chain resolve through to the blocked domain?
- With `INSPECT_REDIRECTION_DOMAIN`: is it blocked at the CNAME target?

The attack pattern: an attacker who controls a CNAME target inside an allowed domain (a dangling
record, a takeover-able SaaS subdomain) turns your allow list into a tunnel.

### Exercise 4 — Exfiltration-style traffic and DNS Firewall Advanced

The script encodes random bytes into base32 labels and sends them as TXT queries to
`*.tunnel-lab.example`, a domain in **no** list, so only Advanced can catch it.
- Does `DNS_TUNNELING` fire, after how many queries, and at which confidence threshold?
- Does `DGA` fire on the same traffic?

Record the actual result: Advanced is behavioral, so the outcome is empirical.

### Exercise 5 — The bypass

```
dig @8.8.8.8 evil.c2-lab.example
```

- With the default SG egress (allow all), the query goes straight to Google DNS: **no firewall
  evaluation and no query log entry**. DNS Firewall never saw it.
- **Fix:** lock down SG egress to UDP/TCP 53 **only to the VPC resolver** (`10.60.0.2/32`), plus
  443 for SSM. The direct query now times out, and the VPC path keeps working.
- **What still gets out:** DNS over HTTPS rides on 443, which SSM needs. Closing that needs an
  egress proxy or Network Firewall with TLS SNI rules (see
  [NET-011](../../../NetworkFirewall/scenarios/NET-011_suricata_domain_allowlist_syn_drop/)),
  not DNS Firewall.

### Exercise 6 — Fail open vs fail closed

Read and flip `FirewallFailOpen` for the VPC. It can't be triggered on demand, so this one is a
design discussion: which workloads accept DNS going down along with DNS Firewall (fail-closed),
and which accept losing filtering during an outage (fail-open)?

## Query Log Insights Query

```
fields @timestamp, query_name, query_type, rcode, firewall_rule_action, firewall_domain_list_id, answers.0.Rdata
| filter ispresent(firewall_rule_action)
| sort @timestamp desc
| limit 50
```

## Cost

Roughly **$0.02/hour**: t3.micro + public IPv4 address. DNS Firewall charges per million queries
and per domain in custom lists, query logs go to CloudWatch, and the PHZ isn't billed if deleted
within 12 hours. Tear down the same day.

## Teardown

Order matters:
1. Disassociate the rule group from the VPC and **wait** until it's gone.
2. Delete the rules, then the rule group, then the custom domain lists.
3. Disassociate query logging, wait, delete the config and the log group.
4. Delete the PHZ records, then the zone.
5. Terminate the EC2, delete IAM, then the VPC. Watch for a `guardduty-data` endpoint added by
   GuardDuty Runtime Monitoring, which blocks VPC deletion.
6. **Verify** nothing is left: `list-firewall-rule-groups`, `list-firewall-domain-lists`
   (custom), `list-resolver-query-log-configs`, `list-hosted-zones`.

## Files

- `README.md` — this document
- `architecture.drawio` — evaluation order, the CNAME chain, and the bypass path
- `lab/deploy_lab.py` — deploy, exercise knobs and verified teardown (published after the run)

## References

- [Route 53 Resolver DNS Firewall](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall.html)
- [Rule groups and rules](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-rule-groups.html)
- [Rule actions and block responses](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-rule-actions.html)
- [AWS managed domain lists](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-managed-domain-lists.html)
- [DNS Firewall Advanced](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/firewall-advanced.html)
- [DNS Firewall VPC configuration (fail open)](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-vpc-configuration.html)
- [Resolver query logging](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html)
