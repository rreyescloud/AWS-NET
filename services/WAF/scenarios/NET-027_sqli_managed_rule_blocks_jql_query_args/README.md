# NET-027 — SQLi Managed Rule Blocks Issue-Tracker Query Language (JQL/ZQL) in Query Arguments

**Tier:** Case analysis
**Status:** Root cause confirmed on 2026-11-04 from the change history (web ACL update events), per-rule WAF metrics, a browser HAR capture and a WAF log record. Findings and the label-based exception were prepared for the customer; the customer still has to review the other sub-rules that went back to Block. Not yet replicated in a lab.
**Services:** AWS WAF (AWS managed rule groups, rule action overrides, labels) · Amazon CloudFront · Elastic Load Balancing (ALB) · AWS CloudTrail · CloudWatch

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FWAF%2Fscenarios%2FNET-027_sqli_managed_rule_blocks_jql_query_args%2Farchitecture.drawio) · [Interactive explainer](explainers/explainer.html)

## Business Context — Pension Administration Software / Self-hosted issue tracker for client test cycles

A software vendor that builds pension and benefits administration platforms runs a self-hosted
Jira Data Center instance (with a test-management plugin) on AWS. Its QA and delivery teams use
shared dashboards to track client acceptance test cycles. The instance is published through
CloudFront with a CLOUDFRONT-scope web ACL in front of an Application Load Balancer.

## Problem Statement

Users suddenly receive a CloudFront `403 ERROR — Request blocked` page when they click an item in a
Jira dashboard. Searching for a single issue directly still works. The customer's security team
reports that `SQLi_QueryArguments` started blocking "with no changes on our side" and moved the
rule to Count as a temporary fix. They ask why the rule began blocking and what the correct fix is.

## Architecture

- **Account:** `<ACCOUNT_A>` (single account, production).
- **Edge:** CloudFront distribution `<dist-jira>` for `jira.example.com`.
- **Web ACL:** `<webacl-jira>`, scope CLOUDFRONT (us-east-1), default action Allow. Rules by priority:
  0 custom rule group · 1 fallback-URL rule group · 2 `AWSManagedRulesCommonRuleSet` (most rules
  excluded) · 3 `AWSManagedRulesSQLiRuleSet` · 4 `KnownBadInputsRuleSet` · 5 `LinuxRuleSet` ·
  6 `AmazonIpReputationList`. No managed group has a pinned version.
- **Origin:** ALB → Apache → Jira application.

```
browser ──► CloudFront ──► web ACL (CLOUDFRONT) ──► ALB ──► Apache ──► Jira
                               │
                               └─ priority 3: SQLiRuleSet ─ SQLi_QUERYARGUMENTS inspects every query arg
```

## Root Cause

1. **JQL looks like SQL.** A dashboard click opens `/jira/issues/?jql=project = "<Project>" and
   issuetype = Test and status != Cancelled ...`. Test-management gadgets load iframes with
   `up_zqltext=project="PROJ" AND fixVersion="..." AND cycleName in (...)`. `SQLi_QUERYARGUMENTS`
   runs SQL injection detection (sensitivity LOW) on the value of **every** query argument. The WAF
   log shows `matchedData: ["project","=","<Project>","and","issuetype"]` — a field, an operator, a
   quoted literal and `and`, the same shape as a tautology-based injection.
2. **The "sudden" start was a configuration change, not an AWS update.** The change history shows
   six `UpdateWebACL` calls on the same day:

   | Time (UTC) | Actor | SQLi rule set overrides |
   |---|---|---|
   | 08:17 | Admin A | All 8 sub-rules set to **Count** (including `SQLi_QUERYARGUMENTS`) |
   | 08:18 | Admin A | `SQLiExtendedPatterns_BODY/HEADER/URIPATH` back to Block; `SQLi_QUERYARGUMENTS` still Count |
   | **15:21** | **Admin B** | Only `SQLi_BODY` and `SQLiExtendedPatterns_BODY` left in Count. **`SQLi_QUERYARGUMENTS` back to Block**, together with `SQLi_COOKIE`, `SQLi_URIPATH` and three ExtendedPatterns sub-rules |
   | 17:17 | Admin B | `SQLi_QUERYARGUMENTS` set to Count again (temporary fix) |

