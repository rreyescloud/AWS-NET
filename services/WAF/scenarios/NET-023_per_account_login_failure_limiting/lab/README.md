# NET-023 Lab — Step by Step

A fake OAuth token endpoint behind a REGIONAL web ACL. One Region (`us-east-1`), one lab account.
Nothing here touches a real identity provider: the Lambda holds two test users.

```
Client (curl / test) ──▶ API Gateway REST (stage "lab") ──▶ Lambda "NET-023-token" ──▶ DynamoDB counter
                              ▲
                     web ACL "NET-023-token-acl" (REGIONAL)
                       1. RateLimitPerForwardedIP  (100 / 5 min per X-Forwarded-For)
                       2. ATP in Count             (only with --atp)
```

## Before you start

- Use a **lab account only**. The deploy needs IAM, Lambda, API Gateway, DynamoDB, WAF and CloudWatch Logs.
- Configure a named profile for it, then pass it with `--profile`.
- Cost: almost zero without `--atp` (WAF web ACL + 1 rule, a few Lambda calls). **ATP has a monthly
  fee plus a per-request fee.** Run teardown when you finish.

## What `deploy` creates, and what to check in the console

### Step 1 — DynamoDB table `NET-023-login-failures`
- On-demand capacity. Partition key `pk` (string). TTL on attribute `ttl`.
- **Console → DynamoDB → Tables → NET-023-login-failures**
  - *Overview*: capacity mode **On-demand**.
  - *Additional settings → Time to Live*: **On**, attribute `ttl`.
  - *Explore items* (after `test`): keys look like `<sha256 of the username>#<minute number>`, with
    `fails` and `ttl`. The username itself is never stored.

### Step 2 — IAM role `NET-023-token-lambda-role`
- Trust: `lambda.amazonaws.com`. Policies: `AWSLambdaBasicExecutionRole` + an inline policy
  `counter-table` with only `dynamodb:GetItem` and `dynamodb:UpdateItem` on the table.
- **Console → IAM → Roles → NET-023-token-lambda-role → Permissions**: confirm that the inline
  policy's resource is the table ARN, not `*`.

### Step 3 — Lambda `NET-023-token` (python3.12)
- Parses the form-encoded body, normalizes the username (`strip().lower()`), builds the key
  `sha256(username)#minute`.
- If `fails >= 5` in the current minute → **429** + `Retry-After`, and the fake IdP is **not** called.
- On a wrong password → atomic `ADD fails 1`, `ttl = now + 120` → **400 invalid_grant**.
- Every response has `x-lab-source: lambda`. Failures add `x-lab-fails: <n>`.
- **Console → Lambda → NET-023-token**
  - *Code*: read `handler()`; the order is *check counter → check password → increment*.
  - *Configuration → Environment variables*: `TABLE`, `FAIL_LIMIT=5`.
  - *Monitor → View CloudWatch logs* (after `test`): one invocation per request that WAF allowed.

### Step 4 — API Gateway REST API `NET-023-oauth-api`
- Resource `/token`, method `POST`, Lambda proxy integration, stage `lab`.
- **Why REST and not HTTP API:** AWS WAF can be associated with REST API stages, not with HTTP APIs.
- **Console → API Gateway → NET-023-oauth-api**
  - *Resources → /token → POST → Integration request*: type **Lambda proxy**.
  - *Stages → lab*: copy the **Invoke URL**. The test calls `<invoke-url>/token`.
  - *Stages → lab → Web ACL* (after step 7): shows `NET-023-token-acl`.

### Step 5 — Web ACL `NET-023-token-acl` (REGIONAL, default Allow)
- Rule 1 `RateLimitPerForwardedIP`:
  - Rate-based, limit **100 requests / 5 minutes**, aggregation **Forwarded IP** from `X-Forwarded-For`.
  - Scope-down: URI path **ends with** `/token` **and** body (URL-decoded) contains
    `grant_type=password`.
  - Action Block with a **custom 429** response, body `{"error":"rate_limited","source":"waf"}`,
    and header `lab-source: waf`. Behind API Gateway it arrives as-is (no `x-amzn-waf-` prefix), together with `x-amzn-ErrorType: ForbiddenException`.
