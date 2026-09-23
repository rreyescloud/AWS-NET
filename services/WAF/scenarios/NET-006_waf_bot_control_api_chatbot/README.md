# NET-006: WAF Bot Control for Public API Chatbot

**Tier:** Lab — reproducible end to end
**Status:** Responded + Lab Reproduced

## Objective

Determine the recommended WAF configuration for protecting a public-facing REST API chatbot, specifically how to use Bot Control without emitting Challenge/CAPTCHA responses that are incompatible with API calls.


## Business Context — European Government / Public Services

European government institution providing digital public services to EU citizens. They launched a public chatbot (no authentication required) powered by Amazon Bedrock Knowledge Bases and the Nova Lite model. The chatbot is accessible to all EU citizens, making it a high-value target for bots, scrapers, and automated abuse.

Key requirements:
- **Public access** — No login/auth required, open to all EU citizens
- **API-based** — REST API through API Gateway, not a traditional web page
- **AI-powered** — Bedrock + Nova Lite (cost implications of bot abuse: every request = inference cost)
- **EU data residency** — Hosted in EU region
- **Bot protection** — Must prevent automated abuse without breaking legitimate API consumers


## Problem Statement

Customer has a solid WAF baseline (IP Reputation, Common Rules, Known Bad Inputs, Rate Limiting) but when they enabled Bot Control with Targeted inspection + ML, it emitted CAPTCHA/Challenge responses. Since the frontend consumes a REST API (not rendered HTML), these challenges appear as errors — the browser's fetch/XHR cannot execute the WAF challenge JavaScript automatically.

### Current WAF Configuration (Global, CloudFront scope):
```
1. AWSManagedRulesAmazonIpReputationList
2. AWSManagedRulesCommonRuleSet
3. AWSManagedRulesKnownBadInputsRuleSet
4. Rate-based rule: 200 requests/IP
5. AWSManagedRulesBotControlRuleSet (DISABLED — causes errors)
```

### Architecture:
```
EU Citizens (browser) → CloudFront [Global WAF] → API Gateway [Regional WAF] → Lambda → Bedrock
```

### The Challenge Problem:
1. WAF Bot Control detects uncertain traffic
2. Emits HTTP 202 + HTML/JavaScript challenge
3. Browser's fetch() receives HTML instead of expected JSON
4. Frontend treats it as an error
5. User sees failure instead of chatbot response


## Key Questions

1. How to use Bot Control for APIs without Challenge/CAPTCHA?
2. Can the WAF JS SDK solve this for a web-app-to-API pattern?
3. What additional rules should protect a public AI chatbot endpoint?
4. How to balance bot protection with API usability?


## Research Findings

### Solution 1: WAF JavaScript SDK (Recommended for web-app-to-API)

If the chatbot frontend is a **web app** (browser-based), integrate the AWS WAF JavaScript SDK. It acquires a token silently (no visible challenge) and includes it in all API calls:

```html
<head>
  <script type="text/javascript" src="<WebACL-integration-URL>/challenge.js" defer></script>
</head>
```

```javascript
// Instead of fetch(), use AwsWafIntegration.fetch()
const response = await AwsWafIntegration.fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: userInput })
});
```

How it works:
1. SDK loads in background → runs silent browser challenge
2. Acquires `aws-waf-token` cookie automatically
3. `AwsWafIntegration.fetch()` includes the token in every request
4. WAF validates token → Bot Control doesn't emit Challenge (already has proof)
5. If token expires, SDK refreshes silently

Requirements:
- Frontend must be served over HTTPS
- Token domain must match the API domain (or be configured via token domain list)
- Integration URL available in WAF console: Web ACL → Application integration → JavaScript SDK

### Solution 2: Bot Control in COUNT Mode + Label-Based Rules

If SDK integration isn't feasible, use Bot Control's **labels** without its default actions:

1. Set ALL Bot Control rules to **Count** (override all actions)
2. Bot Control still evaluates and **labels** every request
3. Create custom rules AFTER Bot Control that match labels → Block

Example custom rule:
```
Rule: block-bad-bots
  Condition: Has label "awswaf:managed:aws:bot-control:bot:category:monitoring"
             OR Has label "awswaf:managed:aws:bot-control:bot:category:scraper"
             OR Has label "awswaf:managed:aws:bot-control:signal:automated_browser"
  Action: Block
```

This gives you Bot Control's detection WITHOUT challenges — you choose which labels trigger blocks.

Key Bot Control labels:
- `awswaf:managed:aws:bot-control:bot:verified` — known good bots (Google, etc.)
- `awswaf:managed:aws:bot-control:bot:unverified` — claims to be a bot but unverified
- `awswaf:managed:aws:bot-control:bot:category:scraper` — web scrapers
- `awswaf:managed:aws:bot-control:bot:category:ai` — AI crawlers
- `awswaf:managed:aws:bot-control:signal:automated_browser` — headless browsers
- `awswaf:managed:aws:bot-control:signal:non_browser_user_agent` — non-browser UA

### Solution 3: Additional Rules for AI Chatbot Protection

Beyond Bot Control, recommend these for a public chatbot:

1. **Geographic restriction** — If only EU citizens, block non-EU geos:
   ```
   NOT (geo IN [DE, FR, IT, ES, NL, BE, AT, ...all EU]) → Block
   ```

2. **Request body size limit** — Chatbot messages shouldn't be huge:
   ```
   Body size > 4KB → Block
   ```

