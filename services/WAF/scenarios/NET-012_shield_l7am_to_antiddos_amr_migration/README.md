# NET-012 — Shield Advanced L7AM vs. AWSManagedRulesAntiDDoSRuleSet: activation parity

**Tier:** Case analysis — no deploy script
**Status:** Answered, open items pending customer data

## Case summary

Customer (financial-sector API platform — public REST APIs behind CloudFront and a regional
ALB/API Gateway stack) has **both** protections enabled simultaneously:

- Shield Advanced automatic application layer DDoS mitigation (L7AM) — the feature that creates
  `ShieldMitigationRuleGroup_*` inside the associated Web ACL.
- `AWSManagedRulesAntiDDoSRuleSet` (Anti-DDoS AMR), customer-managed.

They ran an approved production DDoS simulation on 2026-08-08 (5 waves, 06:08–09:14 UTC) and
observed **inconsistent activation**: neither mechanism fired first consistently, and in 2 waves
only one of the two produced a blocking action. They want to know whether the AMR is an
equivalent replacement once L7AM goes away.

**Resources involved**

- Regional Web ACL `api-gateway-alb-prod` — `us-west-2` (waves 1, 2, 5)
- Global Web ACL `cf-waf-prod` — `us-east-1` / CloudFront (waves 3, 4)

## Observed data (customer-reported)

- **Wave 1** — regional, 06:08 visible → Shield blocking 06:27 → AMR blocking 06:36. Peak 106,900 RPS.
- **Wave 2** — regional, 07:41 visible → Shield blocking 07:56 → **no AMR block observed**. Peak 18,100 RPS.
- **Wave 3** — CloudFront, 08:15 visible → Shield blocking 08:42 → **no AMR block observed**. Peak 1,790 RPS.
- **Wave 4** — CloudFront, 08:46 visible → **no Shield rule** → AMR blocking 08:50. Peak 161,900 RPS.
- **Wave 5** — regional, 09:14 visible → **no Shield rule** → AMR blocking same minute. Peak 190,500 RPS.

## The correlation the customer missed

Sorted by peak RPS, the pattern is not random at all:

- AMR did **not** act: 1,790 RPS (wave 3) and 18,100 RPS (wave 2) — the two smallest waves.
- AMR acted: 106,900 / 161,900 / 190,500 RPS (waves 1, 4, 5) — the three largest.
- Shield acted on waves 1, 2, 3 (the first three, chronologically) and **not** on waves 4, 5.

So AMR activation tracks **magnitude**, and Shield activation tracks **chronology**. Both are
explainable, and neither indicates a misconfiguration.

## Root-cause analysis

### 1. Measurement artifact: the AMR's default mitigation is Challenge, not Block

This is the first thing to rule out, and the most likely single cause of "no AMR blocking observed."

Rules in `AWSManagedRulesAntiDDoSRuleSet` and their default actions:

- `ChallengeAllDuringEvent` — **Challenge**. Matches any request carrying
  `awswaf:managed:aws:anti-ddos:challengeable-request` while the resource is under attack.
- `ChallengeDDoSRequests` — **Challenge**. Matches requests at/above the configured *challenge*
  sensitivity. **Only evaluated if `ChallengeAllDuringEvent` is overridden to Count.**
- `DDoSRequests` — **Block**. Matches requests at/above the configured *block* sensitivity.

A Challenge outcome appears in WAF logs as `action: CHALLENGE` and in CloudWatch under that rule's
own metric — **not** under `BlockedRequests`. If the customer filtered on blocked requests or on the
Web ACL `BlockedRequests` metric, soft mitigation was invisible to them by construction.

### 2. Block sensitivity gate

`DDoSRequests` matches by suspicion label, gated by the block sensitivity setting:

- **Low** → matches only `high-suspicion-ddos-request`
- **Medium** → matches `medium` + `high`
- **High** → matches `low` + `medium` + `high`

At 1,790 RPS on a CloudFront distribution, request-level suspicion scoring is very unlikely to reach
high-suspicion. Low block sensitivity therefore produces zero blocks even if the event was detected.

### 3. "Detected but did not act" is directly falsifiable

The AMR applies `awswaf:managed:aws:anti-ddos:event-detected` to **every** request going to a
protected resource in event state — attack traffic *and* legitimate traffic. So:

- Label metric present in waves 2/3 → **detected**, but no rule action fired (sensitivity /
  challengeability / token acceptance).
- Label metric absent → **not detected** as an event; the deviation from baseline was not significant.

This single label answers the customer's question 2 definitively. No internal tooling required —
it is in their own label metrics and logs.

### 4. Why Shield acted earlier or exclusively

Four independent mechanisms, all documented:

- **Shield's rule group contains an always-on rate-based rule.** `ShieldKnownOffenderIPRateBasedRule`
  rate-limits IPs known to be DDoS sources. It requires **no event detection** and is never removed.
  A production simulation typically sources traffic from cloud/hosting ranges that appear on threat
  intel lists, so this rule can fire within seconds at low volume. This is the most probable reason
  Shield "acted first" in waves 1–3, and it is *not* the L7AM signature capability being compared.
