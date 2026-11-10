# WAF — Web Application Firewall

## Enterprise Experience

Delivered WAF, Shield Advanced, and Firewall Manager solutions for enterprise customers across financial services, education, transportation, e-commerce, entertainment, and multi-account organizations. Specialized in managed rule optimization, rate-based protection, bot control, and centralized security policy enforcement at scale.

Key highlights:
- Resolved **false positives in managed rules** (SQLi_COOKIE, XSS_BODY, AdminProtection) using scope-down statements and label-based exceptions
- Identified **EKS Ingress Controller overwriting WAF associations** — documented fix with WAF annotations
- Discovered **undocumented VPC Endpoint policy action** blocking WAF-to-ALB association
- Managed **WAF Classic to V2 migration** for banking customers before Sep 2025 deadline
- Configured **Bot Control and ATP/ACFP** for fraud prevention on student enrollment portals
- Documented **rate-based rule behavior** — 5-min minimum window, IP block duration, URI encoding bypass

102+ cases across 9+ industries | WAF Rules (39%), Shield (22%), Firewall Manager (18%)

---

## Documentation

- **[WAF Overview](docs/WAF.md)** — Web ACLs, rules, managed rules, scope, resource association
- **[Rules](docs/Rules.md)** — rate-based, managed rules, custom rules, labels, Bot Control
- **[Logging](docs/Logging.md)** — destinations, sampling, metrics, cost estimation
- **[Integrations](docs/Integrations.md)** — CloudFront, ALB, API Gateway, EKS, AppSync
- **[Troubleshooting](docs/Troubleshooting.md)** — false positives, blocking issues, log analysis
- **[Customer Onboarding](docs/Customer-Onboarding.md)** — migration guide, deployment patterns
- **[Cases Worked](docs/Cases-Worked.md)** — enterprise patterns by industry
- **[Shield](Shield/)** — DDoS protection, Shield Advanced, SRT
- **[Firewall Manager](FirewallManager/)** — centralized policies, cross-account, import

## Scenarios

- **[NET-006](scenarios/NET-006_waf_bot_control_api_chatbot/)** — WAF Bot Control for a public API chatbot · Public Sector · *Lab*
- **[NET-014](scenarios/NET-014_waf_ai_traffic_monetization_x402/)** — AI traffic monetization over x402 · Content and API providers · *Lab*
- **[NET-022](scenarios/NET-022_bot_control_llm_crawler_label_allow/)** — Bot Control blocks LLM crawlers despite a label-based allow (unpinned version, misnamed BLOCK group) · Healthcare Data & Analytics · *Lab*
- **[NET-023](scenarios/NET-023_per_account_login_failure_limiting/)** — Per-account login failure limiting on a custom OAuth token endpoint (why WAF can't count failures, ATP response inspection CloudFront-only) · SaaS · *Lab*
- **[NET-024](scenarios/NET-024_custom_block_response_managed_rule_groups/)** — One custom block response for every block, including AWS managed rule groups (response precedence, CloudFront 403 page, Count + label block, FMS retrofit ordering, FMS drops custom responses in overrides) · Banking · *Lab*
- **[NET-025](scenarios/NET-025_webacl_architecture_review_eks_alb/)** — Web ACL architecture review for EKS ingress ALBs (WCU and rule order, rate-based keys, OWASP Top 10 coverage, 8 KB ALB body limit, label-based false-positive tuning) · Banking Software · *Case analysis*
- **[NET-026](scenarios/NET-026_automated_vulnerability_scan_cloudfront_waf_layers/)** — Automated vulnerability scanning through CloudFront (layered managed rules, rate-based and trap-path rules, the slow distributed scan gap, origin bypass, log-based impact analysis) · Agriculture Technology · *Case analysis*
- **[NET-027](scenarios/NET-027_sqli_managed_rule_blocks_jql_query_args/)** — SQLi managed rule blocks issue-tracker JQL/ZQL in query arguments (change-history diff vs "nothing changed", CloudFront edge 403 in a HAR, Count + label exception scoped by path) · Pension Administration Software · *Case analysis*
- **[NET-028](scenarios/NET-028_waf_log_filter_keep_block_count/)** — WAF logging filter that drops benign ALLOW traffic but keeps BLOCK and every Count match (first-match filter order, COUNT vs EXCLUDED_AS_COUNT, fully qualified label conditions, CAPTCHA/CHALLENGE gap, Firehose/S3 cost sizing) · Media / Video Streaming · *Case analysis*
- **[NET-012](scenarios/NET-012_shield_l7am_to_antiddos_amr_migration/)** — Shield Advanced L7AM vs the AntiDDoS managed rule set · Financial Services · *Case analysis*

## Industries Served

- **Financial Services / Banking** — Shield Advanced for payment APIs; WAF Classic to V2 migration for PCI-DSS
- **Education** — ACFP for student enrollment fraud prevention; managed rule false positives on content platforms
- **Transportation** — Geo-restriction rules hitting 276 statement container limit
- **E-commerce** — CloudFront geo-restriction accuracy (MaxMind 99.8%); Bot Control cost analysis
- **Entertainment / Ticketing** — FWM-managed WebACLs on CloudFront distributions
- **Enterprise (Multi-Account)** — Firewall Manager cross-account enforcement; Terraform state drift; Shield migration between Organizations

---

## Shield

See [Shield/](Shield/) for DDoS protection documentation and cases.

## Firewall Manager

See [FirewallManager/](FirewallManager/) for centralized policy management documentation and cases.
