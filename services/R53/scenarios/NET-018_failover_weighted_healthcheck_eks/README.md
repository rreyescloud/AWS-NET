# NET-018 — Route 53 Failover + Weighted Routing + Health Checks with EKS ExternalDNS

**Tier:** Lab — design ready, scripts not written yet
**Status:** README and diagram only
**Services:** Route 53 (public hosted zone, failover, weighted, alias) · Route 53 Health Checks · EKS · AWS Load Balancer Controller · ExternalDNS · Application Load Balancer · CloudWatch

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2Fscenarios%2FNET-018_failover_weighted_healthcheck_eks%2Farchitecture.drawio)

## Business Context — SaaS / B2B API Platform

A SaaS company serves its public API from EKS in two regions. us-east-1 is primary and runs two
ALBs (70/30 traffic split between node pools); eu-west-1 is the passive secondary with one ALB.
ExternalDNS publishes DNS records from Kubernetes annotations, so each team owns its own
hostnames without touching the zone directly.

Requirements:
- **Active-passive failover** for `api.example.com` between regions
- **Weighted routing** inside the primary region
- **Health checks** on each ALB's `/healthz`
- **DNS managed from Kubernetes** by ExternalDNS, one controller per cluster

## Problem Statement

ExternalDNS already owns a simple A record for `api.example.com`. When the team adds a failover
policy on the same name by hand, Route 53 rejects the change:

```
InvalidChangeBatch: RRSet with DNS name api.example.com., type A, SetIdentifier primary
cannot be created as a non-weighted set exists with the same name and type.
```

Behind that one error sit the design questions this lab answers:
1. How do you combine cross-region failover with intra-region weighting?
2. Who owns which records when ExternalDNS and IaC share a zone?
3. What does "healthy" mean for an ALB: `EvaluateTargetHealth`, a `/healthz` health check, or both?
4. What does Route 53 answer when everything is unhealthy?

## Architecture

```
                         api.example.com   (IaC-owned)
                                │
                ┌───────────────┴───────────────┐
        failover PRIMARY                 failover SECONDARY
        alias → use1.api  ETH=yes        alias → euw1.api  ETH=yes
                │                                │
   use1.api.example.com (ExternalDNS 1)   euw1.api.example.com (ExternalDNS 2)
        ┌───────┴────────┐                       │
   weighted 70      weighted 30             weighted 100
   ALB-1 HC=hc-alb1  ALB-2 HC=hc-alb2       ALB-3 HC=hc-alb3
        │                │                       │
   EKS cluster 1 (us-east-1)               EKS cluster 2 (eu-west-1)

   Route 53 health checkers (internet) ──HTTPS /healthz──► each ALB, by the ALB's own DNS name
```

### Record structure

A name + type can hold **one** routing policy, so the two layers need different names. The
failover records alias the regional names.

```
use1.api.example.com  A  alias  weighted  set-id=alb1  weight=70   → ALB-1  ETH=yes  HC=hc-alb1
use1.api.example.com  A  alias  weighted  set-id=alb2  weight=30   → ALB-2  ETH=yes  HC=hc-alb2
euw1.api.example.com  A  alias  weighted  set-id=alb3  weight=100  → ALB-3  ETH=yes  HC=hc-alb3
api.example.com       A  alias  failover  PRIMARY    → use1.api.example.com  ETH=yes
api.example.com       A  alias  failover  SECONDARY  → euw1.api.example.com  ETH=yes
```

### Ownership split

- **ExternalDNS, cluster 1** → the `use1.api` weighted records, from Ingress annotations
- **ExternalDNS, cluster 2** → the `euw1.api` records
- **IaC** (CloudFormation/Terraform) → the `api.example.com` failover records and **all health
  checks**. ExternalDNS can reference a health check ID, but it doesn't create health checks.

Each ExternalDNS gets its own `--txt-owner-id`, `--domain-filter` and `--txt-prefix`. Annotation
names differ between ExternalDNS versions (`external-dns.alpha.kubernetes.io/` historically), so
check the docs for the version you deploy.

## Rules That Drive the Behavior

