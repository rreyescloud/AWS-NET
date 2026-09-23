# NET-015 — Hybrid DNS Resolution: Cross-Region Resolver Forwarding Chain

**Tier:** Lab — reproducible end to end
**Status:** Scaffold, ready to deploy

## Business Context

An enterprise in the insurance industry runs a hybrid architecture: workloads across two AWS
regions plus an on-premises Active Directory domain. The DR region has no direct connectivity to
the corporate data center — all DNS resolution for the on-prem domain must transit through the
production region's resolver infrastructure.

This is the single most common multi-region DNS architecture seen in enterprise support cases,
and it produces the single most commonly misdiagnosed failure: **SERVFAIL that looks like a
routing problem but is actually a DNS forwarding chain problem.**

## What This Lab Teaches

This lab is designed as SME preparation. Every section includes the *why*, not just the *how*.

**Core concepts exercised:**

- Resolver inbound and outbound endpoints — what each one does and why they exist as separate
  resources
- Forwarding rules, system rules and autodefined rules — the precedence model that determines
  which rule wins when multiple match
- Cross-region PHZ association — how a Private Hosted Zone becomes resolvable from a VPC in
  another region without any forwarding
- The forwarding chain — VPC-B outbound → VPC-A inbound → VPC-A outbound → on-prem DNS — and
  why each hop matters
- Query logging correlation — tracing a single query across two VPCs by timestamp and query ID
- Security group debugging for DNS — the TCP requirement that most people miss

**Failure modes you will reproduce deliberately:**

1. SG allows UDP 53 but not TCP 53 — works for small responses, fails for large ones (DNSSEC,
   long TXT records)
2. Forwarding rule targets an unreachable IP — SERVFAIL with no useful error
3. PHZ associated with VPC-A but not VPC-B — query returns NXDOMAIN from VPC-B even though it
   resolves from VPC-A
4. Forwarding rule for a subdomain conflicts with a system rule — the forwarding rule silently
   loses
5. Cross-region peering route missing — outbound endpoint cannot reach inbound endpoint, produces
   SERVFAIL

## Architecture

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2Fscenarios%2FNET-015_hybrid_resolver_cross_region%2Farchitecture.drawio)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   "On-Premises" (VPC, us-east-1, 192.168.0.0/16)                               │
│                                                                                  │
│     EC2 running dnsmasq                                                          │
│     Serves: corp.example.com zone                                                │
│       dc1.corp.example.com    → 192.168.1.10                                     │
│       dc2.corp.example.com    → 192.168.1.11                                     │
│       filesvr.corp.example.com → 192.168.10.50                                   │
│       vpn.corp.example.com    → 192.168.20.1                                     │
│       _ldap._tcp.corp.example.com → SRV record                                  │
│                                                                                  │
│     192.168.1.100 ◄── this is the IP the forwarding rule targets                │
│                                                                                  │
└──────────────┬───────────────────────────────────────────────────────────────────┘
               │ VPC peering (simulates Direct Connect)
               │
┌──────────────▼───────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   VPC-A  "Production" (us-east-1, 10.0.0.0/16)                                 │
│                                                                                  │
│     Inbound endpoint  → 10.0.1.10, 10.0.2.10                                   │
│       Receives queries from VPC-B and from on-prem                               │
│                                                                                  │
│     Outbound endpoint → 10.0.1.20, 10.0.2.20                                   │
│       Sends queries to on-prem DNS (192.168.1.100)                               │
│                                                                                  │
│     Forwarding rule: corp.example.com → 192.168.1.100                           │
│     PHZ: prod.internal (associated with VPC-A and VPC-B)                        │
│       app.prod.internal  → 10.0.10.50                                            │
│       db.prod.internal   → 10.0.20.100                                           │
│       cache.prod.internal → 10.0.10.51                                           │
│                                                                                  │
│     Query logging: enabled → CloudWatch Logs                                     │
│     EC2 test instance: 10.0.10.x                                                │
│                                                                                  │
└──────────────┬───────────────────────────────────────────────────────────────────┘
               │ Cross-region VPC peering
               │
