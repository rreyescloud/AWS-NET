# NET-022 — AWS WAF Bot Control: Legitimate LLM Crawlers Blocked Despite a Label-Based Allow Design

**Tier:** Lab — reproducible end to end
**Status:** Lab Part 1 (Bot Control version comparison) built and run on 2026-10-13. Part 2 (customer rules + fix) is still design.
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
- **Lab (Part 1):** the default Bot Control version is `Version_1.0`, and it has no Anthropic
  label. In 1.0, ClaudeBot gets only `signal:non_browser_user_agent`. `Version_6.0` and
  `Version_6.1` classify the Claude crawlers (`bot:name:claudebot`, `bot:category:ai`, ...), and
  they don't add the signal label to them. The unpinned version is what turns a known crawler into
  blocker A.

**Not confirmed:** individual log samples with `terminatingRuleId`, `labels` and the UA header.

**Lesson:** the group's name said "allow", its action was BLOCK, and it sat in the post-process
slot where nobody looked first. Read the action and the statement, never the name.

## Resolution

**Status:** proposed. Application not confirmed.

### Step 0 — Pin Bot Control to a version that classifies the crawler

An unpinned Bot Control runs the current default, which was `Version_1.0` on 2026-10-13. Check the
crawler's `bot:name:*` label in each available version:

```
aws wafv2 describe-managed-rule-group --vendor-name AWS --name AWSManagedRulesBotControlRuleSet \
  --scope CLOUDFRONT --region us-east-1 --version-name Version_6.1 \
  --query 'AvailableLabels[].Name' --output text | tr '\t' '\n' | grep -i '<crawler>'
```

Pin the policy to the oldest version that has the label (`Version: Version_6.1` in the managed
rule group statement). In the lab, this alone moved the Claude crawlers out of blocker A: they're
classified, so they no longer carry the signal label that the post-process rule matches. Steps 1–2
are still needed, because now `CategoryAI` blocks them.

- A pinned version is a commitment. Watch the version's expiry and move forward on your own
  schedule, because AWS deprecates old versions.
- A newer version changes labels and the rules' behavior for every bot, not only for AI crawlers.
  Run it in Count first (Step 3).

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

First check whether a newer Bot Control version already classifies the crawler (Step 0). If none
does, request a classification through AWS Support. Once it's classified and verifiable, P0 covers
it and P1 is removed.

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

The lab is built in two parts. **Part 1 is built and was run**. It answers the question at the
root of blocker A: does Bot Control classify the crawler at all, and does that depend on the
version? **Part 2 is design only.** It reproduces the customer's rules and the fix (Exercises 1–5,
7, 8).

### Part 1 (built) — Bot Control version comparison

One account, no FMS, `us-east-1` (CLOUDFRONT scope). `lab/deploy_lab.py` creates:

- **S3 bucket** `net022-lab-<account>`: private, Block Public Access on, one `index.html`.
- **Origin Access Control**: CloudFront signs the requests to S3. The bucket policy allows
  `s3:GetObject` only for the two lab distributions (`AWS:SourceArn`).
- **Two web ACLs**, default action Allow. Each one has a single rule, Bot Control COMMON:
  - `NET-022-acl-default`: no `Version`, so it gets whatever AWS serves as the default.
  - `NET-022-acl-v61`: pinned to `Version_6.1`.
  - **Every Bot Control rule is overridden to Count.** Nothing is blocked, so the WAF log shows
    only the labels each version adds.
- **WAF logging** of each web ACL to its own CloudWatch Logs group, `aws-waf-logs-lab-default` and
  `aws-waf-logs-lab-v61` (1-day retention).
- **Two CloudFront distributions**, one per web ACL, both on the same S3 origin.

`test` sends the same set of User-Agents to both distributions:
- ClaudeBot, Claude-SearchBot, Claude-User and GPTBot, with their published UA strings
- `ExampleAIBot/1.0`, a stand-in for an unclassified crawler
- `curl` and a Chrome browser, as controls

