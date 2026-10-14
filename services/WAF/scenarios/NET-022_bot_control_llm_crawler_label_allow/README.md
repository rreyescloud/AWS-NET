# NET-022 — AWS WAF Bot Control: Legitimate LLM Crawlers Blocked Despite a Label-Based Allow Design

**Tier:** Lab — reproducible end to end
**Status:** Design. Lab not yet deployed (results recorded as exercises are run)
**Services:** AWS WAF (Bot Control, custom rule groups, labels) · AWS Firewall Manager · CloudFront · S3 · CloudWatch Logs · AWS Organizations

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FWAF%2Fscenarios%2FNET-022_bot_control_llm_crawler_label_allow%2Farchitecture.drawio)

## Business Context — Healthcare Data & Analytics / Public Marketing Website

A healthcare data and analytics company runs its public marketing website on an open-source PHP
CMS behind CloudFront. Marketing wants the site to be discoverable by LLM platforms (answer
engine / generative engine optimization): search crawlers, user-triggered retrieval agents and
training crawlers.

WAF is managed centrally. A security account runs Firewall Manager and pushes the web ACL to the
application account's distributions.

Requirements:
- **Public content only**, `GET`/`HEAD` only
- **No access** to portals, APIs, logins or authenticated apps
- **No global Count** on Bot Control
- **No per-vendor exceptions**: the design has to cover future legitimate crawlers too
- **Keep blocking** malicious and unwanted bots

## Problem Statement

Requests from LLM crawlers to the marketing site get `HTTP 403` from WAF.

- The customer first blamed Bot Control. Their own log analysis, filtered by one major LLM
  vendor's published crawler IP ranges, showed that **90–95% of that traffic is terminated by a
  custom rule**, not by the Bot Control AI category rule. Only a small fraction carries
  `bot:category:ai`.
- **What changed:** nothing in the infrastructure. A new business requirement exposed a custom
  rule built about a year earlier to let one commercial SEO crawler through.
- **Considered and rejected:** setting the Bot Control category rules to Count. That drops
  protection for all bot traffic, not just LLM crawlers.
- **Proposed first:** a label-based design (allow verified AI bots, block the rest). It works for
  crawlers that Bot Control classifies, but it **doesn't fix the main crawler in question**.

## Architecture

- **Accounts:**
  - `<ACCOUNT_SECURITY>`: Firewall Manager administrator. Owns the FMS policy and the custom rule
    groups.
  - `<ACCOUNT_APP>`: application account with the CloudFront distributions in policy scope.
- **Region:** global (CloudFront scope). WAF resources for CloudFront live in `us-east-1`.
- **Edge:** CloudFront distribution(s) for `www.example.com`. The web ACL is managed by FMS
  (`<fms-web-policy>`, resource type `CLOUDFRONT`, account include list, auto-remediation
  disabled).
- **Origin:** CMS-hosted marketing site (details not confirmed). No VPC-level components involved.
- **Logging:** WAF logs to an S3 bucket (`aws-waf-logs-` prefix) in the security account.

### Web ACL evaluation order (default action Allow)

```
FMS pre-process rule groups
  1  Custom: block common PHP probes
     Custom: allow-by-IP-set (partner and internal crawlers)
     Custom: misnamed group that only allows a NAT egress IP set
     Custom: security-plugin allow
  2  AWSManagedRulesBotControlRuleSet
       InspectionLevel COMMON · version: default (not pinned) · 50 WCU · no scope-down
       override: SignalNonBrowserUserAgent → Count
       CategoryAI → Block (unchanged)
  3  Geo-block (one country)
  4  Request body-size groups (x4)
  5  AWSManagedRulesAmazonIpReputationList
  6  AWSManagedRulesCommonRuleSet (4 rules in Count)
  7  CMS-specific managed group · SQLi · PHP
FMS post-process rule groups
  8  Custom: <allow_seo_crawler_ua>   (12 WCU, last group in the web ACL)
default action → Allow
```

### The two rules that matter

- **Bot Control `CategoryAI` — Block.** Unlike the other category rules, it blocks **verified and
  unverified** AI bots alike.
- **Bot Control `SignalNonBrowserUserAgent` — Count.** It only adds
  `awswaf:managed:aws:bot-control:signal:non_browser_user_agent`, and it never evaluates or labels
  verified bots.
- **Custom `<allow_seo_crawler_ua>` — BLOCK, despite the name:**
  ```
  label  awswaf:managed:aws:bot-control:signal:non_browser_user_agent
  AND NOT  User-Agent EXACTLY "<seo-crawler-ua-string>"   (no text transformation)
  → BLOCK
  ```
  In practice this blocks every non-browser User-Agent on the site except one exact string.

### Failing flow A — an LLM crawler that Bot Control doesn't classify