- Rule 2 `ATP` (with `--atp` only): `AWSManagedRulesATPRuleSet`, login path `/lab/token`, payload
  `FORM_ENCODED`, fields `username` / `password`. Override **Count**, so it only adds labels.
- **Console → WAF & Shield → Web ACLs → Region US East (N. Virginia) → NET-023-token-acl**
  - *Rules*: open rule 1 → check **Forwarded IP**, the scope-down and the custom response.
  - Note the path WAF sees is **`/lab/token`**: the stage is part of the URI. That's why the
    scope-down uses *ends with* `/token`.
  - *Rules → ATP → Edit*: login path and payload type. There is **no response inspection** option
    here: on a REGIONAL web ACL it doesn't exist.

> **Why Forwarded IP in a lab:** the test runs from one machine. Using `X-Forwarded-For` as the
> aggregation key lets one host act as hundreds of "source IPs". In production, keep the source IP
> (or the header your proxy sets) — a client-supplied `X-Forwarded-For` is spoofable.

### Step 6 — WAF logging to `aws-waf-logs-NET-023`
- CloudWatch Logs, 7-day retention. WAF requires the `aws-waf-logs-` prefix.
- **Console → WAF → NET-023-token-acl → Logging and metrics**: destination is the log group.
- **Console → CloudWatch → Log groups → aws-waf-logs-NET-023** (after `test`): one JSON record per
  request with `action`, `terminatingRuleId`, `labels`, and the request headers (including `x-lab-run`).

### Step 7 — Association
- The web ACL is associated with `.../restapis/<api-id>/stages/lab`.
- **Console → WAF → NET-023-token-acl → Associated AWS resources**: the API stage is listed.

## What `test` does, and what you should see

| Phase | Requests | Expected result | Where to confirm |
|---|---|---|---|
| A. Legit login | `alice` + correct password, 1 IP | **200**, `x-lab-source: lambda` | Lambda logs |
| B. Distributed attack | 30 wrong passwords for `bob`, **30 different IPs** | #1-#5 → **400 invalid_grant**; #6-#30 → **429 from lambda**. WAF never blocks: 1 request per IP | DynamoDB item for bob: `fails = 5`. WAF logs: all `ALLOW` |
| C. Real bob, same minute | `bob` + correct password | **429 from lambda** — the lockout also blocks the real user | This is the lockout-as-DoS risk. Wait for the next minute and it returns 200 |
| D. Single-IP flood | random usernames, 1 IP, 4 per second | After ~100 requests plus lag: **429 from waf** (observed: request 153, 88 s) | WAF → *Overview* → rule `RateLimitPerForwardedIP` metric; WAF logs `terminatingRuleId = RateLimitPerForwardedIP` |

Then:

```bash
python deploy_lab.py logs <run-tag>     # action + terminating rule counts; ATP labels if --atp
python deploy_lab.py status             # resources and current counter items
```

### What to look for in the WAF logs (CloudWatch Logs Insights)

```
fields @timestamp, action, terminatingRuleId, httpRequest.clientIp
| filter @message like "<run-tag>"
| sort @timestamp asc
```

- Phase B: every request is `ALLOW`. **WAF let all 30 through.** The 429s came from the Lambda,
  which WAF never sees.
- Phase D: `ALLOW` until the rate-based rule catches up, then `BLOCK` with `RateLimitPerForwardedIP`.
  The gap between request ~100 and the first block is the **mitigation lag**.
- With `--atp`: look for `awswaf:managed:aws:atp:` labels on phase B, for example
  `aggregate:attribute:password_traversal` and `aggregate:volumetric:ip:*`.

### Reading a blocked request (annotated, sanitized)

A phase-D record from the lab run. Identifiers replaced.