From the Route 53 Developer Guide and API Reference:

- **HTTP/HTTPS health checks:** TCP connection within **4 s**, then a 2xx/3xx within **2 s**.
  String matching only looks at the first **5,120 bytes**. HTTPS checks **don't validate
  certificates**. An endpoint is healthy when **more than 18 %** of checkers report healthy.
- **Alias + `EvaluateTargetHealth` to an ALB:** the ALB is healthy only if every target group that
  contains targets has at least one healthy target. The same page also says a target group with
  no registered targets is unhealthy, and those two sentences disagree (Exercise 3).
- **A record with both ETH and a health check** is unhealthy if **either** says unhealthy.
- **Fail-open:** if no record in a group is healthy, Route 53 treats them all as healthy and
  answers by routing policy. With failover, both unhealthy → the **primary** is returned.
- **Weights:** zero-weight records are only used when every nonzero record is unhealthy.
- **Alias to an ELB** answers with the ELB's **60 s TTL**, which you can't lower.
- **The `HealthCheckStatus` metric** is only in CloudWatch **us-east-1**.
- **Disabled vs inverted:** a *disabled* health check counts as healthy. To force unhealthy
  without touching the app, use *inverted*.
- **Alarm-based health checks** evaluate the alarm's **metric data stream**, not its displayed
  state. With no data, the result follows `InsufficientDataHealthStatus`.
- **Health checkers run on the internet**, so they can't reach private IPs (internal ALBs).

Expected failover time ≈ `RequestInterval (30 s or 10 s) × FailureThreshold` + 60 s TTL + client
caching. Exercise 2 measures it.

## Exercises

Two phases, to keep the expensive part short. Phase 1 needs the EKS clusters; Phase 2 replaces
EKS with ALBs using fixed-response rules, which reproduce the same DNS and health check
behavior at a fraction of the cost.

### Phase 1 — EKS (both clusters)

#### Exercise 1 — `InvalidChangeBatch` and the atomic fix

- **Reproduce:** let ExternalDNS create a simple `api.example.com` A record, then try to create the
  failover records on the same name. Capture the exact message.
- **Fix:** move the failover layer to IaC, and migrate the name with **one change batch** that
  deletes the simple record and creates the new ones. The `DELETE` must match the existing record
  exactly: name, type, alias target, ETH.
- **Verify:** a client resolving the name in a loop during the change never gets an empty answer.
- **Variants seen in cases:** "already exists", CNAME next to another type (including the
  ExternalDNS TXT registry record, fixed by `--txt-prefix`), and IaC replacing weighted → simple
  with create-before-delete, which rolls the stack back.
- **Design tip:** create production records with a routing policy from day one. A single weighted
  record behaves like simple, so later changes are weight edits, not replacements.

```json
{"Changes": [
  {"Action": "DELETE", "ResourceRecordSet": {"Name": "use1.api.example.com", "Type": "A",
    "AliasTarget": {"HostedZoneId": "<alb1-zone-id>", "DNSName": "<alb1-dns>", "EvaluateTargetHealth": true}}},
  {"Action": "CREATE", "ResourceRecordSet": {"Name": "use1.api.example.com", "Type": "A", "SetIdentifier": "alb1", "Weight": 70,
    "AliasTarget": {"HostedZoneId": "<alb1-zone-id>", "DNSName": "<alb1-dns>", "EvaluateTargetHealth": true}}},
  {"Action": "CREATE", "ResourceRecordSet": {"Name": "use1.api.example.com", "Type": "A", "SetIdentifier": "alb2", "Weight": 30,
    "AliasTarget": {"HostedZoneId": "<alb2-zone-id>", "DNSName": "<alb2-dns>", "EvaluateTargetHealth": true}}}
]}
```

#### Exercise 2 — Two-layer failover and how long it really takes

- **Break ALB-1** (scale its deployment to 0 or make `/healthz` return 500) → traffic stays in
  us-east-1 on ALB-2 (weighted layer).