- **Shield retains mitigations after an attack subsides**, and may proactively keep a signature in
  place to defend against recurrence, extending the retention window as needed. Waves 2 and 3 were
  likely mitigated by rules already deployed for wave 1 — and waves 4/5 showed "no Shield rule"
  because no *new* rule needed to be created; the existing ones were already there. The customer was
  watching for rule creation, not rule presence.
- **Shield only deploys a mitigation if the derived signature isolates attack traffic** without
  impacting normal traffic to *any* resource on the same Web ACL. Otherwise it deploys nothing. At
  161,900 and 190,500 RPS the synthetic traffic may have been too similar to legitimate traffic to
  pass that validation. Documented explicitly: some attacks end before custom rules are deployed.
- **Health-based detection.** Shield requires less evidence of an attack when Route 53 health checks
  report the application unhealthy, and more evidence when it reports healthy. If health checks are
  attached to these protections, Shield tips earlier than a purely traffic-based detector.

### 5. Test methodology invalidates cross-wave comparison

Both mechanisms are **baseline-deviation** detectors:

- Shield needs the Web ACL associated for 24 hours to 30 days to build reliable baselines.
- The AMR profiles traffic within ~15 minutes of enablement, then detects within minutes.

Five waves against the same two resources inside ~3 hours progressively **raises the learned
baseline**. Each successive wave is a smaller relative deviation than the same volume would have been
in isolation. Wave 5 at 190,500 RPS is not comparable to wave 1 at 106,900 RPS, because by 09:14 the
resource had already seen four elevated-traffic periods that morning. Waves should be separated by
enough time for baselines to settle, and the volume ramp should be monotonic or randomized —
not interleaved 106k / 18k / 1.7k / 161k / 190k.

### 6. Resource-type coverage gap — needs confirmation

`AWSManagedRulesAntiDDoSRuleSet` protection extends to **Application Load Balancers and CloudFront
distributions only**. Shield Advanced L7AM likewise covers CloudFront distributions and ALBs; API
Gateway is not a Shield Advanced protected resource type.

The customer describes waves 1, 2 and 5 as "regional API Gateway traffic," yet the Web ACL is named
`api-gateway-alb-prod` and a Shield-generated rule appeared. That is only consistent if the traffic
actually terminated on an **ALB**. If the regional Web ACL is associated with both an ALB and an API
Gateway stage, then:

- Traffic to the ALB → full AMR + L7AM coverage.
- Traffic to the API Gateway stage → the Web ACL still evaluates rules, but there is no protected
  resource for either mechanism to baseline or declare an event on.

This alone could explain wave 2. Must confirm via `list-resources-for-web-acl` before asserting it.

## Answers to the six questions

1. **Timestamps for both mechanisms** — AMR side is fully customer-visible (label metrics + per-rule
   metrics + WAF logs). Shield side is not: Shield's rule group generates WAF metrics that are **not
   available to view**, same as any non-owned rule group. Detection/mitigation timeline for Shield
   comes from the Shield event console / `DescribeAttack`. Need the Shield event IDs.
2. **Waves 2/3 — detected or not** — resolved by presence/absence of
   `awswaf:managed:aws:anti-ddos:event-detected` in the 1-minute label metrics for those windows.
3. **Why Shield earlier/exclusive** — see section 4. Primarily the always-on known-offender
   rate-based rule plus mitigation retention across waves; not superior event detection.
4. **Config changes needed** — see recommendations below. Priority, block sensitivity, exempt URI
   regexes, no scope-down, token/SDK for non-browser clients, and own rate-based rules.
5. **Migration process** — none applies. They are already in the target state. Remaining work is
   validation and tuning, then optionally `DisableApplicationLayerAutomaticResponse` per resource.
6. **What happens at retirement** — per current documentation, L7AM is *superseded*, not deleted.
   Existing Shield Advanced customers may continue using it on existing or new accounts; new Shield
   Advanced customers must contact Support for legacy access. Their AMR configuration is
   customer-managed and AWS does not modify it.

## Recommendations for equivalence

**Priority and placement**

- Move `AWS-AWSManagedRulesAntiDDoSRuleSet` so it runs immediately after any pure Allow rules
  (IP allow-lists) and **before** every other rule. Anything that terminates evaluation earlier —
  a rate-based Block, a managed rule group Block, a Bot Control Allow — removes those requests from
  the AMR's view and degrades its baseline.
- **Do not** attach a scope-down statement to this rule group. Documented anti-pattern: it produces
  an inaccurate baseline and weaker event detection.

**Sensitivity and actions**

- Raise **block sensitivity** from Low to Medium so `DDoSRequests` also matches medium-suspicion
  requests. Validate in Count first.
- Keep `ChallengeAllDuringEvent` at Challenge for browser-facing paths. Note that leaving it at
  Challenge means `ChallengeDDoSRequests` is never evaluated — that is expected, not a bug.
