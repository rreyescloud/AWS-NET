# NET-015 — Hybrid DNS Resolution: Cross-Region Resolver Forwarding Chain

**Tier:** Lab — reproducible end to end
**Status:** Exercises verified on a live lab (2026-09-30)
**Services:** Route 53 Resolver · VPC Peering (cross-region) · VPC Endpoints · EC2 · SSM · CloudWatch Logs · DHCP Option Sets

## Business Context

An enterprise in the insurance industry runs a hybrid architecture: workloads across two AWS
regions plus an on-premises Active Directory domain. The DR region has no direct connectivity to
the corporate data center, so all DNS resolution for the on-prem domain must transit through the
production region's resolver infrastructure.

This is the most common multi-region DNS architecture in enterprise support cases, and it
produces the most commonly misdiagnosed failure: **a timeout that looks like a routing problem
but is actually a DNS forwarding chain problem.**

## What This Lab Teaches

This lab is designed as SME preparation. Every section includes the *why*, not just the *how*.

**Core concepts exercised:**

- Resolver inbound and outbound endpoints: what each one does and why they're separate resources
- How the resolver picks between forwarding rules, PHZs (including the ones interface endpoints
  create) and system rules: the most specific domain wins
- Cross-region PHZ association: a PHZ resolvable from a VPC in another region without forwarding
- The forwarding chain (VPC-B outbound → VPC-A inbound → VPC-A outbound → on-prem DNS) and what
  each hop changes
- Query log correlation: locating the break by comparing `rcode` across two VPCs' logs
- DNS over TCP: when it's used, and what breaks when it's blocked
- Serve-stale: why the resolver keeps answering when the upstream DNS is down
- DHCP option sets: how the search domain changes *which* name is actually queried

**Failure modes you will reproduce (all verified):**

1. TCP 53 blocked: small answers keep working, large ones time out (Exercise 2)
2. A forwarding rule silently overriding a correct PHZ for the same domain (Exercise 3)
3. On-prem DNS down, with the resolver serving stale answers that hide the outage (Exercise 4)
4. A forwarding rule for `amazonaws.com` breaking AWS service endpoints in the local region (Exercise 5)
5. A wrong DHCP search domain turning short names into NXDOMAIN and leaking them to public DNS (Exercise 6)

## Architecture

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2Fscenarios%2FNET-015_hybrid_resolver_cross_region%2Farchitecture.drawio)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   "On-Premises" (VPC, us-east-1, 192.168.0.0/16)                                 │
│                                                                                  │
│     EC2 running dnsmasq                                                          │
│     Serves: corp.example.com zone                                                │
│       dc1.corp.example.com    → 192.168.1.10                                     │
│       dc2.corp.example.com    → 192.168.1.11                                     │
│       filesvr.corp.example.com → 192.168.10.50                                   │
│       vpn.corp.example.com    → 192.168.20.1                                     │
│       _ldap._tcp.corp.example.com → SRV (small, ~136 bytes)                      │
│       big.corp.example.com    → 25 TXT records (~6.6 KB, forces TCP)             │
│                                                                                  │
│     <onprem-dns-ip> ◄── the forwarding rule targets the instance's private IP    │
│       (DHCP-assigned at deploy; 192.168.1.121 in the 2026-09-30 run)             │
│                                                                                  │
└──────────────┬───────────────────────────────────────────────────────────────────┘
               │ VPC peering (simulates Direct Connect)
               │
┌──────────────▼───────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   VPC-A  "Production" (us-east-1, 10.0.0.0/16)                                   │
│                                                                                  │
│     Inbound endpoint  → 10.0.1.10, 10.0.2.10                                     │
│       Receives queries from VPC-B and from on-prem                               │
│                                                                                  │
│     Outbound endpoint → 10.0.1.20, 10.0.2.20                                     │
│       Sends queries to on-prem DNS (<onprem-dns-ip>)                             │
│                                                                                  │
│     Forwarding rule: corp.example.com → <onprem-dns-ip>                          │
│     PHZ: prod.internal (associated with VPC-A and VPC-B)                         │
│       app.prod.internal  → 10.0.10.50                                            │
│       db.prod.internal   → 10.0.20.100                                           │
│       cache.prod.internal → 10.0.10.51                                           │
│                                                                                  │
│     Query logging: enabled → CloudWatch Logs                                     │
│     EC2 test instance: 10.0.10.x                                                 │
│                                                                                  │
└──────────────┬───────────────────────────────────────────────────────────────────┘
               │ Cross-region VPC peering
               │