1. The crawler sends `GET https://www.example.com/...` with UA `ExampleAIBot/1.0` from the
   vendor's range (`203.0.113.0/24`).
2. CloudFront hands the request to the web ACL. The pre-process groups don't match.
3. Bot Control has no `bot:name:*` or category entry for this UA. It adds no `bot:category:ai` and
   no `bot:verified`, only `signal:non_browser_user_agent`, and that rule is in Count, so the
   request keeps going.
4. The remaining managed groups don't match.
5. `<allow_seo_crawler_ua>`: the label is present and the UA isn't the SEO string → **BLOCK
   (403)**.

### Failing flow B — an LLM crawler that Bot Control does classify

1. Bot Control labels it `bot:category:ai`, `bot:name:<known-ai-bot>` and `bot:verified`.
2. `CategoryAI` **blocks it, even though it's verified**. The request never reaches the
   post-process group.

## Root Cause

Two independent blockers:

- **Blocker A (primary for the crawler in question):** the vendor's crawler isn't classified by
  Bot Control in the default version. `availableLabels` has no entry for it, so the only label it
  gets is `signal:non_browser_user_agent`, and the post-process custom rule blocks every
  non-browser UA except one exact SEO-crawler string. **No label-based allow can ever match it.**
- **Blocker B (secondary, for crawlers Bot Control does classify):** `CategoryAI` in Block stops
  them even when they're verified.

**Evidence:**
- `DescribeManagedRuleGroup` on the default Bot Control version: `availableLabels` includes several
  AI crawler `bot:name:*` labels and `bot:category:ai`, but **none for the vendor in question** (or
  for another large answer-engine vendor).
- Customer WAF log analysis filtered by the vendor's IP ranges: 90–95% terminated by the custom
  rule, only a few by `CategoryAI`.
- The rule group definition: BLOCK on a label match combined with NOT exact UA match.
- Public docs: `CategoryAI` blocks verified bots too, and the signal rules skip verified bots.

**Not confirmed:** individual log samples with `terminatingRuleId`, `labels` and the UA header.

**Lesson:** the group's name said "allow", its action was BLOCK, and it sat in the post-process
slot where nobody looked first. Read the action and the statement, never the name.

## Resolution

**Status:** proposed. Application not confirmed.

### Step 1 — Override only `CategoryAI` to Count (in the FMS policy)

The rule keeps labeling (`bot:category:ai`, `bot:name:*`, `bot:verified` / `bot:unverified`) but no
longer terminates. Every other Bot Control category rule stays in Block.

```
ManagedRuleGroupStatement:
  VendorName: AWS
  Name: AWSManagedRulesBotControlRuleSet
  ManagedRuleGroupConfigs: [{AWSManagedRulesBotControlRuleSet: {InspectionLevel: COMMON}}]
  RuleActionOverrides:
    - Name: SignalNonBrowserUserAgent   ActionToUse: {Count: {}}
    - Name: CategoryAI                  ActionToUse: {Count: {}}
```

### Step 2 — Rebuild the post-process group

It stays post-process on purpose: by the time a request reaches it, IP reputation, CRS, SQLi,
geo and the body-size groups have already been evaluated. An ALLOW here only skips the rest of
this group and the default action.

```
P0 ALLOW  Host = www.example.com AND Method IN (GET, HEAD)
          AND ( (label bot:category:ai AND (label bot:verified OR label bot:user_triggered:verified))
                OR label bot:web_bot_auth:verified )
P1 ALLOW  (interim, unclassified crawler only)
          Host = www.example.com AND Method IN (GET, HEAD)
          AND User-Agent CONTAINS "ExampleAIBot"
          AND source IP IN <ipset-vendor-crawlers>   (e.g. 203.0.113.0/24)
P2 BLOCK  label bot:category:ai
P3 BLOCK  label signal:non_browser_user_agent AND NOT label bot:name:<seo-crawler>
```

- **P0** covers every classified and verified AI crawler, including future ones, with no
  per-vendor rule. It's the requirement "no per-vendor exceptions" expressed as labels.
- **P1** is the only per-vendor exception, and it's temporary. UA alone is spoofable, so the IP
  set is mandatory.
- **P2** restores the block that Step 1 removed, for everything P0 didn't allow: unverified and
  spoofed AI UAs, and verified AI bots on other hosts or methods.
- **P3** replaces the old rule, keyed on the SEO crawler's label instead of an exact UA string.

All labels are namespaced `awswaf:managed:aws:bot-control:`. Check that each one exists in the
`availableLabels` of the Bot Control version in use before relying on it.

### Step 3 — Validate in Count before enforcing

Deploy the new rules in Count for 24–48 h and compare labels and terminating rules in the logs
before switching them to their real actions.

### Permanent path for unclassified crawlers

Request a Bot Control classification for the crawler through AWS Support. Once it's classified
and verifiable, P0 covers it and P1 is removed.