- Review **Exempt URI regular expressions**. The console default targets `/api/`; their real paths
  may differ. Exempted URIs can only be mitigated by `DDoSRequests`, which makes block sensitivity
  the *only* control for pure API traffic. Up to five exemptions.

**Non-browser clients**

- Challenge requires a client that can execute the silent browser challenge and expects HTML.
  Native/mobile/machine clients need the AWS WAF application integration SDK, and the Web ACL needs
  correct token domain configuration. Otherwise Challenge behaves as a de facto block for legitimate
  API clients.

**Always-on floor (closest analogue to what they lose)**

- The one thing L7AM provided unconditionally was `ShieldKnownOffenderIPRateBasedRule` — a
  deterministic, always-active rate limit that needs no event detection. Replace it with their own
  rate-based rules: aggregate by IP, and a second by URI path or custom header for expensive
  endpoints, with scope-down statements. This is what makes protection independent of detection
  latency in either mechanism.

**Observability**

- Enable WAF logging (CloudWatch Logs or S3) on both Web ACLs and use the Web ACL traffic overview
  dashboard.
- Add label-match rules after the AMR for graduated response: Count or rate-limit
  `low-suspicion-ddos-request`, Block `high-suspicion-ddos-request`. This also gives them the
  visibility that Shield's rule group never provided.

**Capacity**

- L7AM rule group = 150 WCU. AMR = 50 WCU. Running both = 200 WCU. Shield Advanced covers standard
  WAF costs up to 1,500 WCU, so no issue at this scale, but worth tracking.

## Data to request

- `aws wafv2 get-web-acl` for both Web ACLs (full JSON — AMR rule group configuration, rule
  priorities, default action, overrides).
- `aws wafv2 list-resources-for-web-acl` for the regional Web ACL — confirm ALB vs API Gateway.
- `aws shield describe-protection` per protected resource, plus the automatic mitigation action
  setting (Count or Block) for each.
- `aws shield list-attacks` / `describe-attack` for the 2026-08-08 window, and the Shield event IDs.
- CloudWatch, 1-minute period, 2026-08-08 05:30–10:00 UTC:
  - Per-rule metrics for `ChallengeAllDuringEvent`, `ChallengeDDoSRequests`, `DDoSRequests`.
  - Label metrics: `awswaf:managed:aws:anti-ddos:event-detected`, `ddos-request`,
    `low/medium/high-suspicion-ddos-request`, `challengeable-request`.
  - `AllowedRequests`, `BlockedRequests`, `CountedRequests`, `ChallengeRequests` at Web ACL scope.
- WAF logs for the five wave windows.
- Whether Route 53 health checks are associated with the Shield Advanced protections.
- Source characteristics of the simulation traffic (hosting-provider ranges? residential proxies?
  realistic User-Agent and TLS fingerprints? does the client execute JS?).

## References

- AWS WAF DDoS prevention rule group — https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-anti-ddos.html
- Advanced Anti-DDoS protection using the AWS WAF Anti-DDoS managed rule group — https://docs.aws.amazon.com/waf/latest/developerguide/waf-anti-ddos-advanced.html
- Adding the Anti-DDoS managed rule group to your web ACL — https://docs.aws.amazon.com/waf/latest/developerguide/waf-anti-ddos-rg-using.html
- AWS WAF DDoS prevention (tiers, supported resources) — https://docs.aws.amazon.com/waf/latest/developerguide/waf-anti-ddos.html
- Automating application layer DDoS mitigation with Shield Advanced (supersession note, caveats) — https://docs.aws.amazon.com/waf/latest/developerguide/ddos-automatic-app-layer-response.html
- How Shield Advanced manages automatic mitigation — https://docs.aws.amazon.com/waf/latest/developerguide/ddos-automatic-app-layer-response-behavior.html
- Factors affecting application layer event detection and mitigation — https://docs.aws.amazon.com/waf/latest/developerguide/ddos-app-layer-detection-mitigation.html
- Shield Advanced rule group — https://docs.aws.amazon.com/waf/latest/developerguide/ddos-automatic-app-layer-response-rg.html
- CAPTCHA and Challenge action behavior — https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html
- Anti-DDoS AMR launch blog — https://aws.amazon.com/blogs/networking-and-content-delivery/introducing-the-aws-waf-application-layer-ddos-protection/

## Open items

- [ ] Establish whether a hard end-of-life date exists for L7AM beyond the 2026-03-26 supersession.
      Public documentation states only that the legacy solution remains available to existing
      subscribers, which is not the same as a commitment to keep it.
- [ ] Confirm regional Web ACL resource associations (ALB vs API Gateway stage).
- [ ] Pull Shield event detail once event IDs are provided.
- [ ] Reproduce in lab: AMR label emission at low RPS vs. high RPS on an ALB, to demonstrate the
      `event-detected` / suspicion-label boundary empirically.