┌──────────────▼───────────────────────────────────────────────────────────────────┐
│                                                                                  │
│   VPC-B  "DR" (us-west-2, 10.1.0.0/16)                                           │
│                                                                                  │
│     Outbound endpoint → 10.1.1.20, 10.1.2.20                                     │
│       Sends queries to VPC-A inbound (10.0.1.10, 10.0.2.10)                      │
│                                                                                  │
│     Forwarding rule: corp.example.com → 10.0.1.10, 10.0.2.10                     │
│       (targets VPC-A's inbound endpoint, not the on-prem DNS directly)           │
│                                                                                  │
│     PHZ: dr.internal (local)                                                     │
│       dr-app.dr.internal → 10.1.10.50                                            │
│                                                                                  │
│     PHZ: prod.internal (cross-region association from VPC-A)                     │
│       resolves directly from VPC-B's resolver — no forwarding needed             │
│                                                                                  │
│     Query logging: enabled → CloudWatch Logs                                     │
│     EC2 test instance: 10.1.10.x                                                 │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## The DNS Resolution Chain — Step by Step

When EC2 in VPC-B queries `filesvr.corp.example.com`, this is what happens at the packet level:

```
Step 1: EC2 in VPC-B sends the DNS query to the VPC resolver (10.1.0.2)

Step 2: VPC-B resolver finds the most specific match for the name:
           forwarding rule corp.example.com → forward to 10.0.1.10, 10.0.2.10

Step 3: VPC-B outbound endpoint sends the query to VPC-A inbound endpoint
        Source: 10.1.1.20 (outbound ENI)
        Dest:   10.0.1.10 (inbound ENI)
        Crosses the cross-region VPC peering

Step 4: VPC-A inbound endpoint receives the query and hands it to the VPC-A resolver

Step 5: VPC-A resolver finds its own most specific match:
           forwarding rule corp.example.com → forward to <onprem-dns-ip>

Step 6: VPC-A outbound endpoint sends the query to the on-prem DNS
        Source: 10.0.1.20 (outbound ENI)
        Dest:   <onprem-dns-ip> (dnsmasq)
        Crosses the intra-region VPC peering (simulating DX)

Step 7: dnsmasq resolves filesvr.corp.example.com → 192.168.10.50

Step 8: Response flows back: on-prem → VPC-A outbound → VPC-A resolver
        → VPC-A inbound → VPC-B outbound → VPC-B resolver → EC2

Measured end to end: ~60 ms, almost all of it the us-west-2 ↔ us-east-1 round trip
```

**Why the inbound endpoint exists as a separate resource:**

The inbound endpoint is a set of ENIs with IP addresses in your VPC. External DNS clients
(other VPCs, on-prem servers) send queries *to* those IPs. The VPC resolver then evaluates
the query as if it originated inside the VPC, including forwarding rules, PHZ associations and
system rules.

Without the inbound endpoint, there is no IP address that an external client can target.
The VPC resolver at x.x.x.2 is only reachable from inside the VPC.

**Why the outbound endpoint exists as a separate resource:**

The outbound endpoint is a set of ENIs that the VPC resolver uses to *send* queries when a
forwarding rule matches. The query leaves from those ENIs, which means:
- The ENIs need security groups allowing outbound DNS
- The target IP must be reachable from the ENIs' subnets
- The ENIs are what appear as the source in the target server's logs, not the original client

Without the outbound endpoint, forwarding rules cannot execute.

## Rule Precedence — The Part That Produces the Most Tickets

When the VPC resolver receives a query, it picks the **most specific matching domain** across
everything that applies to the VPC:

- **Forwarding rules** you create, or that are shared to you via RAM, and are associated with the VPC
- **Private Hosted Zones** associated with the VPC, including the **AWS-managed PHZs created by
  interface endpoints with private DNS** (for example `ssm.us-east-1.amazonaws.com`, owned by
  `vpce.amazonaws.com`)
- **Autodefined system rules**: VPC-internal names (`<region>.compute.internal`, reverse DNS for
  the VPC CIDR) and the `internet-resolver` rule for everything else

Tie-breaks and consequences:
- `sub.corp.example.com` beats `corp.example.com`, whatever the source.
- For the **same** domain name, a forwarding rule wins over a PHZ (Exercise 3). The PHZ is never
  consulted, and you cannot "prefer" it.
- Recursive (public) resolution is only used when nothing more specific matched.

**The gotcha that matters (Exercise 5):** there is **no** system rule that protects
`amazonaws.com`. A forwarding rule for `amazonaws.com` → on-prem **captures every AWS service
endpoint, including the local region's** (`s3.us-east-1`, `sts.us-east-1`, `dynamodb.us-east-1`).
The only names that survive are the ones with a more specific interface-endpoint PHZ. If the
on-prem DNS can't resolve public names, S3, STS, KMS, DynamoDB, etc. break in that VPC.
Serve-stale (Exercise 4) can hide the breakage for names that were recently cached.

## Security Groups for DNS — The TCP Requirement

DNS uses **both UDP and TCP on port 53**. Most people only open UDP.

Everything starts over UDP. A server only switches to TCP when the answer doesn't fit: it sets
the **TC** (truncated) flag and the client retries over TCP. That happens with DNSSEC, big AD
sites with many SRV records, long TXT records for domain verification, or any answer above the
EDNS UDP size (4096 bytes on the Route 53 Resolver, 1232 on dnsmasq). **Size decides, not record
type:** the SRV record in this lab is 136 bytes and never needs TCP.

The deploy script creates the SGs in a working state (UDP + TCP 53, scoped to the VPC CIDRs).
Exercise 2 removes TCP to break it.

## Exercises

The deploy script creates the full infrastructure in a working state. Each exercise breaks one
thing, and you locate the break with `dig` and the query logs.

**Where to look:**
- **Query logs:** `/aws/route53/net015-dr` (us-west-2, VPC-B) and `/aws/route53/net015-prod`
  (us-east-1, VPC-A), in CloudWatch Logs Insights.
- **On-prem side:** `sudo tail -f /var/log/dnsmasq.log` on the on-prem instance. Queries are not
  in journald, which only shows service start/stop.

```
fields @timestamp, query_name, query_type, rcode, srcaddr, srcids.instance, srcids.resolver_endpoint
| filter query_name like /corp.example.com/
| sort @timestamp asc
```

### Exercise 1 — Trace a query through the chain

With everything working, query `filesvr.corp.example.com` from VPC-B, then find it in both query
logs and in the dnsmasq log. Before looking, predict: what `srcaddr` will VPC-A's log show?

**What you'll see:**
- `dig`: `NOERROR`, `192.168.10.50`, **~60 ms** end to end, **TTL 0**.
- VPC-B log: `srcaddr` = the EC2's IP, `srcids.instance` = the EC2, `srcids.resolver_endpoint` =
  VPC-B's outbound endpoint (so the `corp.example.com` rule fired).
- VPC-A log: `srcaddr` = **VPC-B's outbound ENI** (`10.1.1.20` / `10.1.2.20`), not the EC2.
  `srcids.resolver_endpoint` = VPC-A's inbound endpoint.
- dnsmasq log: `query[A] filesvr.corp.example.com from 10.0.x.20` (VPC-A's outbound ENIs).
- **One client query arrives twice downstream.** VPC-B logs 1 entry, VPC-A logs 2, dnsmasq
  receives 2, often one from each outbound ENI.

**Takeaways:**
- **The original client IP is lost at every hop.** To find which instance asked, correlate by
  name and timestamp with the first hop's log.
- **TTL 0 comes from the authoritative server.** dnsmasq serves its `address=` records with
  `local-ttl=0` by default, so no resolver caches the answer and every query walks the full chain.
  `local-ttl=300` on the authoritative server is the lever to cut forwarded volume.
- **Expect the on-prem DNS to see more queries than clients send** (duplication, retries).
- `192.168.10.50` is just a record; no host exists there. Resolving says nothing about reachability.

### Exercise 2 — The TCP/UDP gotcha

**Baseline** (from VPC-B):
```
dig SRV _ldap._tcp.corp.example.com     # 136 bytes, over UDP, ~60 ms
dig TXT big.corp.example.com            # "Truncated, retrying in TCP mode", 6599 bytes, (TCP), ~200 ms
```
The big TXT takes ~200 ms because **every hop** gets TC=1 and retries over TCP on its own
(dnsmasq → VPC-A, VPC-A → VPC-B, VPC-B → client). The cross-region hop pays the most.

**Break:** remove TCP 53 from the VPC-A resolver SG (shared by VPC-A's inbound and outbound
endpoints):
```
aws ec2 revoke-security-group-ingress --region us-east-1 --group-id <sg-resolver-a> \
  --ip-permissions 'IpProtocol=tcp,FromPort=53,ToPort=53,IpRanges=[{CidrIp=10.0.0.0/16},{CidrIp=10.1.0.0/16}]'
```

**What you'll see:**
- SRV keeps working, because it never needs TCP.
- Big TXT: client timeout. **VPC-B logs `TIMEOUT`, VPC-A logs `NOERROR`.** VPC-A resolved it, the
  UDP answer came back truncated, and the TCP retry to VPC-A's inbound endpoint was silently
  dropped.
- One big-TXT client query produced **6 entries** in VPC-A's log (TC, retries, both ENIs).
- Removing UDP too kills everything under `corp.example.com`, and VPC-A logs nothing.

Only the **inbound** endpoint's SG needs inbound rules. SGs are stateful, so replies to the
outbound endpoint come back without any inbound rule.

**Fix:** re-authorize TCP 53 (same command with `authorize-security-group-ingress`).

**Log correlation rule:**
- Only the first hop logs the query (`TIMEOUT`) → it never arrived: routing, SG or NACL on the way in.
- Next hop logs `NOERROR` and first hop logs `TIMEOUT` → it arrived and resolved, **the answer
  didn't make it back**: TCP/size problem between the hops.
- Next hop logs a fast `SERVFAIL` → it got past this hop and failed further down (Exercise 4).

An SG drops silently, with no RST or ICMP, so the client sees a timeout, not SERVFAIL.

**The trap in real cases:** everything "works" (A records resolve) until one large answer shows
up. That single name fails and the SG looks fine for "DNS".

### Exercise 3 — Forwarding rule vs PHZ for the same domain

In us-west-2, create a forwarding rule `prod.internal` → `10.99.99.99` on VPC-B's outbound
endpoint and associate it with VPC-B. Then from VPC-B:
```
dig app.prod.internal
dig dr-app.dr.internal
```

**What you'll see:**
- The association takes **~70 s** to reach `COMPLETE`. Nothing changes until then.
- `app.prod.internal` → client timeout. VPC-B log: `rcode: TIMEOUT`. Nothing in VPC-A's log: the
  query went to `10.99.99.99`, which has no route.
- `dr-app.dr.internal` unaffected: `NOERROR`, 0 ms, TTL 60, answered by the local PHZ.

**Takeaway:** the `prod.internal` PHZ is still associated and correct, but **never consulted**.
When "the PHZ is associated and it still doesn't resolve", list the rule associations for that
VPC: a rule for the same (or a more specific) domain always wins.

**Fix:** disassociate the rule. Once disassociated, the rule still exists but has no effect:
a rule only applies to VPCs it's associated with.

```bash
aws route53resolver list-resolver-rule-associations --filters Name=VPCId,Values=<vpc-b-id> \
  --query 'ResolverRuleAssociations[*].[Name,ResolverRuleId,Status]'
aws route53resolver list-resolver-rules --query 'ResolverRules[?RuleType==`FORWARD`].[Name,Id,DomainName]'
```

### Exercise 4 — On-prem DNS down (serve-stale)

On the on-prem instance: `sudo systemctl stop dnsmasq` (confirm with `ss -lunp | grep :53`, which
should show nothing). From VPC-B, query `filesvr.corp.example.com` several times, then a name
you've **never** queried, such as `vpn.corp.example.com`.

**What you'll see:**
- `filesvr` **keeps resolving**: `NOERROR`, `192.168.10.50`, with a **TTL fixed at 30** (not
  counting down) and 0–10 ms. It was still served **~8 minutes** into the outage.
- VPC-A keeps receiving the forwarded queries at the same timestamps and logs **`SERVFAIL`**.
- `vpn.corp.example.com` (never cached) → **timeout**.

This matches **serve-stale (RFC 8767)**: when upstream fails, the resolver answers with the last
known good record using a fixed 30 s TTL, and keeps retrying upstream in the background. This is
observed behavior, not yet confirmed in Route 53 docs.

**`SERVFAIL` here vs `TIMEOUT` in Exercise 2:**
- A stopped service answers with **ICMP port unreachable**, so VPC-A fails fast and logs `SERVFAIL`.
- An SG **drops silently**, so the first hop waits and logs `TIMEOUT`.
- VPC-A answered `SERVFAIL` quickly, but the client still saw a timeout for `vpn`: VPC-B keeps
  retrying (both inbound IPs) past `dig`'s timeout (5 s × 3). **The client symptom doesn't
  identify the failure; the logs do.**

**Case implications:**
- "On-prem DNS was down and AWS kept resolving": serve-stale.
- "We changed the record on-prem and AWS still returns the old IP": stale data while upstream is failing.
- Client-side monitoring can look healthy during an upstream outage. The failure is only visible
  in the next hop's query log.

**Fix:** `sudo systemctl start dnsmasq`, then confirm `vpn.corp.example.com` resolves (TTL 0, ~60 ms).

### Exercise 5 — Forwarding `amazonaws.com` to on-prem

**Baseline** from VPC-A: `s3.us-east-1`, `s3.eu-central-1` and `sts.us-east-1` resolve to public
IPs; `ssm.us-east-1.amazonaws.com` resolves to the interface endpoint's private IP.

In us-east-1, create a forwarding rule `amazonaws.com` → `<onprem-dns-ip>` on VPC-A's outbound
endpoint and associate it with VPC-A (~120 s). Watch `/var/log/dnsmasq.log`, then from VPC-A
query:
- `ssm.us-east-1.amazonaws.com` (has an interface endpoint in VPC-A)
- `s3.us-east-1.amazonaws.com`, `sts.us-east-1.amazonaws.com` (local region, no endpoint)
- `s3.eu-central-1.amazonaws.com` (other region)
- a name **never queried before**, e.g. `s3.ap-south-1.amazonaws.com`

**What you'll see:**
- dnsmasq receives the S3/STS queries **for both regions**, and answers each one with
  `config error is REFUSED (EDE: not ready)`. It has `no-resolv`, so it can't resolve public
  names. EDE is an RFC 8914 Extended DNS Error meaning "no upstream available".
- `ssm.us-east-1` never reaches dnsmasq and keeps returning the endpoint IP: its managed PHZ is
  more specific than `amazonaws.com`.
- Names cached minutes earlier (`s3.us-east-1`, `sts.us-east-1`) **still resolve with TTL 30**.
  That's serve-stale masking the breakage.
- `s3.ap-south-1` (never queried) **fails**.
- Each client query reaches dnsmasq **4–8 times**: after a `REFUSED`, the resolver retries from
  both outbound ENIs.

**Takeaway:** there is no system rule protecting the local region. An `amazonaws.com` rule breaks
every AWS service without an interface endpoint in that VPC, and floods the on-prem DNS with
queries it will refuse.

**Fix:** disassociate (~100 s) and delete the rule. If some AWS names must resolve on-prem, forward
only those specific names, never the whole `amazonaws.com`.

### Exercise 6 — DHCP search domain changes the query

Create a DHCP option set with `domain-name = svc.internal` and `domain-name-servers =
AmazonProvidedDNS`, tagged `Project=NET-015` so `teardown` removes it, associate it with VPC-B,
and renew the lease on the DR instance:
```
aws ec2 create-dhcp-options --region us-west-2 \
  --dhcp-configurations Key=domain-name,Values=svc.internal Key=domain-name-servers,Values=AmazonProvidedDNS \
  --tag-specifications 'ResourceType=dhcp-options,Tags=[{Key=Project,Value=NET-015}]'
aws ec2 associate-dhcp-options --region us-west-2 --vpc-id <vpc-b-id> --dhcp-options-id <new-dopt-id>
```
On the DR instance:
```
sudo networkctl renew $(ip -o route show default | awk '{print $5}')
grep search /etc/resolv.conf                # search svc.internal
```
Then from VPC-B, and check the VPC-B query log after each one:
```
dig filesvr.corp.example.com
dig +search filesvr.corp.example.com
dig +search filesvr
dig +search +ndots=5 filesvr.corp.example.com
```

**What you'll see:**
- **The DHCP change doesn't reach instances immediately.** Each one picks it up at lease renewal
  or reboot, so for a while the fleet runs mixed suffixes, which looks "intermittent".
- `dig filesvr.corp.example.com`: works. `dig` doesn't apply the search list without `+search`.
- `dig +search filesvr.corp.example.com`: works, and **no suffixed query is sent**. With the
  default `ndots:1`, a name containing a dot is tried as-is first, and the suffix is only appended
  if that fails.
- `dig +search filesvr` (short name): 2 queries, both `NXDOMAIN`: `filesvr.svc.internal.`, then
  `filesvr.` (answered by the root servers). `dig` only prints the last attempt; the query log
  shows all of them. With `domain-name = corp.example.com`, the first try would have succeeded.
- `dig +search +ndots=5 filesvr.corp.example.com` (the Kubernetes/EKS default): **the suffixed
  name goes first** (`filesvr.corp.example.com.svc.internal.` → `NXDOMAIN`), then the real name
  → `NOERROR`.

**Takeaways:**
- `domain-name-servers` decides **who** you ask; `domain-name` decides **what** you ask when the
  name is short.
- Where it really bites: short names (Windows domain join and `nltest` use them heavily) and
  `ndots:5` in EKS, where every external lookup pays extra wasted queries.
- **Security:** the suffixed names matched no rule, so they went out through `internet-resolver`
  to public DNS. A wrong search domain **leaks internal hostnames to the internet**.

**Fix options:** correct the `domain-name` in the DHCP option set (create a new set and associate
it; option sets are immutable), use a trailing period for absolute names
(`filesvr.corp.example.com.`), or override the suffix list on the client. **Revert** by
associating the original option set back and renewing the lease.

## Lab Notes

Things found while running the exercises against the live lab. Fixed in the repo unless noted.

- **On-prem DNS IP.** The rule targets the instance's DHCP-assigned private IP (`<onprem-dns-ip>`),
  not a fixed `192.168.1.100`.
- **`lab/dnsmasq.conf` had two bugs that stopped dnsmasq from working:**
  - `auth-zone` without `auth-server`: dnsmasq refuses to start
    (`--auth-server required when an auth zone is defined`). `dnsmasq --test` still reports
    "syntax check OK", because it validates syntax, not option combinations.
  - `listen-address=0.0.0.0`: dnsmasq starts and binds port 53, but silently drops every query,
    including from `127.0.0.1`. listen-address filters on the packet's destination IP, and
    `0.0.0.0` is not an interface address. The symptom is "service active, port listening, every
    query times out".
- **The on-prem DNS was exposed to the internet.** The instance has a public IP (for package
  install), and the original SG allowed UDP/TCP 53 and TCP 443 from `0.0.0.0/0`. dnsmasq logged
  `query[ANY] ni from 141.98.11.129`, an open-resolver scan typical of DNS amplification
  reconnaissance. It answered `REFUSED` thanks to `no-resolv`, but it shouldn't be reachable at
  all. The deploy now opens 53 only from VPC-A, and has no inbound 443 (the SSM agent only needs
  outbound 443).
- **Compliance automation stops lab instances.** In the lab account, an account-level compliance
  Lambda stopped the us-east-1 instances on a daily schedule, most likely because of the
  world-open SG on a public-IP instance. If instances go down mid-lab, check CloudTrail
  `StopInstances` to see who stopped them.
- **Workload SGs need inbound 443 from the VPC.** The SSM interface endpoints use them. The deploy
  now adds it.
- **`SERVFAIL` vs `TIMEOUT`.** When a target doesn't answer, Resolver query logs record
  `rcode: TIMEOUT` and `dig` shows `communications error ... timed out`, not SERVFAIL.
- **Teardown.** `teardown` waits for each dependency (rule disassociation → rule deletion →
  endpoint deletion → ENI release) and cleans each VPC by what's actually in it, including rules
  left over from exercises, GuardDuty-created `guardduty-data` endpoints and SGs, and any managed
  policies that account automation attached to the SSM role. If anything fails, it keeps
  `resources.json` so you can run it again.
- **Unresolved:** in Exercise 6, two queries returned TTL 30 in 0 ms and **didn't appear in the
  query log**, while the same name in the same second went through the chain with TTL 0. This
  looks like a resolver node still serving the stale answer from Exercise 4, and stale answers
  apparently aren't logged. Needs confirmation.

## Lab Resources

- **3 VPCs** — on-prem (us-east-1), production (us-east-1), DR (us-west-2)
- **2 VPC peerings** — on-prem↔production (intra-region), production↔DR (cross-region)
- **3 resolver endpoints** — production inbound, production outbound, DR outbound
- **2 forwarding rules** — corp.example.com in each VPC
- **2 Private Hosted Zones** — prod.internal, dr.internal
- **1 cross-region PHZ association** — prod.internal → VPC-B
- **2 query logging configs** — one per VPC, to CloudWatch Logs
- **3 EC2 instances** — on-prem DNS, production test, DR test
- **SSM interface endpoints** — in VPC-A and VPC-B, so the test instances need no internet access

**Estimated cost:** ~$1.00/hour (resolver endpoints dominate, and they keep billing with the
instances stopped). Tear down the same day.

## Files

- `README.md` — this document (study guide + exercises)
- `architecture.drawio` — the three-VPC topology and the forwarding chain
- `lab/deploy_lab.py` — `deploy | status | test | teardown`
- `lab/dnsmasq.conf` — the on-prem DNS configuration (corp.example.com zone, SRV, big TXT)

## Concepts to Know for SME (study checklist)

- [ ] Explain the difference between an inbound and an outbound resolver endpoint, including why
      they are separate resources and whose IP appears as the source at each hop
- [ ] Explain how the resolver chooses between forwarding rules, PHZs, endpoint-managed PHZs and
      system rules (most specific domain wins; rule beats PHZ on a tie)
- [ ] Explain why a forwarding rule for `amazonaws.com` breaks local-region service endpoints,
      and which names survive it
- [ ] Explain why DNS needs both TCP and UDP, what triggers TCP fallback, and why the SRV record
      in this lab never does
- [ ] Given a client timeout, use the `rcode` in two VPCs' query logs to decide whether the query
      never arrived, arrived but the answer didn't come back, or failed further down
- [ ] Explain `TIMEOUT` vs a fast `SERVFAIL` (silent drop vs ICMP port unreachable)
- [ ] Explain serve-stale: what the client sees, what the next hop logs, and how it can hide an
      outage or keep an old record alive
- [ ] Describe the cross-region PHZ association model: no forwarding rule needed, the VPC resolver
      resolves directly from the PHZ
- [ ] Explain why TTL 0 from the authoritative server multiplies forwarded query volume
- [ ] Explain how the DHCP `domain-name` and `ndots` decide which name is actually queried, why
      option set changes reach instances gradually, and how a wrong suffix leaks names to public DNS

## References

- [Resolving DNS queries between VPCs and your network](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver.html)
- [Forwarding outbound DNS queries to your network](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-outbound-queries.html)
- [Forwarding inbound DNS queries to your VPCs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries.html)
- [Managing forwarding rules](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-rules-managing.html)
- [Resolver query logging](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html)
- [How DNS traffic is routed for your VPC](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-dns.html#vpc-dns-resolving)
- [VPC DNS resolver (AmazonProvidedDNS)](https://docs.aws.amazon.com/vpc/latest/userguide/AmazonDNS-concepts.html)
- [DHCP option sets](https://docs.aws.amazon.com/vpc/latest/userguide/DHCPOptionSet.html)
- [RFC 8767 — Serving Stale Data to Improve DNS Resiliency](https://www.rfc-editor.org/rfc/rfc8767)
- [RFC 8914 — Extended DNS Errors](https://www.rfc-editor.org/rfc/rfc8914)