- **Break ALB-1 and ALB-2** → `api.example.com` answers with eu-west-1 (failover layer).
- **Measure:** time from break to new answer, against the formula above. Compare a client that
  re-resolves per request with one that keeps a persistent connection. DNS failover only changes
  new lookups, and open connections keep the old IP.
- **Weighted split:** check the 70/30 against the authoritative name server (no resolver
  caching), then against ALB `RequestCount`. Resolver and client caching make real traffic drift
  far from the weights.

#### Exercise 3 — ETH, health checks and target groups

- **ETH + health check on the same record:** make `/healthz` fail while the targets stay healthy,
  then the reverse. Either one makes the record unhealthy. This explains "the health check was
  green and it still failed over".
- **Empty target group:** attach a listener rule with an empty target group to ALB-1. Does ETH
  mark the ALB unhealthy? This settles the contradiction in the docs.
- **IngressGroups:** add a second Ingress to ALB-1's IngressGroup
  (`alb.ingress.kubernetes.io/group.name`) whose pods fail readiness. One broken app can make the
  whole shared ALB unhealthy for ETH, and every hostname aliased to it fails over.

#### Exercise 4 — Everything unhealthy

- Break all three ALBs and record what `api.example.com` returns (expected: the primary,
  fail-open).
- Set ETH = No on the SECONDARY, break eu-west-1 only, and watch it keep being served.
- Discuss a static "maintenance" secondary (S3/CloudFront) as the real last resort.

#### Exercise 5 — ExternalDNS owner-id fight

- Deploy both ExternalDNS with the same `--txt-owner-id` (the default is `default`) and the same
  domain filter, with `--policy=sync`. Capture the create/delete loop in the logs.
- **Fix:** unique owner IDs + `--domain-filter`. Compare with `--policy=upsert-only`, which stops
  deletions but leaves stale records behind.
- **Also check:** how many TXT registry records ExternalDNS created per hostname. They count
  toward the 10,000 record sets per zone default quota
  (`aws route53 get-hosted-zone-limit --type MAX_RRSETS_BY_ZONE`).

### Phase 2 — No EKS (ALBs with fixed-response rules)

#### Exercise 6 — Health check unhealthy, app works

`/healthz` returns 200 from inside the VPC while the health check fails. Break each cause on
purpose and read `get-health-check-last-failure-reason` every time:
- SG/NACL/WAF blocks the `ROUTE53_HEALTHCHECKS` ranges → `Connection timed out ... blocked by your firewall`
- Slow `/healthz` (passes with curl, fails the 2 s budget)
- `Host` header that doesn't match a listener rule → default action 404/403
- Health check FQDN equal to the record it protects → unpredictable results
- String match beyond the first 5,120 bytes
- TLS 1.3-only listener policy: verify whether HTTPS checks still pass

#### Exercise 7 — Internal ALB

Convert ALB-2 to internal. The health check fails with `Resolved IP: 10.x.x.x. Connection timed
out`, because checkers can't reach private IPs. Try both fixes and compare time to detect:
- Alias with ETH = Yes (reads target group health, no reachability needed)
- CloudWatch alarm-based health check on `HealthyHostCount` (see Exercise 8 for its traps)

#### Exercise 8 — Alarm-based health check goes stale

Publish a custom "region stopped" metric for 10 minutes, then stop publishing. Record
`get-health-check-status` under each `InsufficientDataHealthStatus` (`Healthy`, `Unhealthy`,
`LastKnownStatus`), and what the alarm shows with `treatMissingData` = `missing` vs `ignore`.
- **Case pattern:** the alarm kept showing `ALARM` while Route 53 saw insufficient data, and
  `LastKnownStatus` gave inconsistent answers.
- **Fix:** `InsufficientDataHealthStatus=Unhealthy` and a metric published every minute. For an
  operator-driven "stop this region" switch, ARC routing controls are the purpose-built tool
  (see [NET-016](../../ApplicationRecoveryController/scenarios/NET-016_arc_region_switch_scp_deny/)).

#### Exercise 9 — IP-based health checks and "who is health-checking me?"

- **Break:** create an IP-based health check for ALB-1. ALB IPs change, so it eventually probes an
  address that's no longer yours. Fix: always health-check managed endpoints by domain name.
