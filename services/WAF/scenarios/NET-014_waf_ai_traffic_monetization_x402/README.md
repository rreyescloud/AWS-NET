# NET-014: AWS WAF AI Traffic Monetization (x402)

**Tier:** Lab — reproducible end to end
**Status:** Lab Reproduced — full 402 → pay → 200 loop verified on-chain (Base Sepolia)

## Objective

Explore and validate the **AWS WAF AI traffic monetization** feature (launched June 15, 2026): a Bot Control capability that lets content/API providers **charge AI bots and agents** for access to protected resources, using the **x402 protocol** (HTTP 402 + on-chain USDC micropayments). The feature is largely **console-driven** — the `Monetize` action is not yet exposed in this environment's `wafv2` CLI, which is precisely why it is interesting to reproduce hands-on.


## Background — What the feature does

AI traffic monetization builds on AWS WAF Bot Control. Historically, bot management was binary — allow or block. This adds a third option: **monetize**. When an AI bot requests a monetized resource, WAF returns `HTTP 402 Payment Required` with pricing and payment instructions; the bot signs a micropayment; WAF verifies it at the edge and serves the content — all within a single request cycle.

- Available globally with **Amazon CloudFront** at no additional charge beyond standard AWS WAF pricing.
- Payments denominated in **USDC**; settlement on the **Base** blockchain (mainnet) or **Base Sepolia** (testnet).
- Differentiated pricing by **agent identity and intent** (e.g., verified vs. unverified/training crawlers), driven by Bot Control labels.


## The x402 protocol (under the hood)

x402 (developed by Coinbase) revives the dormant HTTP `402 Payment Required` status code as a programmable payment rail for autonomous agents. End-to-end flow:

1. Agent requests a protected resource.
2. Server (AWS WAF at the edge) returns **HTTP 402** + a payment specification (price, accepted networks, license terms).
3. Agent signs a **USDC micropayment authorization** — signature standard **EIP-3009 (Transfer With Authorization)**. This is **gasless for the payer**.
4. Agent resubmits with a **payment proof** header (`PAYMENT-SIGNATURE`).
5. A **facilitator** (Coinbase x402 Facilitator) verifies the signature and settles the payment on-chain; WAF serves the content.

Reported characteristics: sub-2-second settlement, ~$0.0001/tx, immutable on-chain audit trail, platform-agnostic (any HTTP-402-capable client: LangChain, CrewAI, AutoGen, Strands, or a plain library client).

### Two sides of the transaction

| Side | Role | AWS building blocks |
|------|------|---------------------|
| Payer / agent | Discovers services, evaluates cost, signs & submits payment | Bedrock AgentCore + AgentCore Payments; or any x402 client library |
| Provider / seller | Gates content, returns 402, verifies payment, serves content | CloudFront + AWS WAF + Lambda@Edge |

**AWS WAF AI traffic monetization is the fully managed provider side** — you don't write the Lambda@Edge verification yourself.


## Lab environment

- **Account:** <LAB_ACCOUNT_ID> (Admin role in the lab account), region us-east-1 (CLOUDFRONT scope).
- **CloudFront distribution:** `<CF_DISTRIBUTION_ID>` (`<distribution-domain>.cloudfront.net`), origin = KMS-encrypted S3 bucket `sa003-documents-<LAB_ACCOUNT_ID>`.
- **Web ACL:** `waf-monetization-lab`
  - Rule 0: `AWS-BotControl` (managed `AWSManagedRulesBotControlRuleSet`, `InspectionLevel=TARGETED`, ML enabled) — identifies and labels AI bots.
  - Rule 1: `Monetize-AI-Bots-402` — LabelMatch `awswaf:managed:aws:bot-control:bot:category:ai` → **native `Monetize` action** (set in console).
- **MonetizationConfig (console):** CurrencyMode `TEST`, chain **Base Sepolia** (`eip155:84532`), base price **0.001 USDC**, payTo = recipient testnet wallet.
- **Two testnet wallets** (throwaway, generated locally with `eth-account`): a PAYER (bot) and a RECIPIENT (publisher/payTo). They **must be different** (see findings).


## How the lab was built

The CLI-reproducible parts (Web ACL, Bot Control, association, logging) are in `lab/deploy_lab.py`. The monetization config + `Monetize` rule are console-only. The payment client is `lab/x402_pay_client.py`.

```
# 1. Create Web ACL + Bot Control + associate + logging (CLI)
python lab/deploy_lab.py --distribution-id <ID>

# 2. Console: configure monetization (Test / Base Sepolia / 0.001 USDC / payTo wallet),
#    add a rule matching bot:category:ai with the Monetize action.

# 3. Fund a SEPARATE payer wallet with testnet USDC at https://faucet.circle.com

# 4. Run the payment client
python lab/x402_pay_client.py
```


## Test results — the three levels

### Level 1 — Payment challenge (402). PASSED.

An AI-crawler request receives a 402 with the x402 challenge; a browser does not.

```bash
URL="https://<distribution-domain>.cloudfront.net/documents/premium.txt"
curl -si -A "GPTBot/1.2" "$URL" | head -1          # -> HTTP/2 402
curl -s -o /dev/null -w "%{http_code}\n" -A "Mozilla/5.0 Chrome/120" "$URL"   # -> 200 (allowed) / 403 pre-fix
```

Decoded `payment-required` header:
```json
{
  "accepts": [{
    "amount": "1000",                                      // 0.001 USDC (6 decimals)
    "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e", // USDC on Base Sepolia
    "network": "eip155:84532",                             // 84532 = Base Sepolia chain ID
    "payTo": "0x544D...Fc00",                              // recipient wallet
    "scheme": "exact"
  }],
  "error": "PAYMENT-SIGNATURE header is required",
  "x402Version": 2
}
```