3. **Metrics match the change exactly.** `BlockedRequests` for the SQLi rule set ran at 1–78 per
   hour before, then 694 (15:00 hour), 1,192 (16:00), 281 (17:00) and no datapoints after 17:17.
4. **HAR confirms the edge block.** Of 958 requests, 3 returned `403` with `Server: CloudFront`,
   `X-Cache: Error from cloudfront` and the 919-byte default CloudFront error page. All three were
   test-management gadget iframes carrying `up_zqltext`. Gadgets without ZQL in the URL returned 200.

The unpinned rule group version is not the cause: the spike starts and stops with override changes,
and the same JQL already matched (in Count) before 15:21.

## What Was Verified

All checks were read-only. Each one confirms a hypothesis or rules one out.

| # | Check | Question | Result |
|---|---|---|---|
| 1 | WAF log record from the customer | Which rule and which field? | `SQLi_QUERYARGUMENTS`, `matchedFieldName: jql`, sensitivity LOW, matched `project = "..." and issuetype` |
| 2 | HAR parsed locally (958 entries) | Edge block or origin error? | 3 × 403 from CloudFront (`X-Cache: Error from cloudfront`, default "Request blocked" page); all carry `up_zqltext`; gadgets without ZQL return 200; query string ~1.2 KB, so not a size rule |
| 3 | `GetWebACL` | Which sub-rules are in Count? Is the version pinned? | 7 rules; 3 SQLi overrides to Count now; no managed group has a pinned version |
| 4 | `UpdateWebACL` events, consecutive payloads diffed | Customer change or AWS change? | 6 updates on the incident day; 15:21 UTC removed the Count override from 6 sub-rules; 17:17 UTC restored it for `SQLi_QUERYARGUMENTS` |
| 5 | Hourly `BlockedRequests` for the rule group | Do blocks follow the change? | 1–78/h before, 694 / 1,192 / 281 in the next three hours, no datapoints after 17:17 |
| 6 | Version-update hypothesis | Could a new default version explain it? | No: the spike starts and stops with override changes, and the same JQL already matched in Count before 15:21 |

## Resolution

Keep the sub-rule in Count and re-create the block with a label-based rule that skips the paths
where query languages are legitimate:

1. Keep `SQLi_QUERYARGUMENTS` in Count via `RuleActionOverrides` (a Count rule still adds its label).
2. Add a rule **after** the SQLi rule group (higher priority number):

```json
{
  "Name": "Block-SQLi-QueryArgs-except-tracker-search",
  "Priority": 4,
  "Statement": { "AndStatement": { "Statements": [
    { "LabelMatchStatement": { "Scope": "LABEL",
        "Key": "awswaf:managed:aws:sql-database:SQLi_QueryArguments" } },
    { "NotStatement": { "Statement": { "OrStatement": { "Statements": [
      { "ByteMatchStatement": { "FieldToMatch": { "UriPath": {} }, "PositionalConstraint": "STARTS_WITH",
          "SearchString": "/jira/issues", "TextTransformations": [{ "Priority": 0, "Type": "LOWERCASE" }] } },
      { "ByteMatchStatement": { "FieldToMatch": { "UriPath": {} }, "PositionalConstraint": "STARTS_WITH",
          "SearchString": "/jira/plugins/servlet/gadgets/", "TextTransformations": [{ "Priority": 0, "Type": "LOWERCASE" }] } },
      { "ByteMatchStatement": { "FieldToMatch": { "UriPath": {} }, "PositionalConstraint": "STARTS_WITH",
          "SearchString": "/jira/rest/", "TextTransformations": [{ "Priority": 0, "Type": "LOWERCASE" }] } }
    ] } } } }
  ] } },
  "Action": { "Block": {} },
  "VisibilityConfig": { "SampledRequestsEnabled": true, "CloudWatchMetricsEnabled": true,
    "MetricName": "Block-SQLi-QueryArgs-except-tracker-search" }
}
```