It tags each request with `?run=<HHMMSS>&ua=<key>`. `labels` reads both log groups for that run and
prints the labels side by side.

### Run the lab

Requires Python 3.12+ and boto3, with a profile for the lab account.

```
cd lab
python deploy_lab.py deploy   --profile <lab-profile>   # bucket, OAC, 2 web ACLs + logging, 2 distributions (~5–10 min)
python deploy_lab.py status   --profile <lab-profile>   # default version now vs at deploy, distribution state, browser 200
python deploy_lab.py test     --profile <lab-profile>   # every UA → both distributions, prints the run tag
python deploy_lab.py labels [RUN] --profile <lab-profile>   # waits for the logs (1–5 min), compares labels per UA
python deploy_lab.py teardown --profile <lab-profile>   # deletes everything and verifies (~5–15 min)
```

`deploy` is idempotent: it reads `lab/resources.json` and skips what already exists.
`resources.json` and the logs are git-ignored because they hold live resource IDs.

### Part 1 results (run on 2026-10-13)

At deploy time, `CurrentDefaultVersion` for Bot Control was **`Version_1.0`**. The available
versions were 1.0, 2.0, 3.0–3.3, 4.0, 5.0, 6.0 and 6.1. `availableLabels` per version:
- **Version_1.0:** 247 labels and **no Anthropic entry at all**.
- **Version_6.0:** `bot:name:claudebot`, `bot:name:claude_user`, `bot:name:claude_web`,
  `bot:name:anthropic` and `bot:organization:anthropic`.
- **Version_6.1:** 950 labels in total. The same as 6.0 plus `bot:name:claude_searchbot`.

These are the labels observed in the WAF log. All the rules were in Count, so every request got a
`200`. The prefix `awswaf:managed:aws:bot-control:` is omitted.

- **ClaudeBot**
  - Version_1.0: only `signal:non_browser_user_agent`. **This is blocker A.**
  - Version_6.1: `bot:category:ai`, `bot:name:claudebot`, `bot:organization:anthropic`,
    `bot:intent:train_ai`, `bot:unverified`. **No `signal:non_browser_user_agent`.**
- **Claude-SearchBot**
  - Version_1.0: no Bot Control label.
  - Version_6.1: `bot:category:ai`, `bot:name:claude_searchbot`, `bot:intent:search`,
    `bot:organization:anthropic`, `bot:unverified`.
- **Claude-User**
  - Version_1.0: no Bot Control label.
  - Version_6.1: `bot:category:ai`, `bot:name:claude_user`, `bot:intent:ai_assistant`,
    `bot:organization:anthropic`, `bot:unverified`.
- **GPTBot**
  - Version_1.0: `bot:category:ai`, `bot:name:gptbot`, `bot:unverified`,
    `signal:non_browser_user_agent`. It's classified even in 1.0, so it would hit blocker B.
  - Version_6.1: the same, plus `bot:intent:train_ai` and `bot:organization:openai`, and
    **without** the signal label.
- **ExampleAIBot/1.0**: `signal:non_browser_user_agent` in both versions. It stays unclassified,
  as expected.
- **curl**: `bot:category:http_library`, `bot:name:curl`, `bot:unverified` in both. The signal
  label shows up only in 1.0.
- **Browser**: no bot label in either version.
- **Version_6.1 only**: every request also matched `TGT_TokenAbsent` (counted). This means a
  targeted-level rule is evaluated at COMMON inspection level in 6.1.

What this means for the case:
- **Blocker A is caused by the unpinned version.** With the default (1.0), ClaudeBot carries only
  the signal label, so the customer's post-process rule blocks it and no label-based allow can
  match it.
- **In 6.x the picture flips.** The Claude crawlers are classified and lose the signal label, so the
  post-process rule no longer matches them. The only remaining blocker is `CategoryAI` (blocker B),
  which Step 1 and P0 already handle.
- From the lab IP every crawler comes out `bot:unverified`, as expected. That P0 matches the real
  crawler from its own infrastructure (`bot:verified`) is **not confirmed** by the lab.

