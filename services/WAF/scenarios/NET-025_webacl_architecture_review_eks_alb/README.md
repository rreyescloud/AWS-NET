# NET-025 — AWS WAF Web ACL Architecture Review for EKS Ingress Load Balancers

**Tier:** Case analysis
**Status:** Design review framework written on 2026-11-03. The customer's web ACL JSON, logging configuration and per-rule metrics are still pending, so findings below are the review method plus the checks that decide each answer.
**Services:** AWS WAF (managed rule groups, rate-based rules, labels, WCU) · Elastic Load Balancing (ALB) · Amazon EKS (AWS Load Balancer Controller) · CloudWatch

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FWAF%2Fscenarios%2FNET-025_webacl_architecture_review_eks_alb%2Farchitecture.drawio) · [Interactive explainer](explainers/explainer.html)

## Business Context — Banking Software / Hosted escrow platform

A banking software vendor runs a client-facing escrow platform on Amazon EKS. Two internet-facing
Application Load Balancers, created by the AWS Load Balancer Controller, publish the cluster: one in
front of an Istio ingress gateway and one in front of a second Kubernetes ingress. A single REGIONAL
web ACL protects both.

The platform team asks for a general review before tightening the configuration. No incident is open.

## Problem Statement

The customer wants four questions answered about the existing web ACL:

1. Are the rule groups efficient (order, duplication, capacity)?
2. Is the rate-limiting logic correct?
3. How well does the web ACL cover the OWASP Top 10?
4. What is the false-positive risk, and how should it be managed?