3. **Rate limiting per path** — More granular than 200/IP global:
   ```
   URI path = "/api/chat" AND rate > 50 per 5min per IP → Block
   ```

4. **Block missing User-Agent** — Legitimate browsers always send UA:
   ```
   Header "User-Agent" size = 0 → Block
   ```

5. **Block known AI scrapers by UA** — GPTBot, CCBot, etc.:
   ```
   User-Agent contains "GPTBot" OR "CCBot" OR "anthropic-ai" → Block
   ```

6. **Token required rule** — If using SDK (Solution 1):
   ```
   URI path = "/api/chat" AND no valid aws-waf-token → Block
   ```
   This is the strongest protection — only browsers that ran the SDK can call the API.


## Recommended Configuration (Final)

```
Priority  Rule                                    Action
───────────────────────────────────────────────────────────────
1         Geo restriction (EU only)               Block
2         AWSManagedRulesAmazonIpReputationList    Block
3         AWSManagedRulesCommonRuleSet             Block
4         AWSManagedRulesKnownBadInputsRuleSet     Block
5         Rate limit: 200/IP (global)              Block
6         Rate limit: 50/IP for /api/chat          Block
7         Block no User-Agent                      Block
8         Block AI scrapers (UA match)             Block
9         Request body size > 4KB                  Block
10        AWSManagedRulesBotControlRuleSet          Count (labels only)
11        Custom: block bad bot labels             Block
12        Token required for /api/chat (if SDK)    Block
```


## Lab Reproduction

Successfully reproduced in us-east-1 (account <LAB_ACCOUNT_ID>) on 2026-05-23.

### Architecture Deployed
```
S3 (index.html) → CloudFront (EFHZDFAPB6QBH) → [Global WAF] → API Gateway → Lambda → Bedrock Nova Lite
                   d21za96d8qf5pb.cloudfront.net
                   Path /     → S3 origin (frontend)
                   Path /chat → API Gateway origin (POST to chatbot)
```

### Test Results — WAF Rules

| Attack | Payload | Result | Rule |
|--------|---------|--------|------|
| Legitimate request | Normal message + Chrome UA | 200 ALLOW | — |
| XSS | `<script>alert(document.cookie)</script>` | 403 BLOCK | CrossSiteScripting_BODY |
| No User-Agent | Empty UA header | 403 BLOCK | NoUserAgent_Header |
| Bot UA (python-requests) | UA: python-requests/2.28.0 | 200 ALLOW (COUNT) | CategoryHttpLibrary (labeled) |
| Path traversal | `?file=../../../etc/passwd` | 403 BLOCK | CRS/BadInputs |
| Oversized body | 10KB body | 403 BLOCK | SizeRestrictions_BODY |
| Rate limit burst | 10 rapid requests | 200 ALLOW | Below 200 threshold |

### Test Results — Bot Control Modes

| Mode | Bot (python-requests) | Browser (Chrome) | Multiple requests no token |
|------|----------------------|------------------|---------------------------|
| COUNT (Common) | 200 + labels | 200 | 200 (all pass) |
| BLOCK (Common) | 403 Block | 200 | 200 (browser UA trusted) |
| BLOCK (Targeted+ML) | 403 Block | 200 (first 2) then **202 CHALLENGE** | 202 CHALLENGE |

### The Bug Reproduced

With Bot Control in **Targeted + ML + Block**:
```
Request 1: 200 ALLOW (tolerance window)
Request 2: 200 ALLOW (tolerance window)
Request 3: 202 CHALLENGE ← starts here
Request 4: 202 CHALLENGE
Request 5: 202 CHALLENGE

WAF Log:
  Action: CHALLENGE
  Rule: TGT_VolumetricIpTokenAbsent
  Labels: [absent, token_absent, absent]
```

**`TGT_VolumetricIpTokenAbsent`** = "Multiple requests from this IP without a WAF token → Challenge to verify"

The Challenge returns HTTP 202 with empty/HTML body. A `fetch()` API call receives this instead of JSON → frontend treats it as error → user sees failure.

### Key Finding: WAF Labels in Logs

```
BLOCK   python-requests/2.28.0   CategoryHttpLibrary   [http_library, unverified, python_requests, non_browser_user_agent]
ALLOW   Mozilla/5.0 (Chrome)     —                     [absent, absent]
CHALLENGE  Mozilla/5.0 (Chrome)  TGT_VolumetricIpTokenAbsent  [absent, token_absent, absent]
```

- `absent` = no aws-waf-token (hasn't passed JS SDK challenge)
- `token_absent` = Targeted rule specifically flags this
- `non_browser_user_agent` = UA doesn't match known browsers
- `http_library` = identified as programmatic HTTP client

### Lab Resources
See `lab/resources.json` for all resource IDs.
Deploy: `python lab/deploy_lab.py deploy`
Teardown: `python lab/deploy_lab.py teardown`


## References

[1] Bot Control deployment guide
https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control-deploying.html

[2] WAF Tokens and intelligent threat mitigation
https://docs.aws.amazon.com/waf/latest/developerguide/waf-tokens.html

[3] JavaScript integration SDK
https://docs.aws.amazon.com/waf/latest/developerguide/waf-javascript-api.html

[4] Client application integrations
https://docs.aws.amazon.com/waf/latest/developerguide/waf-application-integration.html

[5] Bot Control rule group reference
https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-bot.html

[6] Bot Control examples
https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control-examples.html
