# NET-011: Suricata Domain Allowlist Never Matches — Catch-All Drop Kills the TCP SYN

**Tier:** Lab — reproducible end to end
**Status:** Resolved — customer confirmed 2026-08-18 · Lab replicated
**Services:** Network Firewall · Suricata · TLS · Transit Gateway · VPC Routing · CloudWatch Logs

## Objective

Reproduce and document why a syntactically valid `pass tls ... tls.sni` allowlist rule in AWS Network Firewall never matches, when the real cause is a catch-all `drop ip any any -> any any` rule at the lowest strict-order priority that terminates the connection on the TCP SYN — before any TLS buffer exists.

Source case: `CASE-13`.


## Business Context — Regulated Financial Services

Client in the regulated financial services industry operating a landing-zone style multi-account
organization with centralized egress inspection. Compliance requires that every denied outbound
connection produce a logged event, which is why the customer had an explicit catch-all drop rule
rather than relying on the policy's stateful default action.

Architecture goals:
- **Centralized egress inspection** — all spoke VPC internet-bound traffic hairpins through a shared inspection VPC via Transit Gateway
- **Domain allowlist** — only `*.amazonaws.com` and a short list of vendor FQDNs may leave the network
- **Auditable denies** — every drop must appear in the ALERT log with a rule identifier


## Problem Statement

Outbound HTTPS from the spoke workloads to AWS service endpoints (`iam.amazonaws.com`,
`sts.amazonaws.com`, and similar) timed out. The customer's allowlist rule looked correct and had
been reviewed twice:

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FNetworkFirewall%2Fscenarios%2FNET-011_suricata_domain_allowlist_syn_drop%2Farchitecture.drawio)

```
pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; content:".amazonaws.com"; endswith; msg:"Allow AWS service endpoints"; flow:to_server,established; sid:20000; rev:1;)
```

The ALERT log showed only the customer's own default-deny rule firing, with no `tls` object and no
`app_proto` field on the event. The customer read that as "the firewall is not evaluating my pass
rule" and asked whether `dotprefix` was supported in their engine version.


## Root Cause

The last rule group in the STRICT_ORDER policy, at priority 999, contained:

```
drop ip any any -> any any (sid:30000;msg:"Default drop"; flow:to_server;)
```

Two independent defects compound here:

**1. `flow:to_server` without `established` matches the SYN.**
`flow:to_server` alone is true for *every* packet travelling toward the server, including the very
first TCP SYN. At SYN time the flow has no application layer — Suricata has parsed only the L3/L4
headers. `tls.sni`, `http.host` and `app_proto` do not exist yet. `tls.sni` is populated only when
the TLS Client Hello arrives, which is the first data packet *after* the three-way handshake
completes.

So the evaluation order for a `curl https://iam.amazonaws.com` was:

- **SYN arrives.** Priority 10 and 99 groups contain only `tls` rules — no match, because there is no
  TLS state. Priority 999 `drop ip any any` matches on L3 alone and is terminating. Connection dies.
- The handshake never completes, so the Client Hello is never sent, so `tls.sni` is never populated,
  so the `pass` rule at priority 99 is structurally unreachable.

**2. An explicit catch-all drop voids the stateful default action.**
A stateful default action applies only to traffic that matched **no** rule. Adding an explicit
catch-all `drop` means traffic always matches something, so `aws:drop_established` — which is
correctly scoped to established flows — never gets a chance to run.

### Reading the log for what is absent

The diagnostic signal was in the *missing* fields, not the present ones. The alert event carried
`proto: TCP`, `dest_port: 443`, and no `tls` object, no `app_proto`, no `tls.sni`. That combination
means the drop happened at connection setup, at packet level. Had the drop occurred after the Client
Hello, the event would have carried the SNI. Absence of the TLS metadata is what localizes the
failure to the SYN.


## Secondary Findings

Found while reviewing the full policy; none of these were the outage cause, but each is a latent
defect worth reporting.

- **Round-1 rule had unsatisfiable anchors.** An earlier revision of the allowlist read
  `content:".amazonaws.com"; startswith; nocase; endswith;`. Applying both `startswith` and
  `endswith` to a single `content` turns it into an exact-match predicate: the whole SNI buffer must
  equal `.amazonaws.com`. No real hostname satisfies that. The fix is `dotprefix`, which normalizes
  the buffer for wildcard-domain matching.
- **`dotprefix` is a security fix, not only a correctness fix.** A bare `content:".amazonaws.com"; endswith;`
  without `dotprefix` also matches `evilamazonaws.com` in some buffer forms. `dotprefix` forces the
  label boundary.
- **`HOME_NET` default is the inspection VPC CIDR, not the spokes'.** In a centralized inspection
  design the firewall lives in the hub VPC, so the engine's default `HOME_NET` is the hub CIDR. Every
  `$HOME_NET` rule silently no-ops against spoke source addresses unless `HOME_NET` is overridden per
  rule group. The customer had overridden it to `10.0.0.0/8` — correct — but this is the single most
  common silent failure in this architecture.
- **`alert` is non-terminating in STRICT_ORDER.** Rule evaluation continues past an `alert`, so a
  managed threat-signature group placed at a higher priority than a `pass` does not stop the `pass`
  from pre-empting later drops. Ordering a managed group *after* an allowlist `pass` means the
  allowlist bypasses that group entirely.
- **`pkt_src: geneve encapsulation` carries no diagnostic signal.** It is the normal marker for
  traffic arriving via a GWLB endpoint and appears on every event in this topology.


## Recommended Fix

**Option 1 (preferred) — delete the catch-all group, use the default action.**