### Caveats

- P1 is a per-vendor exception and needs IP set maintenance (vendors publish and rotate ranges).
- `bot:user_triggered:verified` has lower confidence than `bot:verified`. Drop it for a stricter
  posture.
- The `Host` condition is only as good as the policy's resource scope. If the web ACL is also
  attached to a portal distribution, the `Host` check is what keeps crawlers out of it. Confirm
  which distributions are in scope.

## Troubleshooting Path

1. **Pull 2–3 blocked requests** from the WAF logs (S3 + Athena, or CloudWatch Logs Insights).
   Check `terminatingRuleId`, `ruleGroupList[].terminatingRule`, `labels[]` and
   `httpRequest.headers[user-agent]`.
   - Terminating rule is the custom post-process group → **blocker A**.
   - Terminating rule is `CategoryAI` → **blocker B**.
2. **Read the labels** on those requests:
   - `bot:category:ai` + `bot:verified` → classified and verified. Fix: `CategoryAI` override plus a
     label-based allow.
   - Only `signal:non_browser_user_agent` → unclassified. No label-based rule will ever match it.
3. **Check classification** for the crawler:
   ```
   aws wafv2 describe-managed-rule-group --vendor-name AWS \
     --name AWSManagedRulesBotControlRuleSet --scope CLOUDFRONT --region us-east-1 \
     --query 'AvailableLabels[].Name' --output text | tr '\t' '\n' | grep -i '<crawler>'
   ```
   Missing → unclassified.
4. **`aws wafv2 get-rule-group`** on every custom group that consumes Bot Control labels. Read the
   real action and statement; don't trust the group name.
5. **`aws fms get-policy`**: confirm rule group order (pre vs post), overrides, managed group
   version (pinned or default) and resource scope (which distributions, which accounts).
6. **Confirm the scope** covers only the marketing distributions, so the `Host` condition can't be
   bypassed through another distribution sharing the web ACL.
7. **Deploy in Count** and compare labels and terminating rules before and after.

### Logs Insights query — who is terminating crawler traffic

```
fields @timestamp, action, terminatingRuleId, httpRequest.clientIp, httpRequest.httpMethod,
       httpRequest.uri, @message
| filter httpRequest.clientIp like /^203\.0\.113\./
| parse @message /"name":"(?i)user-agent","value":"(?<ua>[^"]*)"/
| stats count(*) as requests by action, terminatingRuleId, ua
| sort requests desc
```

For requests terminated inside a rule group, `terminatingRuleId` is the group; the rule is in
`ruleGroupList[].terminatingRule.ruleId`.

## Lab Architecture

One account, no FMS (the central-policy model doesn't change WAF evaluation; it's an optional
variant).

- **S3 bucket** with a static page, private, behind **Origin Access Control**
- **2 CloudFront distributions** sharing **one web ACL**:
  - `marketing`: the site crawlers should reach
  - `portal`: stands in for the authenticated apps; crawlers must stay out
- **Web ACL `NET-022-acl`** (CLOUDFRONT scope, us-east-1, default Allow), reduced to what the
  exercises need:
  1. Pre-process slot (empty at first; Exercise 5 uses it)
  2. Bot Control COMMON, default version, `SignalNonBrowserUserAgent` → Count
  3. `AWSManagedRulesAmazonIpReputationList`
  4. `AWSManagedRulesCommonRuleSet`
  5. Post-process group `NET-022-allow_seo_crawler_ua`: the customer's BLOCK rule, reproduced as-is
- **IP set `NET-022-vendor-crawlers`** holding the lab client's public IP, standing in for the
  vendor's range
- **WAF logging** to CloudWatch Logs `aws-waf-logs-net022` (1-day retention)

The `Host` condition is tested with the second distribution: CloudFront rejects a `Host` header
that doesn't belong to the distribution before WAF sees it, so the realistic bypass is "the same
web ACL attached to another distribution", which is what Troubleshooting step 6 checks.

**What the lab can't reproduce:** `bot:verified` and `bot:web_bot_auth:verified`. Verification
depends on the real crawler infrastructure (reverse DNS, published IPs, signed requests). From a
lab IP, a classified AI UA always comes out `bot:unverified`. Flow B is shown with the unverified
variant and through the docs.

## Exercises

`<ai-ua-classified>` is a UA taken from `availableLabels` of the deployed Bot Control version,
picked at deploy time. Every exercise reads the result from the WAF log: `action`,
`terminatingRuleId`, `ruleGroupList[].terminatingRule` and `labels`.

### Exercise 1 — Reproduce blocker A (unclassified crawler)

```
curl -s -o /dev/null -w '%{http_code}\n' -A "ExampleAIBot/1.0" https://<marketing>.cloudfront.net/
```