### Part 2 (design, not built) — customer rules and fix

This part adds what Exercises 1–5, 7 and 8 need on top of Part 1:

- A second distribution, `portal`, that shares the marketing web ACL. It stands in for the
  authenticated apps that crawlers must not reach.
- The managed groups `AWSManagedRulesAmazonIpReputationList` and `AWSManagedRulesCommonRuleSet`.
- The post-process group `NET-022-allow_seo_crawler_ua`: the customer's BLOCK rule, reproduced
  as-is.
- The IP set `NET-022-vendor-crawlers`, holding the lab client's public IP as a stand-in for the
  vendor's range.

The `Host` condition is tested with the second distribution. CloudFront rejects a `Host` header
that doesn't belong to the distribution before WAF sees it. So the realistic bypass is "the same
web ACL attached to another distribution", which is what Troubleshooting step 6 checks.

**What the lab can't reproduce:** `bot:verified` and `bot:web_bot_auth:verified`. Verification
depends on the real crawler infrastructure (reverse DNS, published IPs, signed requests). From a
lab IP, a classified AI UA always comes out `bot:unverified`.

## Exercises

`<ai-ua-classified>` is a UA taken from the `availableLabels` of the deployed Bot Control version.
Every exercise reads the result from the WAF log: `action`, `terminatingRuleId`,
`ruleGroupList[].terminatingRule` and `labels`.

Exercise 6 runs on Part 1. The others need Part 2.

### Exercise 1 — Reproduce blocker A (unclassified crawler) · *Part 2*

```
curl -s -o /dev/null -w '%{http_code}\n' -A "ExampleAIBot/1.0" https://<marketing>.cloudfront.net/
```

- Expected: `403`. Labels: only `signal:non_browser_user_agent`. Terminated by
  `NET-022-allow_seo_crawler_ua`.
- Control: the exact SEO UA string → `200`. A browser UA → `200`.
- With Bot Control on the default version (1.0), ClaudeBot behaves the same way (see the Part 1
  results).

### Exercise 2 — Reproduce blocker B (classified AI crawler) · *Part 2*

```
curl -s -o /dev/null -w '%{http_code}\n' -A "<ai-ua-classified>" https://<marketing>.cloudfront.net/
```

- Expected: `403` from `CategoryAI`. Labels: `bot:category:ai`, `bot:name:<...>`, `bot:unverified`.
- Does this request also carry `signal:non_browser_user_agent`? Part 1 answers it: **it depends on
  the version.** Yes in 1.0 (GPTBot), no in 6.1.

### Exercise 3 — Apply the fix and validate it · *Part 2*

Override `CategoryAI` → Count and replace the post-process group with P0–P3 (P1 with the lab IP).

