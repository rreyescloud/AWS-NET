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