- Expected: `403`. Labels: only `signal:non_browser_user_agent`. Terminated by
  `NET-022-allow_seo_crawler_ua`.
- Control: the exact SEO UA string → `200`. A browser UA → `200`.

### Exercise 2 — Reproduce blocker B (classified AI crawler)

```
curl -s -o /dev/null -w '%{http_code}\n' -A "<ai-ua-classified>" https://<marketing>.cloudfront.net/
```

- Expected: `403` from `CategoryAI`. Labels: `bot:category:ai`, `bot:name:<...>`, `bot:unverified`.
- Question to answer: does this request carry `signal:non_browser_user_agent` too? (Signal rules
  skip verified bots, but this one is unverified.)

### Exercise 3 — Apply the fix and validate it

Override `CategoryAI` → Count and replace the post-process group with P0–P3 (P1 with the lab IP).

- `ExampleAIBot/1.0` + `GET` from the lab IP on `marketing` → `200` (P1)
- Same UA from another IP (e.g. CloudShell) → `403` (P3)
- Same UA, `POST` → `403` (P3)
- Same UA, `GET` on `portal` → `403` (P3)
- `<ai-ua-classified>` from the lab IP → `403` (P2: unverified, so P0 doesn't match)
- `python-requests` on `marketing` → `403` from Bot Control `CategoryHttpLibrary` (other categories
  still block)
- Browser UA on both distributions → `200`

### Exercise 4 — Count before enforce

Put P0–P3 in Count (rule action override on the group's rules), replay Exercise 3, and confirm in
the log that each request has the expected `nonTerminatingMatchingRules` without changing its
outcome. That's the 24–48 h validation step in miniature.

### Exercise 5 — Allow placement: pre-process vs post-process

Move the P1 allow into the pre-process slot, ahead of every managed group, and send
`ExampleAIBot/1.0` with a CRS-triggering payload (`?file=../../etc/passwd`).

- Pre-process: `200`. The allow terminated evaluation before CRS inspected it.
- Post-process: `403` from CRS.
- Takeaway: an early allow doesn't mean "trust this crawler", it means "skip all inspection".

### Exercise 6 — Version drift

Pin Bot Control to the oldest available version, diff its `availableLabels` against the default
version, and note which AI crawlers each one classifies. An unpinned rule group changes which
crawlers fall into blocker A without any change on your side.

### Exercise 7 — Exact UA vs label for the SEO exception

With the original rule, send the SEO UA with a version bump or an extra token: it's blocked by the
exact match. With P3 (keyed on `bot:name:<seo-crawler>`), check what Bot Control labels the
modified UA as.

### Exercise 8 — Scope-down on Bot Control (design discussion + measurement)

Add a scope-down so Bot Control only evaluates the marketing host. Measure the Bot Control request
count (CloudWatch `AWS/WAFV2` metrics) against what you lose on the other hosts.

## Cost

Roughly **$0.03/hour** while deployed. WAF fees are prorated hourly:
- Web ACL $5/month + $1/month per rule or rule group
- Bot Control $10/month per web ACL + per-request fee beyond the included tier (lab traffic stays
  inside it)
- CloudFront requests and CloudWatch Logs ingestion: negligible for a lab
- FMS variant: FMS policy fee + AWS Config in both accounts

Tear down Bot Control right after testing.

## Teardown

Order matters:
1. Disassociate the web ACL from both distributions (set `WebACLId` to empty and wait for
   `Deployed`).
2. Disable and delete both distributions (disable, wait for `Deployed`, then delete with the ETag).
3. Delete the logging configuration and the log group.
4. Delete the web ACL, then the custom rule group, then the IP set (each needs its lock token).
5. Delete the OAC, empty and delete the bucket.
6. **Verify:** `wafv2 list-web-acls`, `list-rule-groups`, `list-ip-sets` (CLOUDFRONT, us-east-1),
   `cloudfront list-distributions`, and the bucket.

## Files

- `README.md` — this document
- `architecture.drawio` — the customer architecture with both failing flows (page 1) and the fix
  with the lab variant (page 2)
- `lab/deploy_lab.py` — deploy, exercise knobs and verified teardown (added when the lab is built)

## References

- [Bot Control rule group](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-bot.html)
- [Deploying Bot Control](https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control-deploying.html)
- [Bot Control examples](https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control-examples.html)
- [Labels on web requests](https://docs.aws.amazon.com/waf/latest/developerguide/waf-labels.html)
- [Label match rule statement](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-label-match.html)
- [Managed rule group versioning](https://docs.aws.amazon.com/waf/latest/developerguide/waf-managed-rule-groups-versioning.html)
- [Firewall Manager AWS WAF policies](https://docs.aws.amazon.com/waf/latest/developerguide/waf-policies.html)
- [WAF log fields](https://docs.aws.amazon.com/waf/latest/developerguide/logging-fields.html)