- **Security angle:** an IP pinned in someone else's health check can later belong to you. The
  requests come from `ROUTE53_HEALTHCHECKS` ranges with user agent
  `Amazon-Route53-Health-Check-Service (ref <id>; ...)` and a foreign `Host` header. Report the ref
  ID to AWS rather than blocking all checker ranges, which would break your own checks.

## Validation Commands

```bash
# Route 53's answer, no resolver caching
aws route53 test-dns-answer --hosted-zone-id <ZONE_ID> --record-name api.example.com --record-type A

# Authoritative weighted split
for i in $(seq 1 2000); do dig +short use1.api.example.com @<zone-ns>; done | sort | uniq -c

# Health check status and last failure reason, per checker region
aws route53 get-health-check-status --health-check-id <HC_ID>
aws route53 get-health-check-last-failure-reason --health-check-id <HC_ID>

# HealthCheckStatus metric (us-east-1 only)
aws cloudwatch get-metric-statistics --region us-east-1 --namespace AWS/Route53 \
  --metric-name HealthCheckStatus --dimensions Name=HealthCheckId,Value=<HC_ID> \
  --start-time <t0> --end-time <t1> --period 60 --statistics Minimum
```

Public DNS query logs record the query, resolver and response code, **not** the IPs returned. To
prove what Route 53 answered during a test, log your own lookups (`dig` every 10 s) alongside the
`HealthCheckStatus` metric.

## Lab Requirements

- **Public hosted zone:** a delegated lab subdomain (`example.com` is only a placeholder).
- **Phase 1:** 2 EKS clusters (us-east-1, eu-west-1) with the AWS Load Balancer Controller, and
  ExternalDNS with IRSA or Pod Identity (`route53:ChangeResourceRecordSets` on the zone,
  `route53:ListHostedZones`, `route53:ListResourceRecordSets`).
- **Phase 2:** 3 ALBs with fixed-response rules (`/healthz` → 200) and a small EC2 target for
  the slow-response and internal-ALB tests.
- **ALB security groups** allow the `ROUTE53_HEALTHCHECKS` ranges from `ip-ranges.json`.
- **Health checks:** HTTPS, domain name endpoint = each ALB's own DNS name, SNI enabled, and a
  listener rule that serves `/healthz` for that Host.

**Estimated cost:**
- Phase 1: ~$0.60–1.00/h (2 EKS control planes at $0.10/h + worker nodes + 3 ALBs + health checks).
- Phase 2: ~$0.10/h (3 ALBs + health checks).

Run each phase in one sitting and tear down the same day.

## Teardown

Order matters, and the leftovers nobody sees are what keep billing:

1. **ExternalDNS first.** If it's still running, it recreates the records you delete.
2. Failover records, then the weighted records and TXT registry records.
3. **Health checks.** They bill monthly per check and don't show up in any VPC or cluster view.
4. ALBs and target groups, including the empty target groups from Exercise 3.
5. EKS clusters, then the hosted zone.
6. **Verify:** `aws route53 list-health-checks`, `list-hosted-zones`, `aws eks list-clusters` in
   both regions and `aws elbv2 describe-load-balancers` in both regions all come back empty for
   the lab.

## Files

- `README.md` — this document
- `architecture.drawio` — record layers, ownership split, health check paths

## References

- [Choosing a routing policy](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-policy.html)
- [How health checks work in complex configurations](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-complex-configs.html)
- [How Route 53 determines whether a health check is healthy](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-determining-health-of-endpoints.html)
- [AliasTarget.EvaluateTargetHealth (API Reference)](https://docs.aws.amazon.com/Route53/latest/APIReference/API_AliasTarget.html)
- [Configuring router and firewall rules for health checks](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-router-firewall-rules.html)
- [Health checks based on CloudWatch alarms](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating-values.html#health-checks-creating-values-cloudwatch)
- [ExternalDNS — AWS provider](https://kubernetes-sigs.github.io/external-dns/latest/docs/tutorials/aws/)
- [AWS Load Balancer Controller — IngressGroup](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/#ingressgroup)
