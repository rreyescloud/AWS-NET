# NET-008 — Route 53 Global Resolver Troubleshooting Lab

**Tier:** Case analysis — partially replicated, no deploy script
**Status:** 2 of 6 scenarios replicated (2026-06-25)

## Objective

Replicate and document the most common Global Resolver failure modes, producing step-by-step
troubleshooting guidance that stands on its own without access to any particular account.

Global Resolver is a managed public DNS resolver endpoint: you get anycast IPs and a DNS name,
you attach DNS Views (which carry Private Hosted Zone associations), and you authorise callers by
source IP per protocol. Most of the confusion in practice comes from three properties that differ
from the familiar in-VPC `.2` resolver — it is reachable from outside AWS, it authenticates by
source IP, and it does not forward to anything you own.

## Lab Environment

- **Account** — lab account, `<LAB_ACCOUNT_ID>`
- **Region** — `us-east-2` (control plane)

## Infrastructure Used

- **Global Resolver** — `gr-<id>`, resolving at `<id>.route53globalresolver.global.on.aws`
- **Anycast IPs** — two addresses assigned by the service, used as the Do53 targets
- **DNS View** — `dnsv-<id>`, OPERATIONAL
- **Private Hosted Zone** — `Z<id>` for `lab.internal.`
- **Access Source** — `<YOUR_PUBLIC_IP>/32` over Do53

## Scenarios to Replicate

### Scenario 1 — A network device intercepting or blocking Do53 (most common)

- Reproduce a Do53 query failing because port 53 egress is not permitted to an arbitrary resolver
- Prove DoH/DoT succeeds over the same path
- Document the `dig` vs `kdig` vs `curl` DoH outputs side by side

### Scenario 2 — Cross-account PHZ association failure

- Attempt `associate-hosted-zone` against a PHZ owned by a different account
- Document the error codes returned
- Show the supported alternative (PHZ in the same account as the DNS View)

### Scenario 3 — A PHZ shadows the public zone and produces NXDOMAIN

- Associate a PHZ for a domain that also resolves publicly
- Query a name that does not exist in the PHZ
- Show NXDOMAIN instead of public resolution, and explain the fix

### Scenario 4 — DoH/DoT TLS failure when using the raw anycast IP

- Attempt a DoH query straight to an anycast IP
- Show the TLS handshake failing because there is no SNI/hostname to validate
- Fix by using the resolver's DNS name

### Scenario 5 — Global Resolver does not forward to external resolvers

- Query a name only an on-premises or third-party resolver can answer
- Show NXDOMAIN — there is no outbound forwarding rule concept here
- Contrast with VPC Resolver outbound endpoints and forwarding rules

### Scenario 6 — Unexpected cost

- Document the pricing model (the endpoint is billed while it exists, not only when queried)
- Show how to find and delete resolvers left behind from a proof of concept

## Case Patterns Behind These Scenarios

Drawn from support cases across several industries; no customer-identifying detail included.

- **Data & analytics SaaS** — cross-account PHZ association rejected by the DNS View
- **Pharmaceutical** — outbound Do53 blocked by a network control; DoH was the workaround
- **Pharmaceutical** — delegation model and multi-cloud forwarding expectations
- **Hardware manufacturing** — split DNS with Global Resolver plus a hard DoH requirement
- **Individual account** — resolver created accidentally and left running, four-figure monthly bill
- **Two further cases** — credit request for an unused proof of concept, and a quota increase

The distribution is informative on its own: half of these are not resolution failures at all,
they are lifecycle and billing surprises caused by a resource that costs money while idle.

## Lab Results (2026-06-25)

### Scenario 1 — Do53 blocked, DoH succeeds — REPLICATED

**Setup**

- Access sources configured for the two source prefixes observed during testing
- PHZ records: `app` / `db` / `api.lab.internal` → RFC 1918 addresses
- Queries issued from a network whose egress path filters outbound DNS

**Results**

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2Fscenarios%2FNET-008_global_resolver_troubleshooting%2Farchitecture.drawio)

```
Do53  (dig @<anycast-ip> app.lab.internal)
  → connection timed out; no servers could be reached
  → outbound port 53 to a non-approved resolver is not permitted on this path

DoH   (HTTPS to the Global Resolver DNS name)
  → app.lab.internal   NOERROR   1 answer      (from the PHZ)
  → db.lab.internal    NOERROR   1 answer      (from the PHZ)
  → api.lab.internal   NOERROR   1 answer      (from the PHZ)
  → example.com        NOERROR   1 answer      (public resolution)
```

**Conclusion**

DoH survives because it is indistinguishable from ordinary HTTPS on port 443. This is the whole
reason DoH exists as an option on this service, and it is the first thing to try when Do53 times
out from a managed corporate network.

### Scenario 3 — PHZ shadows the public zone — REPLICATED

```
nonexistent.lab.internal → NXDOMAIN
```

Once a PHZ for `lab.internal.` is associated with the DNS View, **every** query under that suffix
is answered from the PHZ. A name absent from the PHZ returns NXDOMAIN; there is no fallback to
public DNS. This is the same authoritative-zone precedence rule as the in-VPC resolver, but it
surprises people more here because the resolver is reachable from the public internet and feels
like a public resolver.

### Access Source behaviour — DOCUMENTED

Queries from an address not listed as an Access Source return **REFUSED (RCODE=5)** — not a
timeout, not SERVFAIL. Adding the address resolved it immediately. Authorisation is evaluated
per protocol, so an address allowed for DoH is not automatically allowed for Do53.

**Troubleshooting tip that cost real time:** the source address the resolver sees is your actual
internet egress address, which is not always what a given "what is my IP" endpoint reports. Some
of those endpoints are reached over a path that makes them report a different address than a
general internet destination would. Confirm against more than one, and prefer one with no special
relationship to AWS.

## Why REFUSED vs timeout vs NXDOMAIN matters

The three failures look similar in a ticket and have completely different causes:

- **Timeout** — the query never arrived. Path or protocol problem: filtered egress, wrong port,
  Do53 blocked. Nothing to fix on the resolver.
- **REFUSED** — the query arrived and was rejected. Access Source problem: your egress address is
  not authorised for that protocol.
- **NXDOMAIN** — the query arrived, was authorised, and was answered authoritatively. Zone data
  problem: a PHZ is authoritative for the suffix and the record does not exist in it.

Establishing which of the three you have narrows the investigation to one of three disjoint areas
before touching any configuration.

## TODO

- [x] Update access source to current egress address
- [x] Add A records to the PHZ (`app` / `db` / `api.lab.internal`)
- [x] Replicate Scenario 1 (Do53 vs DoH)
- [x] Replicate Scenario 3 (PHZ shadowing)
- [x] Document REFUSED behaviour (access source mismatch)
- [ ] Replicate Scenario 2 (cross-account — needs a second account)
- [ ] Replicate Scenario 4 (TLS failure on raw IP)
- [ ] Replicate Scenario 5 (no outbound forwarding)
- [ ] Capture outputs for each scenario
- [ ] Draft the public write-up

## References

- [Route 53 Resolver on the internet (Global Resolver)](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-global.html)
- [Private hosted zones](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zones-private.html)
- [Resolving DNS queries between VPCs and your network](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver.html)
- [DNS over HTTPS — RFC 8484](https://datatracker.ietf.org/doc/html/rfc8484)