```
statefulDefaultActions = ["aws:drop_established", "aws:alert_established"]
```

`aws:drop_established` drops only packets in flows that completed a handshake, so it cannot kill the
SYN. `aws:alert_established` supplies the logged deny event that the compliance requirement needs,
which is the reason the explicit rule existed in the first place.

**Option 2 — keep the explicit rule, scope it correctly.**

```
drop ip any any -> any any (msg:"Default drop"; flow:to_server,established; sid:30000; rev:1;)
```

Adding `established` restricts the match to flows past the handshake. This works, but it still
pre-empts the default action, so any future change to `statefulDefaultActions` will have no effect —
a maintenance trap. Option 1 is the cleaner contract.


## Outcome — customer confirmed 2026-08-18

Customer adopted **Option 1** and reported the policy working as intended. Both secondary findings
were accepted:

- **Managed group priority.** They had placed `AttackInfrastructureStrictOrder` at priority 100,
  *below* their `*.amazonaws.com` pass group at 99, on the assumption that AWS would remediate
  security issues in its own services rather than list them as threat infrastructure. They had not
  considered customer-owned resources reachable through those same endpoints — the S3 case being the
  obvious one. Reordering accepted.
- **`*.amazonaws.com` over-grant.** Accepted that the rule permits any S3 bucket in any AWS account.
  Moving to a named-FQDN allowlist covering only the service endpoints without regional PrivateLink
  coverage, extended reactively as gaps appear. This in turn pushed Console Private Access onto their
  roadmap, since it reduces the FQDN set that has to stay in the allowlist.

The customer's framing of the original design is worth recording verbatim as a diagnostic pattern:
they described `drop ip any any` as a "ham-fisted approach" to a compliance requirement that only
asked for unauthorized access to be *logged*, with no constraint on mechanism. The requirement was
satisfiable by `aws:alert_established` alone. **The catch-all rule was never the requirement — it was
one implementation of it, and the outage was caused entirely by the implementation choice.** When a
customer defends a rule on compliance grounds, ask what the requirement literally says before
arguing about the rule.

Closing question was for Network Firewall training material beyond the docs site and Skill Builder.
Answered in `correspondence_02.md`; the single highest-value pointer is the community-maintained
[AWS Security Services Best Practices — Network Firewall guide](https://aws.github.io/aws-security-services-best-practices/guides/network-firewall/),
which independently covers strict-order-vs-action-order, `flow:to_server` semantics,
alert-before-pass logging, and a correctly engineered custom default-block ruleset — i.e. most of
this case, pre-written.


## Follow-up Leads

Two items surfaced by the resolution that are worth their own scenarios:

- **Alert-before-pass logging for allow-side compliance evidence.** Pass rules do not log. This
  customer has denies covered but not allows, and an audit will ask. The technique is a matching
  `alert` rule immediately before the pass rule under strict order, with cross-referenced SIDs.
- **Console Private Access as an egress-allowlist reduction lever.** Not usually framed this way —
  it is normally presented as a console security control. Worth documenting the supported
  Regions/consoles ceiling and quantifying how much of a `*.amazonaws.com` grant it can actually
  retire versus what must remain as named FQDNs.


## Lab

`lab/deploy_lab.py` builds a faithful hub-and-spoke replica: spoke VPC `10.212.0.0/16`, inspection
VPC `10.208.0.0/16`, Transit Gateway with appliance mode, two firewall endpoints, two NAT gateways,
and the policy deployed in the **broken** state.

See `lab/README.md` for the three demo phases and the exact alert-log evidence each one produces.

Cost is roughly $1.02/hour. Tear it down when done.


## Reusable Takeaways

- **The buffer lifecycle is the mental model.** Before you reason about any application-layer
  keyword, ask which packet in the flow the engine would have to be looking at for that buffer to
  exist. If the answer is "a packet that never gets sent", the rule is unreachable regardless of how
  correct its syntax is.
- **`flow:to_server` is not `flow:to_server,established`.** The first includes the SYN. In a `drop`
  rule that difference is the whole outage.
- **A stateful default action only fires when nothing matched.** An explicit catch-all silently
  disables it.
- **Demand the last rule group, not just the default action.** A customer describing their policy
  will say "my default action is drop_established" and mean the policy setting. Ask for the rule
  content of every group, ordered by priority, and read the highest-numbered one first.
- **Invalidate the customer's control test before trusting it.** The customer had "proven" the pass
  rule was ignored by watching it not fire. An alert-only `alert ip <spoke-cidr> any -> any any`
  probe distinguishes "packets are not arriving" from "packets arrive but the buffer does not exist" —
  and that distinction is the whole diagnosis.


## References

Public documentation used for the response:

- Suricata rule format and `flow` keyword — https://docs.suricata.io/en/latest/rules/flow-keywords.html
- Suricata TLS keywords, including `tls.sni` — https://docs.suricata.io/en/latest/rules/tls-keywords.html
- Suricata `dotprefix` transform — https://docs.suricata.io/en/latest/rules/transforms.html
- Network Firewall stateful rule groups — https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-ips.html
- Strict evaluation order — https://docs.aws.amazon.com/network-firewall/latest/developerguide/suricata-rule-evaluation-order.html
- Stateful default actions — https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-policy-settings.html
- Rule group variables and `HOME_NET` — https://docs.aws.amazon.com/network-firewall/latest/developerguide/rule-group-variables.html
- Domain filtering with Suricata rules — https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html
- Firewall logging fields — https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-logging.html
- Deployment models for AWS Network Firewall — https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/
- Appliance mode on Transit Gateway attachments — https://docs.aws.amazon.com/vpc/latest/tgw/transit-gateway-appliance-scenario.html