- `ExampleAIBot/1.0` + `GET` from the lab IP on `marketing` → `200` (P1)
- Same UA from another IP (e.g. CloudShell) → `403` (P3)
- Same UA, `POST` → `403` (P3)
- Same UA, `GET` on `portal` → `403` (P3)
- `<ai-ua-classified>` from the lab IP → `403` (P2: unverified, so P0 doesn't match)
- `python-requests` on `marketing` → `403` from Bot Control `CategoryHttpLibrary` (other categories
  still block)
- Browser UA on both distributions → `200`

### Exercise 4 — Count before enforce · *Part 2*

Put P0–P3 in Count (rule action override on the group's rules) and replay Exercise 3. In the log,
confirm that each request has the expected `nonTerminatingMatchingRules` and that its outcome
doesn't change. That's the 24–48 h validation step in miniature.

### Exercise 5 — Allow placement: pre-process vs post-process · *Part 2*

Move the P1 allow into the pre-process slot, ahead of every managed group. Then send
`ExampleAIBot/1.0` with a CRS-triggering payload (`?file=../../etc/passwd`).

- Pre-process: `200`. The allow terminated evaluation before CRS inspected it.
- Post-process: `403` from CRS.
- Takeaway: an early allow doesn't mean "trust this crawler", it means "skip all inspection".

### Exercise 6 — Version drift · *Part 1, done*

Compare the labels from an unpinned Bot Control with those from a pinned recent version, for the
same User-Agents:

```
python deploy_lab.py test --profile <lab-profile>
python deploy_lab.py labels --profile <lab-profile>
```

Result: see the Part 1 results. The default version (1.0) doesn't classify any Claude crawler, and
6.0/6.1 do. An unpinned rule group decides which crawlers fall into blocker A, with no change on
your side. `status` prints the default version again, so a later change of default shows up.

### Exercise 7 — Exact UA vs label for the SEO exception · *Part 2*

With the original rule, send the SEO UA with a version bump or an extra token: the exact match
blocks it. With P3 (keyed on `bot:name:<seo-crawler>`), check what label Bot Control gives the
modified UA.

### Exercise 8 — Scope-down on Bot Control (design discussion + measurement) · *Part 2*

Add a scope-down so that Bot Control only evaluates the marketing host. Then measure the Bot Control
request count (CloudWatch `AWS/WAFV2` metrics) against what you lose on the other hosts.

## Cost

Part 1 costs roughly **$0.045/hour** while deployed (about $32/month if left running). WAF fees are
prorated hourly:
- 2 web ACLs × ($5/month + $1/month for the Bot Control rule)
- 2 × Bot Control $10/month per web ACL. The per-request fee doesn't apply, because lab traffic
  stays inside the included tier.
- CloudFront requests and CloudWatch Logs ingestion: negligible
- FMS variant: FMS policy fee + AWS Config in both accounts

Tear down right after testing.

## Teardown

```
python deploy_lab.py teardown --profile <lab-profile>
```

What it does, in order (the order matters):
1. Detaches the web ACL from each distribution and disables it, then waits for `Deployed`
   (3–10 min). After that it deletes the distribution with the current ETag.
2. Deletes the WAF logging configuration, the web ACL (with its lock token) and the log group, for
   each variant.
3. Deletes the OAC (with its ETag).
4. Empties the bucket (every object version and delete marker) and deletes it.
5. **Verifies** that no distribution, web ACL, log group, OAC or bucket from `resources.json` is
   left. Only then does it delete `resources.json`. If anything is left, it keeps the file and exits
   with code 1. Run it again to finish (for example, if a distribution was still deploying).

Manual check:

```
aws wafv2 list-web-acls --scope CLOUDFRONT --region us-east-1 --query 'WebACLs[?starts_with(Name,`NET-022`)]'
aws cloudfront list-distributions --query 'DistributionList.Items[?starts_with(Comment,`NET-022`)].Id'
aws logs describe-log-groups --log-group-name-prefix aws-waf-logs-lab- --region us-east-1
aws s3api head-bucket --bucket net022-lab-<account>
```

## Files

- `README.md`: this document
- `architecture.drawio`: the customer architecture with both failing flows (page 1), and the fix
  with the lab variant (page 2)
- `lab/deploy_lab.py`: Part 1 lab. Commands: `deploy`, `status`, `test`, `labels` and a verified
  `teardown`.
- `explainers/ste100.md`: the case explained in ASD-STE100 (Simplified Technical English)
- `explainers/explainer.html`: an interactive request simulator (current web ACL vs the fix) with
  a narrated walkthrough. Open it locally in a browser. It's modeled on the default Bot Control
  version.

## References

- [Bot Control rule group](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-bot.html)
- [Deploying Bot Control](https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control-deploying.html)
- [Bot Control examples](https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control-examples.html)
- [Labels on web requests](https://docs.aws.amazon.com/waf/latest/developerguide/waf-labels.html)
- [Label match rule statement](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-label-match.html)
- [Managed rule group versioning](https://docs.aws.amazon.com/waf/latest/developerguide/waf-managed-rule-groups-versioning.html)
- [Firewall Manager AWS WAF policies](https://docs.aws.amazon.com/waf/latest/developerguide/waf-policies.html)
- [WAF log fields](https://docs.aws.amazon.com/waf/latest/developerguide/logging-fields.html)
