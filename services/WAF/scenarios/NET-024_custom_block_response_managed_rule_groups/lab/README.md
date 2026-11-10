# NET-024 Lab — Step by Step

CloudFront in front of a private S3 origin, with a CLOUDFRONT-scope web ACL. The lab answers one
question per step: **who produced the block response?**

```
Viewer ──▶ CloudFront (caching disabled, custom error responses: none | 403 → /errors/403.html)
              │ verdict?
              ▼
           web ACL "NET-024-portal-acl"  (priorities simulate Firewall Manager)
             10  pre-AWS-CommonRuleSet        managed  ─┐ "preProcess"
             20  pre-AWS-KnownBadInputs       managed   │
             30  pre-BlockManagedMatches      label block (fixB only)
             35  acct-OwnBlockPath            Block + custom response    ─┐ "account rules (retrofit)"
             40  acct-AllowPartner            Allow if x-lab-partner: trusted
             50  post-BlockManagedMatches     label block (fixB-post only)  "postProcess"
                 default action Allow
              ▼
           S3 (OAC): index.html, errors/403.html
```

> **Firewall Manager is not part of `deploy_lab.py`.** It needs an AWS Organizations administrator
> account. The priorities reproduce the order FMS creates (preProcess → account rules → postProcess).
> The `ruleActionOverrides` + custom response question was tested separately with the AWS CLI — see
> [2026-11-04 — Firewall Manager run](#2026-11-04--firewall-manager-run).

## What `deploy` creates, and what to check in the console

| # | Resource | Console check |
|---|---|---|
| 1 | **S3 bucket** `net-024-<account>-<suffix>`, all public access blocked, `index.html` + `errors/403.html` | *S3 → bucket → Permissions*: Block public access = On. *Objects*: the two pages |
| 2 | **Origin Access Control** `NET-024-oac` (sigv4, always sign) | *CloudFront → Origin access* |
| 3 | Label namespaces read from `DescribeManagedRuleGroup` | Printed by deploy; saved in `resources.json` |
| 4 | **Web ACL** `NET-024-portal-acl` (CLOUDFRONT, state `baseline`) with a `blockpage` custom response body | *WAF & Shield → Web ACLs → Global (CloudFront) → Rules* in priority order. *Custom response bodies*: `blockpage` |
| 5 | **WAF logging** to `aws-waf-logs-NET-024` (us-east-1) | *Web ACL → Logging and metrics* |
| 6 | **CloudFront distribution**, S3 origin via OAC, managed policy *CachingDisabled*, web ACL attached, **no custom error responses** | *CloudFront → distribution → Error pages*: empty. *Security*: the web ACL. *Origins*: OAC |
| 7 | **Bucket policy**: `s3:GetObject` only for `cloudfront.amazonaws.com` with `AWS:SourceArn` = this distribution | *S3 → bucket → Permissions → Bucket policy* |

## States

```
python deploy_lab.py set-state <state> --profile <lab-profile>
```

| State | Web ACL | CloudFront error page | What it shows |
|---|---|---|---|
| `baseline` | Managed groups **Block** (no custom response); own rule with custom response | none | The customer's situation today |
| `fixA` | same as baseline | **403 → /errors/403.html** | Precedence level 2: CloudFront covers managed-group blocks |
| `fixB` | Managed groups **Count** (group-level `OverrideAction`) + label block at priority **30** | none | Precedence level 1 for everything, block evaluated before the Allow rule |
| `fixB-post` | same, label block at priority **50** | none | The ordering mistake: the Allow rule wins |

## Probes

```
python deploy_lab.py probe <name>|all --profile <lab-profile>
```

| Probe | Request | Matches |
|---|---|---|
| `ok` | `GET /` | nothing |
| `xss` | `GET /?q=<script>alert(1)</script>` | Core rule set (XSS in query arguments) |
| `badinput` | `GET /?x=${jndi:ldap://example.com/a}` | Known bad inputs (Log4j pattern) |
| `own` | `GET /blocked-own` | own rule with custom response |
| `partner-xss` | `xss` + header `x-lab-partner: trusted` | Core rule set **and** the own Allow rule |
| `missing` | `GET /missing.html` | nothing in WAF — the origin returns 403 (OAC, object doesn't exist) |

The probe prints the status, the **source** of the response (`own-rule`, `label-block`,
`cloudfront-403-page`, `waf-default-403`) and `x-cache`.

## Test plan (run one step at a time)

### Step 1 — `baseline`: reproduce the problem
- Expect: `xss` and `badinput` → 403 **waf-default-403**. `own` → 403 **own-rule**. `missing` → 403 from the origin.
- Console: *Web ACL → Overview / Sampled requests* — terminating rule per request.
- Logs Insights (`aws-waf-logs-NET-024`): the customer's query — managed blocks without `responseCodeSent`.

### Step 2 — `fixA`: CloudFront 403 page
- Expect: `xss` / `badinput` → **cloudfront-403-page**. `own` → still **own-rule** (WAF custom response wins).
  `missing` → **cloudfront-403-page** too (the caveat: origin 403s are replaced).
- Console: *CloudFront → Error pages*: one entry for 403.

### Step 3 — `fixB`: Count + label block at the end of "preProcess"
- Expect: `xss` / `badinput` → **label-block**. `partner-xss` → **label-block** (the block runs before the Allow).
- Logs: `terminatingRuleId = pre-BlockManagedMatches`; the managed rule appears in `labels` / `ruleGroupList`.

### Step 4 — `fixB-post`: the ordering mistake
- Expect: `xss` → **label-block**, but `partner-xss` → **200**: the Allow rule ended evaluation first.

### Step 5 — back to `baseline`, then teardown

## Teardown

```
python deploy_lab.py teardown --profile <lab-profile>
```

Disables the distribution (5-15 min), deletes it, the OAC, WAF logging, the web ACL, the log group and
the bucket. The last step checks every resource type plus a `Project=NET-024` tag search and must
print `leftovers: none`.

Cost while it runs: CloudFront and WAF (one web ACL, a few rules, two managed groups) — a few cents a
day at lab volume. No paid managed groups are used.

## Run History

### 2026-11-02 — first run

1. **Deploy:** bucket, OAC, label namespaces (`awswaf:managed:aws:core-rule-set:`,
   `awswaf:managed:aws:known-bad-inputs:`), web ACL `baseline`, logging, distribution (~10 min to
   *Deployed*), bucket policy. No probes during deploy.
2. **Step 1 `baseline`:** managed-group blocks → CloudFront's generic 403 page, no `responseCodeSent`;
   own rule → `blockpage`, `responseCodeSent=403`; missing object → S3 `AccessDenied` XML with WAF ALLOW.
3. **Step 2 `fixA`:** managed-group blocks and the missing object → `/errors/403.html`
   (`server: AmazonS3`, fetched from the origin); own rule → still the WAF `blockpage`. WAF logs
   identical to step 1; no WAF record for `/errors/403.html`.
4. **Step 3 `fixB`:** all WAF blocks → `blockpage`, terminating rule `pre-BlockManagedMatches`,
   `responseCodeSent=403`; partner + XSS blocked; missing object back to S3 XML.
5. **Step 4 `fixB-post`:** XSS still blocked, partner + XSS → **200** (`acct-AllowPartner` terminated first).
6. **Teardown:** completed; independent check per resource type (distribution, OAC, web ACL, bucket, log group) and tag search: nothing left.

### 2026-11-04 — Firewall Manager run

Manual, AWS CLI, two accounts in one AWS Organization: `<ACCOUNT_SECURITY>` (management + FMS
administrator) and `<ACCOUNT_APP>` (member, AWS Config recording in us-east-1). Everything tagged
`lab=<tag>`; the FMS policy is scoped by that resource tag so it doesn't touch other distributions.

1. **Member:** web ACL (CLOUDFRONT, default Allow, no rules) with custom response body `blockpage`;
   CloudFront distribution (custom origin, web ACL attached, tagged).
2. **Admin — policy A:** WAFV2, `RETROFIT_EXISTING`, preProcess = `AWSManagedRulesAdminProtectionRuleSet`
   with
   ```json
   "ruleActionOverrides": [{ "name": "AdminProtection_URIPATH",
     "actionToUse": { "block": { "customResponse": { "responseCode": 403, "customResponseBodyKey": "blockpage" } } } }]
   ```
   plus `AWSManagedRulesCommonRuleSet` with no override (control).
   - `put-policy` → success. `get-policy` → `"actionToUse": {"block": {}}`.
   - Retrofitted web ACL → `RuleActionOverrides: [{"Name": "AdminProtection_URIPATH", "ActionToUse": {"Block": {}}}]`.
   - `GET /admin` and `GET /` without User-Agent → 403 **default CloudFront page**.
3. **Probe A':** same policy + `customResponseBodies` in `ManagedServiceData` → the field is not in
   the stored policy. Stored keys: `type`, `preProcessRuleGroups`, `postProcessRuleGroups`,
   `defaultAction`, `customRequestHandling`, `customResponse`, `overrideCustomerWebACLAssociation`,
   `loggingConfiguration`, `sampledRequestsEnabledForDefaultActions`, `webACLSource`.
4. **Admin — policy C:** rule group `label-block` (CLOUDFRONT, capacity 10) in the administrator
   account: `OrStatement` of `LabelMatchStatement` NAMESPACE `awswaf:managed:aws:admin-protection:`
   and `awswaf:managed:aws:core-rule-set:`, Block with `CustomResponse` 403 + body key `rgpage`,
   **`CustomResponseBodies.rgpage` defined in the rule group**. Policy updated: both managed groups
   `overrideAction: COUNT`, rule group appended as the third preProcess group.
   - Retrofitted web ACL → preProcess: AdminProtection (Count) · CommonRuleSet (Count) · label-block (None).
   - `GET /admin` and `GET /` without User-Agent → 403 **custom page from the rule group**. Normal `GET /` → 200.
   - Block response headers still include `server: CloudFront`, `via`, `x-cache`, `x-amz-cf-id`.
5. **Teardown order:** delete the FMS policy → wait until the FMS-managed preProcess groups leave the
   web ACL → disable and delete the distribution → delete the web ACL → delete the rule group.