The request ships no configuration. A design review without the web ACL JSON is guesswork, so the
first step is to collect the data listed in [Troubleshooting Path](#troubleshooting-path).

## Architecture

- **Account:** `<ACCOUNT_A>` (single account, production).
- **Scope:** REGIONAL web ACL `<webacl-prod>`, associated with `<alb-istio-ingress>` and `<alb-k8s-ingress>`.
- **Compute:** EKS cluster; ALBs and their WAF association owned by the AWS Load Balancer Controller.
- **Edge:** no CloudFront confirmed in front of the ALBs (it changes the rate-based key choice, see below).

```
clients ──► web ACL (REGIONAL, shared) ──► ALB istio ingress ──► Istio gateway ──► services
                                      └──► ALB k8s ingress   ──► ingress       ──► services
```

## Analysis

### 1. Rule group efficiency

- A web ACL includes **1,500 WCU** in its base price. Above that (maximum 5,000) every 500 WCU adds a
  per-request charge. `AWSManagedRulesCommonRuleSet` alone is 700 WCU.
- Rules run by priority; Allow, Block, CAPTCHA and Challenge terminate evaluation, Count doesn't.
  Cheap, high-confidence rules first; intelligent-threat groups (Bot Control, ATP) last and always
  behind a scope-down, because they are billed per inspected request.
- Look for groups that don't match the stack (PHP / WordPress / Windows groups in front of services
  that don't use them; Linux + Unix both enabled) and for rules left in Count after a past tuning.
- One web ACL shared by two different workloads means every exclusion applies to both. Splitting it
  per ALB is worth it when traffic profiles differ (API vs browser).

### 2. Rate-limiting logic

- Windows: 60, 120, 300 or 600 s. Up to 5 aggregation keys (IP, forwarded IP, header, cookie, query
  argument, URI path, HTTP method, label namespace, JA3/JA4).
- **Behind a proxy or CDN** the source IP is the proxy. Use `FORWARDED_IP` with `X-Forwarded-For`, or
  move the web ACL to CloudFront.
- **Shared egress (corporate NAT, mobile carrier):** per-IP limits punish many legitimate users. For
  authenticated APIs, aggregate on the token or API key header.
- Layer it: one broad per-IP rule plus narrow rules with scope-down on `/login`, OTP and write APIs.
- The rate is estimated and acts after a short delay. It is a protection control, not a business quota.

### 3. OWASP Top 10 (2021) coverage

| Category | WAF | Control |
|---|---|---|
| A01 Broken Access Control | Partial | `AdminProtectionRuleSet`, path/IP rules; authorization stays in the app |
| A02 Cryptographic Failures | No | ALB TLS policy, app-side encryption |
| A03 Injection | Yes | `SQLiRuleSet`, `CommonRuleSet` (XSS), `LinuxRuleSet` (LFI) |
| A04 Insecure Design | No | Threat modeling |
| A05 Security Misconfiguration | Partial | `CommonRuleSet` probe rules; review what each ALB exposes |
| A06 Vulnerable Components | Partial | `KnownBadInputsRuleSet` (for example Log4j); patching stays in the app |
| A07 Auth Failures | Partial | Rate-based rules on login, ATP |
| A08 Integrity Failures | No | Pipeline and artifact signing |
| A09 Logging Failures | Partial | Web ACL logging enabled |
| A10 SSRF | Partial | `CommonRuleSet` `EC2MetaDataSSRF_*`; IMDSv2 on nodes |

**ALB body limit:** WAF inspects at most the first 8 KB of the body on an ALB, and the limit can't be
raised for ALB. `SizeRestrictions_BODY` in the Core rule set blocks larger bodies by default, which is
both a protection (no uninspected payload) and the most common false positive.

### 4. False-positive risk

Most frequent Core rule set offenders: `SizeRestrictions_BODY` (uploads, large JSON),
`CrossSiteScripting_BODY` (rich text), `NoUserAgent_HEADER` (machine clients, health checks) and
`GenericRFI_*` (URLs in parameters).

## Recommendation

1. **Count first** for every new group or rule (`OverrideAction: Count` / `RuleActionOverrides`).
2. **Measure** with web ACL logs (query in Troubleshooting Path).
3. **Exclude narrowly:** set only the offending rule to Count, then add a custom rule that blocks on
   its label unless the request matches the legitimate path, for example label
   `awswaf:managed:aws:core-rule-set:SizeRestrictions_Body` and URI not `/upload`.
4. **Block** the rest and watch `BlockedRequests` per rule for one to two weeks.
5. Declare the web ACL in the Ingress annotation `alb.ingress.kubernetes.io/wafv2-acl-arn`, so the
   controller and the console don't fight over the association.

## Common Mistakes

- Enabling new managed groups straight in Block.
- Disabling a whole rule for one false positive on one path instead of label + scope-down.
- Per-IP rate limiting behind a CDN without `FORWARDED_IP`.
- Associating the web ACL by hand on a controller-managed ALB.
- Treating WAF as full OWASP Top 10 coverage.

## Troubleshooting Path

1. Export the configuration:
   `aws wafv2 get-web-acl --scope REGIONAL --name <webacl-prod> --id <id> --region <region>`.
2. Confirm scope: `aws wafv2 list-resources-for-web-acl --web-acl-arn <arn> --resource-type APPLICATION_LOAD_BALANCER`.
3. Check logging: `aws wafv2 get-logging-configuration --resource-arn <arn>`.
4. Pull 30 days of `AllowedRequests` / `BlockedRequests` / `CountedRequests` per rule from the
   `AWS/WAFV2` namespace to find rules that never act and rules that act too much.
5. Find would-be blocks among Count rules (CloudWatch Logs Insights):
   ```
   fields @timestamp, httpRequest.uri, terminatingRuleId
   | filter ispresent(nonTerminatingMatchingRules.0.ruleId)
   | stats count(*) by nonTerminatingMatchingRules.0.ruleId, httpRequest.uri
   | sort count(*) desc
   ```
6. List each managed group's rules and labels:
   `aws wafv2 describe-managed-rule-group --vendor-name AWS --name AWSManagedRulesCommonRuleSet --scope REGIONAL --region <region>`.

## Lab Reproduction Notes

No lab yet. A useful one: EKS (or a plain ALB with two target groups) behind one web ACL, the
Core + SQLi + KnownBadInputs groups in Count, a synthetic traffic generator with legitimate large
bodies and attack payloads, and the label-based exclusion from the Recommendation.

## Learning Exercises

1. Compute the WCU of the recommended rule set and the price impact of adding Bot Control and ATP.
2. Send a 9 KB JSON body with an SQLi string after byte 8,192 to an ALB-protected path. Which rule
   terminates, and what happens if `SizeRestrictions_BODY` is set to Count?
3. Build a rate-based rule keyed on a header and compare it with a per-IP rule for 50 clients behind
   one NAT IP.
4. Turn a Core rule false positive into a label-based exclusion and prove the attack payload is still
   blocked on every other path.

## References

- [Web ACL capacity units (WCU)](https://docs.aws.amazon.com/waf/latest/developerguide/aws-waf-capacity-units.html)
- [AWS managed rule groups list](https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-list.html)
- [Rate-based rule statement](https://docs.aws.amazon.com/waf/latest/developerguide/waf-rule-statement-type-rate-based.html)
- [Oversize web request components](https://docs.aws.amazon.com/waf/latest/developerguide/waf-oversize-request-components.html)
- [Testing and tuning your protections](https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-testing.html)
- [AWS Load Balancer Controller ingress annotations](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/)