┌──────────────▼───────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   VPC-B  "DR" (us-west-2, 10.1.0.0/16)                                         │
│                                                                                  │
│     Outbound endpoint → 10.1.1.20, 10.1.2.20                                   │
│       Sends queries to VPC-A inbound (10.0.1.10, 10.0.2.10)                    │
│                                                                                  │
│     Forwarding rule: corp.example.com → 10.0.1.10, 10.0.2.10                   │
│       (targets VPC-A's inbound endpoint, not the on-prem DNS directly)          │
│                                                                                  │
│     PHZ: dr.internal (local)                                                     │
│       dr-app.dr.internal → 10.1.10.50                                            │
│                                                                                  │
│     PHZ: prod.internal (cross-region association from VPC-A)                    │
│       resolves directly from VPC-B's resolver — no forwarding needed            │
│                                                                                  │
│     Query logging: enabled → CloudWatch Logs                                     │
│     EC2 test instance: 10.1.10.x                                                │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## The DNS Resolution Chain — Step by Step

When EC2 in VPC-B queries `filesvr.corp.example.com`, this is what happens at the packet level:

```
Step 1: EC2 in VPC-B sends DNS query to VPC resolver (10.1.0.2)

Step 2: VPC-B resolver evaluates rules in this order:
        a. Autodefined system rules (VPC internal names, amazonaws.com)  → no match
        b. Forwarding rules                                               → MATCH
           corp.example.com → forward to 10.0.1.10, 10.0.2.10

Step 3: VPC-B outbound endpoint sends the query to VPC-A inbound endpoint
        Source: 10.1.1.20 (outbound ENI)
        Dest:   10.0.1.10 (inbound ENI)
        Crosses the cross-region VPC peering

Step 4: VPC-A inbound endpoint receives the query and hands it to the VPC-A resolver

Step 5: VPC-A resolver evaluates its own rules:
        a. Autodefined system rules → no match
        b. Forwarding rules         → MATCH
           corp.example.com → forward to 192.168.1.100

Step 6: VPC-A outbound endpoint sends the query to the on-prem DNS
        Source: 10.0.1.20 (outbound ENI)
        Dest:   192.168.1.100 (dnsmasq)
        Crosses the intra-region VPC peering (simulating DX)

Step 7: dnsmasq resolves filesvr.corp.example.com → 192.168.10.50

Step 8: Response flows back: on-prem → VPC-A outbound → VPC-A resolver
        → VPC-A inbound → VPC-B outbound → VPC-B resolver → EC2

Total hops where DNS processing occurs: 4
Total network hops: 6
Expected additional latency vs direct resolution: 5–15ms per forwarding hop
```

**Why the inbound endpoint exists as a separate resource:**

The inbound endpoint is a set of ENIs with IP addresses in your VPC. External DNS clients
(other VPCs, on-prem servers) send queries *to* those IPs. The VPC resolver then evaluates
the query as if it originated inside the VPC — including checking forwarding rules, PHZ
associations and system rules.

Without the inbound endpoint, there is no IP address that an external client can target.
The VPC resolver at x.x.x.2 is only reachable from inside the VPC.

**Why the outbound endpoint exists as a separate resource:**

The outbound endpoint is a set of ENIs that the VPC resolver uses to *send* queries when a
forwarding rule matches. The query leaves from those ENIs, which means:
- The ENIs need security groups allowing outbound DNS
- The target IP must be reachable from the ENIs' subnets
- The ENIs are what appear as the source in the target server's logs

Without the outbound endpoint, forwarding rules cannot execute. You can create a forwarding
rule without an outbound endpoint, but it will fail at association time.

## Rule Precedence — The Part That Produces the Most Tickets

When the VPC resolver receives a query, it evaluates rules in this fixed order:

1. **Autodefined system rules** (highest priority)
   - `<vpc-id>.ec2.internal` and reverse DNS for the VPC CIDR
   - `<region>.compute.internal`
   - `amazonaws.com` (service endpoints)
   - These exist automatically and **cannot be deleted or overridden by forwarding rules**

2. **Forwarding rules you create** (or that are shared to you via RAM)
   - Evaluated by most-specific match: `sub.corp.example.com` beats `corp.example.com`
   - If two rules have the same specificity, the one owned by this account wins over a
     shared one

3. **Private Hosted Zones** associated with the VPC
   - Only evaluated if no forwarding rule matched
   - If a PHZ is authoritative for the suffix, it answers — no fallback to public DNS

4. **Recursive resolution** (public DNS)
   - Only reached if nothing above matched

**The gotcha that matters:**

If you create a forwarding rule for `amazonaws.com` → your on-prem DNS (thinking you want
to intercept service endpoint resolution), the **autodefined system rule wins** for the
local region's endpoints. Your forwarding rule only fires for endpoints in *other* regions
or for `amazonaws.com` subdomains that don't have a system rule. This is not a bug, but it
surprises everyone the first time.

## Security Groups for DNS — The TCP Requirement

DNS uses **both UDP and TCP on port 53**. Most people only open UDP.

This works until it doesn't:
- Responses larger than 512 bytes (the original UDP limit) trigger TCP fallback
- DNSSEC responses are almost always larger than 512 bytes
- SRV records and TXT records with long values can exceed 512 bytes
- EDNS0 raises the UDP limit to ~4096 bytes, but some firewalls strip EDNS0 options

**In this lab, the security groups deliberately start with UDP-only.** The basic A record
queries will work. The SRV record query for `_ldap._tcp.corp.example.com` will fail until
you add TCP. This is Exercise 3.

## Exercises

The deploy script creates the full infrastructure. The exercises are troubleshooting tasks
that start from a working state and break specific things.

### Exercise 1 — Trace a query through the chain

With everything working, query `filesvr.corp.example.com` from VPC-B. Then:

1. Find the query in VPC-B's query log (CloudWatch Logs Insights)
2. Find the same query arriving at VPC-A's query log
3. Measure the end-to-end resolution time
4. Identify which forwarding rule fired in each VPC

```
fields @timestamp, query_name, rcode, answers, srcaddr
| filter query_name like /corp.example.com/
| sort @timestamp asc
```

### Exercise 2 — Break and fix the cross-region peering route

Delete the route in VPC-B's route table that points `10.0.0.0/16` at the peering connection.
Then query `filesvr.corp.example.com` from VPC-B.

**Expected:** SERVFAIL — the outbound endpoint cannot reach the inbound endpoint.
**Diagnostic:** the query appears in VPC-B's log with `rcode: SERVFAIL` but does NOT appear
in VPC-A's log at all. This tells you the query never arrived.
**Fix:** restore the route.

### Exercise 3 — The TCP/UDP gotcha

Query `_ldap._tcp.corp.example.com` from VPC-B. It will fail (SERVFAIL or truncated
response). Check the security groups: they only allow UDP 53.

**Fix:** add TCP 53 to the SGs on all resolver endpoints and on the on-prem DNS server.
**Verify:** the SRV record resolves correctly.

### Exercise 4 — Forwarding rule vs PHZ precedence

Create a forwarding rule in VPC-B for `prod.internal` → some random IP (e.g., 10.99.99.99).
Then query `app.prod.internal` from VPC-B.

**Expected:** SERVFAIL — the forwarding rule takes precedence over the PHZ association, and
the target IP is unreachable.

This is the most counter-intuitive behavior: **a forwarding rule always wins over a PHZ.**
If both exist for the same domain, the PHZ is never consulted. The fix is to remove the
forwarding rule; you cannot "prefer" the PHZ.

### Exercise 5 — Simulate on-prem DNS failure

Stop the dnsmasq service on the on-prem EC2. Query `filesvr.corp.example.com` from VPC-B.

**Expected:** SERVFAIL — but this time the query appears in both VPC-B's AND VPC-A's logs.
VPC-A's log shows the forwarding rule fired but the target did not respond.

**Compare with Exercise 2:** both produce SERVFAIL, but the query logs tell you exactly
where the chain broke:
- Query in VPC-B only → network connectivity problem between VPC-B and VPC-A
- Query in both → the chain reached VPC-A, so the problem is between VPC-A and on-prem

This is the troubleshooting methodology: **correlate the query logs to locate the break.**

### Exercise 6 — System rule precedence

From VPC-A, create a forwarding rule for `amazonaws.com` → 192.168.1.100 (on-prem).
Then query `s3.us-east-1.amazonaws.com` from EC2 in VPC-A.

**Expected:** the query resolves normally (to the public S3 endpoint). The forwarding rule
did NOT fire because the autodefined system rule for `amazonaws.com` has higher priority.

Check the query log: the `firewallRuleAction` field will be empty (no forwarding rule
matched).

Now query `s3.eu-central-1.amazonaws.com` (a different region). This one WILL hit the
forwarding rule, because the system rule only covers the local region's endpoints.

### Exercise 7 — RAM sharing a forwarding rule

Share VPC-A's `corp.example.com` forwarding rule to VPC-B's account (or to the organization)
via AWS RAM. Then delete VPC-B's local forwarding rule.

**Expected:** VPC-B still resolves `corp.example.com` — the shared rule takes over.

Now recreate VPC-B's local forwarding rule pointing to a *different* target. Query again.

**Expected:** the local rule wins over the shared rule. This is the "local account wins"
precedence. Delete the local rule to restore the shared behavior.

## Lab Resources

- **3 VPCs** — on-prem (us-east-1), production (us-east-1), DR (us-west-2)
- **2 VPC peerings** — on-prem↔production (intra-region), production↔DR (cross-region)
- **3 resolver endpoints** — production inbound, production outbound, DR outbound
- **2 forwarding rules** — corp.example.com in each VPC
- **2 Private Hosted Zones** — prod.internal, dr.internal
- **1 cross-region PHZ association** — prod.internal → VPC-B
- **2 query logging configs** — one per VPC, to CloudWatch Logs
- **3 EC2 instances** — on-prem DNS, production test, DR test
- **Security groups** — deliberately UDP-only initially (Exercise 3)

**Estimated cost:** ~$1.00/hour (resolver endpoints dominate). Tear down the same day.

## Files

- `README.md` — this document (study guide + exercises)
- `architecture.drawio` — the three-VPC topology and the forwarding chain
- `lab/deploy_lab.py` — `deploy | status | test | teardown`
- `lab/dnsmasq.conf` — the on-prem DNS configuration (corp.example.com zone)

## Concepts to Know for SME (study checklist)

- [ ] Explain the difference between an inbound and an outbound resolver endpoint, including
      why they are separate resources and what each one's ENIs are used for
- [ ] State the rule evaluation order from memory: system rules → forwarding rules → PHZ → recursive
- [ ] Explain why a forwarding rule for `amazonaws.com` does not intercept local-region endpoints
- [ ] Describe what happens when a forwarding rule and a PHZ exist for the same domain
- [ ] Explain why DNS needs both TCP and UDP, and what triggers TCP fallback
- [ ] Given a SERVFAIL, describe how to use query logs from two VPCs to determine whether the
      break is between the client VPC and the intermediate VPC, or between the intermediate VPC
      and the final target
- [ ] Explain what RAM sharing of a forwarding rule does and how precedence works when both a
      local and a shared rule exist for the same domain
- [ ] Describe the cross-region PHZ association model — what it requires and what it does NOT
      require (no forwarding rule needed; the VPC resolver resolves directly from the PHZ)
- [ ] Explain the VPC resolver TTL cap (300 seconds minimum) and how it affects DNS change
      propagation in a forwarding chain
- [ ] State the maximum number of IP addresses a forwarding rule can target (6), and the maximum
      number of forwarding rules per VPC/region (currently 1000 via rules + associations)

## References

- [Resolving DNS queries between VPCs and your network](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver.html)
- [Forwarding outbound DNS queries to your network](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-outbound-queries.html)
- [Forwarding inbound DNS queries to your VPCs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries.html)
- [Resolver query logging](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html)
- [Sharing forwarding rules with other accounts](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-rules-managing.html#resolver-rules-managing-sharing)
- [Associating a PHZ with a VPC in a different account](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zone-private-associate-vpcs-different-accounts.html)
- [How DNS traffic is routed for your VPC](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-dns.html#vpc-dns-resolving)
- [VPC DNS resolver (AmazonProvidedDNS)](https://docs.aws.amazon.com/vpc/latest/userguide/AmazonDNS-concepts.html)