```json
{
  "terminatingRuleId": "RateLimitPerForwardedIP",
  "terminatingRuleType": "RATE_BASED",
  "action": "BLOCK",
  "httpSourceName": "APIGW",
  "httpSourceId": "111122223333:<api-id>:lab",
  "ruleGroupList": [],
  "rateBasedRuleList": [{ "limitKey": "FORWARDEDIP", "maxRateAllowed": 100,
                          "evaluationWindowSec": 300, "limitValue": "198.51.100.99" }],
  "nonTerminatingMatchingRules": [],
  "responseCodeSent": 429,
  "httpRequest": {
    "clientIp": "203.0.113.50",
    "headers": [{ "name": "X-Forwarded-For", "value": "198.51.100.99, 203.0.113.50" },
                { "name": "Content-Type", "value": "application/x-www-form-urlencoded" },
                { "name": "Content-Length", "value": "71" }],
    "uri": "/lab/token",
    "httpMethod": "POST"
  }
}
```

- `rateBasedRuleList.limitValue` is the aggregation key that crossed 100 requests in 300 s.
- `X-Forwarded-For` has two hops: the first was set by the test client, the second was appended by
  API Gateway (the real source). **WAF uses the first IP**, which the client controls.
- `clientIp` is the TCP source. ATP aggregates on this one, which is why ATP saw one IP.
- `ruleGroupList` is empty: rule 1 terminated the evaluation, so ATP (priority 2) never ran and no
  labels were added.
- `uri` includes the stage (`/lab/token`). The body is not logged: no credentials in WAF logs.
- `responseCodeSent: 429` is the custom response, not the default 403.

## Experiments after the first run

1. Change `FAIL_LIMIT` in the Lambda environment to 3 and rerun phase B.
2. Deploy with `--atp` and compare the labels for phase B and phase D.
3. Lower `RATE_LIMIT` to 10 and see phase B start to hit WAF (1 IP repeated).
4. Put CloudFront in front, move the web ACL to CLOUDFRONT scope, and enable ATP response
   inspection (status code 400 = failure). Compare how long it takes to block versus the Lambda.

## Teardown

```bash
python deploy_lab.py teardown --profile <lab-profile>
```

Order: disassociate → delete logging → delete web ACL → delete API → Lambda → IAM role → table →
log groups. The last step checks every resource type plus a `Project=NET-023` tag search; it must
print `leftovers: none`.

**Console check after teardown:** WAF → Web ACLs (N. Virginia) has no `NET-023-token-acl`; API
Gateway, Lambda, IAM roles, DynamoDB and CloudWatch Log groups have nothing named `NET-023*`.

## Run History

### 2026-11-02 — first run (ATP enabled)

1. **Deploy, steps 1-6:** created without errors.
2. **Deploy, step 7:** `AssociateWebACL` returned `WAFUnavailableEntityException` for ~100 s. The
   stage existed (`get-stage` returned it) but WAF couldn't see it yet. A retry ~3 minutes after
   stage creation worked. **Fixed:** the script retries for up to 6 minutes.
3. **Test, phases A-C:** exactly as expected (200 · 400×5 then 429×25 from the Lambda · 429 for the
   real user).
4. **Test, phase D:** the script printed "no WAF block after 417 requests", but `logs` showed 157
   requests blocked by `RateLimitPerForwardedIP`. A direct probe returned `429`, body
   `{"error":"rate_limited","source":"waf"}`, header `lab-source: waf` (no `x-amzn-waf-` prefix)
   and `x-amzn-ErrorType: ForbiddenException`. **Fixed:** the script detects both header forms.
   Rerun from a new forwarded IP: first block at request 153, after 88 s.
5. **Logs:** CloudWatch Logs Insights showed part of phase B only after a delay. ATP labels seen:
   `aggregate:volumetric:ip:low` (real source IP), no `password_traversal`.
6. **Teardown:** all steps completed. Independent check per resource type and by tag: nothing left.
