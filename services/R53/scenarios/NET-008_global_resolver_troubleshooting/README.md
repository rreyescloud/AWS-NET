# NET-008 — Route 53 Global Resolver Troubleshooting Lab

**Tier:** Case analysis — partially replicated, no deploy script
**Status:** 2 of 6 scenarios replicated (2026-06-25)

## Objective

Replicate and document the most common Global Resolver issues observed in real support cases, producing a re:Post community article with step-by-step troubleshooting guidance.

## Lab Environment

- **Account** — `<LAB_ACCOUNT_ID>`, Admin role
- **Region** — `us-east-2` (control plane)

## Infrastructure Used

- **Global Resolver** — `gr-<id>`, resolving at `<id>.route53globalresolver.global.on.aws`
- **Anycast IPs** — two addresses assigned by the service, used as the Do53 targets
- **DNS View** — `dnsv-<id>`, OPERATIONAL
- **Private Hosted Zone** — `Z<id>` for `lab.internal.`
- **Access Source** — `<YOUR_PUBLIC_IP>/32` over Do53

## Scenarios to Replicate

### Scenario 1: Corporate Firewall Intercepting Do53 (Most Common)
- Simulate Do53 query failing due to interception
- Prove DoH/DoT bypasses the interception
- Document dig vs kdig vs curl DoH outputs
- **Source case:** CASE-05 (Pharmaceutical)

### Scenario 2: Cross-Account PHZ Association Failure
- Attempt associate-hosted-zone from a different account
- Document GR-ERR03603 and GR-ERR03102 errors
- Show the workaround (same-account PHZ)
- **Source case:** CASE-09 (Data & Analytics SaaS)

### Scenario 3: PHZ Shadows Public Zone → NXDOMAIN
- Associate a PHZ with a common domain (e.g., example.com)
- Query a subdomain NOT in the PHZ
- Show unexpected NXDOMAIN instead of public resolution
- Document the fix

### Scenario 4: DoH/DoT TLS Error with Raw IP
- Attempt DoH query using anycast IP directly
- Show TLS handshake failure (no SNI)
- Fix using the dnsName

### Scenario 5: GR Does Not Forward to External Resolvers
- Query a domain that only on-prem DNS can resolve
- Show that GR returns NXDOMAIN (no forwarding)
- Document the GR vs VPC Resolver capability matrix

### Scenario 6: Unexpected Billing
- Document the pricing model
- Show how to identify and delete unused GRs
- **Source cases:** CASE-04, CASE-07, CASE-02

## Deliverables

1. **re:Post article** — "How do I troubleshoot common issues with Route 53 Global Resolver?"
2. **Lab notes** — step-by-step commands and outputs for each scenario
3. **Architecture diagrams** — drawio for each scenario
4. **Consolidated case notes** — patterns distilled from all source cases

## Source Cases

| Case | Industry | Issue |
|---------|----------|-------|
| CASE-09 | Data & Analytics SaaS | Cross-account PHZ → dns-view not supported |
| CASE-05 | Pharmaceutical | Corporate firewall intercepting Do53 |
| CASE-08 | Pharmaceutical | Delegation type / multi-cloud forwarding |
| CASE-01 | Hardware Manufacturer | Split DNS with GR, DoH requirement |
| CASE-04 | Individual | Accidental creation, $1,644 bill |
| CASE-07 | — | Credit request, POC unused |
| CASE-06 | — | Quota increase |

## Lab Results (2026-06-25)

### Scenario 1: Corporate Firewall Intercepting Do53 — REPLICATED ✅

**Setup:**
- Access sources configured for both `72.21.198.64/32` and `54.240.198.33/32`
- PHZ records: app/db/api.lab.internal → 10.0.x.x
- Testing from Amazon corporate network

**Key finding:** Amazon corporate network uses different egress IPs:
- `checkip.amazonaws.com` → `72.21.198.64` (intra-AWS routing)
- `ifconfig.me` → `54.240.198.33` (actual internet egress)
- GR sees the **internet egress IP**, not the intra-AWS one

**Results:**
```
Do53 (dig @<anycast-ip> app.lab.internal):
  → connection timed out; no servers could be reached
  → Corporate firewall blocks outbound port 53 to non-authorized DNS

DoH (HTTPS to GR dns-name):
  → app.lab.internal  NOERROR  1 answer  ✅ (Private PHZ)
  → db.lab.internal   NOERROR  1 answer  ✅ (Private PHZ)
  → api.lab.internal  NOERROR  1 answer  ✅ (Private PHZ)
  → google.com        NOERROR  1 answer  ✅ (Public DNS)
```

**Conclusion:** DoH bypasses corporate firewall because it uses port 443 (HTTPS) which is always allowed. This confirms the exact pattern from Pharmaceutical case CASE-05.

### Scenario 3: PHZ Shadows Public Zone — REPLICATED ✅

```
nonexistent.lab.internal → NXDOMAIN (PHZ "lab.internal" takes precedence, no fallback to public)
```

Since PHZ `lab.internal.` is associated to the DNS View, ALL queries for `*.lab.internal` go to the PHZ first. If the record doesn't exist in the PHZ, the GR returns NXDOMAIN — it does NOT fall back to public DNS resolution.

### Access Source Discovery — DOCUMENTED ✅

**REFUSED (RCODE=5)** when IP not in Access Source:
- Before adding `54.240.198.33/32`: all queries returned REFUSED
- After adding it: all queries resolved correctly

This confirms authentication is IP-based per-protocol. Important: your actual internet egress IP may differ from what `curl checkip.amazonaws.com` shows if you're behind a corporate proxy.

**Troubleshooting tip:** If getting REFUSED, check your actual egress IP with `curl -s https://ifconfig.me` (NOT checkip.amazonaws.com which uses intra-AWS routing).

## TODO

- [x] Update access source to current IP
- [x] Add A records to PHZ (app.lab.internal, db.lab.internal, api.lab.internal)
- [x] Replicate Scenario 1 (Do53 vs DoH) ✅
- [x] Replicate Scenario 3 (PHZ shadow) ✅
- [x] Document REFUSED behavior (access source mismatch) ✅
- [ ] Replicate Scenario 2 (cross-account — need second account)
- [ ] Replicate Scenario 4 (TLS error raw IP)
- [ ] Replicate Scenario 5 (no forwarding)
- [ ] Capture all outputs for article screenshots
- [ ] Draft re:Post article
- [ ] Peer review TT
- [ ] Publish