### Level 2 — Logs / analytics confirm the MONETIZE action. PASSED.

Web ACL logging (CloudWatch group `aws-waf-logs-monetization-lab`) shows:
```
action=MONETIZE  terminatingRuleId=Monetize-AI-Bots-402  UA=GPTBot
action=ALLOW     terminatingRuleId=Default_Action         UA=Mozilla/... (browser)
```
Bot Control labels on the GPTBot request:
```
awswaf:managed:aws:bot-control:bot:category:ai          <- matches the Monetize rule
awswaf:managed:aws:bot-control:bot:name:gptbot          <- specific crawler
awswaf:managed:aws:bot-control:bot:unverified           <- verified-vs-unverified pricing dimension
awswaf:managed:aws:bot-control:signal:non_browser_user_agent
```

### Level 3 — Full pay → 200 loop. PASSED (on-chain settlement confirmed).

Minimal x402 client (`x402` v2.16 + `eth-account`, no Bedrock/CDK/Docker):
```
[1] GET (as bot)      -> HTTP 402 (payment challenge)
[2] Signed EIP-3009   -> attach PAYMENT-SIGNATURE
[3] GET + PAYMENT     -> HTTP 200 | content served
PAYMENT-RESPONSE: { "success": true,
  "payer": "0x9C0C...DD71",
  "transaction": "0x63d7...3245",
  "network": "eip155:84532" }
```
On-chain proof (Base Sepolia): payer 20.0 → 19.999 USDC, recipient 20.0 → 20.001 USDC. Tx SUCCESS, block 44445895.


## Key findings

1. **`Monetize` action is not in this environment's `wafv2` CLI.** `get-web-acl` renders the rule's `Action` as empty `{}`, and `MonetizationConfig` does not appear. It must be enabled from the AWS WAF console. This is the defining trait of the scenario: a feature you cannot (yet) fully drive from the CLI.

2. **`self_send_not_allowed`.** The first payment attempt signed from the same wallet configured as `payTo`. The facilitator rejects paying yourself. **The payer (bot) wallet must differ from the recipient (publisher) wallet.**

3. **Post-payment 403 was a KMS problem, not x402.** After a valid payment, WAF forwarded to the origin but S3 returned `AccessDenied`. Root cause: the S3 bucket is **KMS-encrypted**, and the KMS key policy did not grant `kms:Decrypt` to the CloudFront service principal. A normal browser hit the same 403, proving the issue was origin-side. Fix: add a `kms:Decrypt` statement to the KMS key policy scoped to the distribution `AWS:SourceArn`.

4. **WAF only settles the payment when the origin returns 200.** While S3 returned 403, no USDC moved; once it served 200, settlement went through. WAF does not charge for content it cannot deliver.

5. **The `exact` scheme is gasless for the payer** (EIP-3009 Transfer With Authorization) — the bot only signs; the facilitator submits and pays gas on-chain. This is why the payment worked with 0 ETH in the payer wallet.


## Files

- **`README.md`** — this document
- **`lab/deploy_lab.py`** — CLI-reproducible setup: Web ACL + Bot Control + association + logging
- **`lab/x402_test.sh`** — Level 1 only: decodes the 402 payment challenge, no wallet needed
- **`lab/x402_pay_client.py`** — Level 3: full x402 payment client (402 → sign EIP-3009 → 200)
- **`lab/requirements.txt`** — pinned deps for the payment client


## Cleanup

```bash
# 1. Disassociate: update-distribution setting WebACLId="" (with current ETag)
# 2. aws wafv2 delete-logging-configuration --resource-arn <web-acl-ARN> --region us-east-1
# 3. aws logs delete-log-group --log-group-name aws-waf-logs-monetization-lab --region us-east-1
# 4. aws wafv2 delete-web-acl --name waf-monetization-lab --scope CLOUDFRONT --id <id> --lock-token <token> --region us-east-1
# 5. (optional) remove the kms:Decrypt statement added to the S3 bucket's KMS key policy
```

Ongoing cost while running: Bot Control Targeted (~$10/mo per Web ACL + per-request inspection) + standard WAF request charges. Monetization itself is no additional charge.


## References

- [AWS WAF announces AI traffic monetization (What's New)](https://aws.amazon.com/about-aws/whats-new/2026/06/aws-waf-ai-traffic-monetization/)
- [AI traffic monetization (AWS WAF Developer Guide)](https://docs.aws.amazon.com/waf/latest/developerguide/waf-ai-traffic-monetization.html)
- [Getting started with AI traffic monetization](https://docs.aws.amazon.com/waf/latest/developerguide/waf-ai-traffic-monetization-getting-started.html)
- [AWS WAF Bot Control](https://docs.aws.amazon.com/waf/latest/developerguide/waf-bot-control.html)
- [x402 and agentic commerce (AWS Blog)](https://aws.amazon.com/blogs/industries/x402-and-agentic-commerce-redefining-autonomous-payments-in-financial-services/)
- [sample-agentcore-cloudfront-x402-payments (aws-samples)](https://github.com/aws-samples/sample-agentcore-cloudfront-x402-payments)
- [EIP-3009: Transfer With Authorization](https://eips.ethereum.org/EIPS/eip-3009)
- [Circle testnet faucet (Base Sepolia USDC)](https://faucet.circle.com)

## Related

- [NET-006](../NET-006_waf_bot_control_api_chatbot/) — WAF Bot Control for Public API Chatbot, the Bot Control foundation this feature builds on.