3. Optionally tighten the exception to authenticated sessions (session cookie present).
4. Review the other sub-rules that went back to Block at 15:21 (`SQLiExtendedPatterns_QUERYARGUMENTS`,
   `SQLi_COOKIE`, `SQLi_URIPATH`, `SQLiExtendedPatterns_HEADER/URIPATH`) against the logs before
   deciding their final action.
5. Optionally pin managed rule group versions so future default-version changes are deliberate.

## Troubleshooting Path

1. Read the WAF log record the customer shares: `terminatingRuleId`, `ruleGroupList[].terminatingRule.ruleId`,
   `terminatingRuleMatchDetails[].matchedFieldName` and `matchedData`.
2. Check the HAR: a `403` with `Server: CloudFront` and `X-Cache: Error from cloudfront` was generated
   at the edge, never at the origin. Decode the query string of each blocked request.
3. Export the current overrides:
   ```
   aws wafv2 get-web-acl --scope CLOUDFRONT --region us-east-1 --name <webacl-jira> --id <id> \
     --query "WebACL.Rules[?Name=='AWS-AWSManagedRulesSQLiRuleSet'].Statement.ManagedRuleGroupStatement.RuleActionOverrides"
   ```
4. Don't accept "nothing changed" — pull the change history and diff consecutive `rules` payloads:
   ```
   aws cloudtrail lookup-events --region us-east-1 \
     --lookup-attributes AttributeKey=EventName,AttributeValue=UpdateWebACL \
     --start-time <incident-day>T00:00:00Z
   ```
5. Correlate with hourly `BlockedRequests` for the rule group (`AWS/WAFV2`, dimensions `WebACL`, `Rule`).
6. Find which sub-rule and path terminate blocks (CloudWatch Logs Insights):
   ```
   fields @timestamp, httpRequest.uri, terminatingRuleId
   | filter action = "BLOCK"
   | parse @message '"terminatingRule":{"ruleId":"*"' as subrule
   | stats count(*) as hits by terminatingRuleId, subrule, httpRequest.uri
   | sort hits desc
   ```

## Common Mistakes

- Blaming a managed rule update because the version isn't pinned, without checking the change history.
- Leaving a whole sub-rule in Count forever for a false positive on one path.
- Placing the label rule **before** the rule group — the label doesn't exist yet and the rule never matches.
- Debugging the origin for a 403 that the edge generated.

## Lab Reproduction Notes

No lab yet. A minimal one: CloudFront → ALB → a tiny HTTP app (or a static 200 responder),
CLOUDFRONT web ACL with `AWSManagedRulesSQLiRuleSet`. Send
`GET /jira/issues/?jql=project%20%3D%20%22Demo%22%20and%20issuetype%20%3D%20Test` with the sub-rule in
Block (expect 403), then in Count + the label rule (expect 200 on `/jira/issues`, 403 on `/other?q=...`).
Teardown deletes the distribution, web ACL and ALB stack.

## Learning Exercises

1. Which other query arguments in a typical issue tracker (filters, saved searches, REST `jql`) would hit the same sub-rule?
2. Prove that `' OR 1=1--` in a query argument on a non-excepted path is still blocked by the label rule.
3. Compare a scope-down statement on the whole SQLi rule group with the label-based rule. What protection does each give up?
4. Replace the URI exception with "session cookie present". What does an unauthenticated attacker gain or lose?

## References

- [SQL database managed rule group](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-use-case.html#aws-managed-rule-groups-use-case-sql-db)
- [Overriding rule actions in a rule group](https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-rule-group-override-options.html)
- [Label match rule statement](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-label-match.html)
- [Managed rule group versioning](https://docs.aws.amazon.com/waf/latest/developerguide/waf-managed-rule-groups-versioning.html)
- [Log fields for web ACL traffic](https://docs.aws.amazon.com/waf/latest/developerguide/logging-fields.html)
