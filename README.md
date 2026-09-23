# AWS Networking & Network Security — Solutions and Knowledge Base

Portfolio of real-world AWS networking and network-security work covering VPC connectivity, Route 53, Network Firewall, and WAF/Shield. Each service carries a documentation set, enterprise case patterns drawn from production support work, and scenarios written up in enough detail to be reproduced.

## Services

- **[VPC](services/VPC/)** — PrivateLink, cross-region private access, Transit Gateway inspection, hybrid connectivity. Docs on endpoints, connectivity, components and troubleshooting.
- **[Route 53](services/R53/)** — ARC failover and Region Switch, multi-region DR, Resolver and Global Resolver, query logging, DNSSEC. Docs on records, resolver, health checks and domains.
- **[Network Firewall](services/NetworkFirewall/)** — Suricata rule development, TLS inspection, centralized egress and TGW inspection models. Docs on rules, deployment models, logging and troubleshooting.
- **[WAF, Shield & Firewall Manager](services/WAF/)** — managed rule tuning, Bot Control, rate-based protection, DDoS response, centralized policy. Docs on rules, logging, integrations and onboarding.

## Scenario Tiers

Scenarios come in two tiers, declared at the top of every scenario README so you know what you are getting before you read it.

- **Lab** — reproducible end to end. Ships a deploy script with a `teardown` path, an architecture diagram, and the results actually observed when it ran.
- **Case analysis** — no deploy script by design. The value is the investigation: problem statement, evidence, root cause and the reasoning that resolved it. Some of these are cases where building a lab would add nothing (a DDoS mitigation comparison, for instance).

## Scenarios

**Lab**

- **[NET-001](services/VPC/scenarios/NET-001_s3_crossaccount/)** — S3 private cross-account cross-region connectivity · Fintech
- **[NET-003](services/R53/scenarios/ARC/NET-003_arc_region_switch_off_fails/)** — Multi-region failover with ARC Region Switch + Aurora Global Database · Financial Services
- **[NET-004](services/NetworkFirewall/scenarios/NET-004_tls_inspection_cross_signed_cert/)** — TLS inspection rejects a cross-signed certificate · Capital Markets
- **[NET-005](services/NetworkFirewall/scenarios/NET-005_asymmetric_routing_regional_natgw/)** — Asymmetric routing with regional NAT Gateway and multi-AZ firewall · Airlines
- **[NET-006](services/WAF/scenarios/NET-006_waf_bot_control_api_chatbot/)** — WAF Bot Control for a public API chatbot · Public Sector
- **[NET-010](services/NetworkFirewall/scenarios/NET-010_container_attributes_referencing/)** — Network Firewall container attributes referencing on EKS · Technology
- **[NET-011](services/NetworkFirewall/scenarios/NET-011_suricata_domain_allowlist_syn_drop/)** — Suricata domain allowlist never matches, catch-all drop kills the SYN · Regulated Financial Services
- **[NET-014](services/WAF/scenarios/NET-014_waf_ai_traffic_monetization_x402/)** — WAF AI traffic monetization over x402 · Content and API providers

**Case analysis**

- **[NET-002](services/R53/scenarios/NET-002_r53_query_logging/)** — Resolver query logging, four recurring problem categories · General
- **[NET-007](services/VPC/scenarios/NET-007_dms_aurora_kafka_latency/)** — DMS CDC latency from Aurora MySQL to Kafka · Accounting SaaS
- **[NET-008](services/R53/scenarios/NET-008_global_resolver_troubleshooting/)** — Global Resolver failure modes · General
- **[NET-009](services/R53/scenarios/NET-009_eks_zonal_shift_recovery_delay/)** — EKS zonal shift recovery delay after expiry · Financial Services
- **[NET-012](services/WAF/scenarios/NET-012_shield_l7am_to_antiddos_amr_migration/)** — Shield Advanced L7AM vs the AntiDDoS managed rule set · Financial Services

## Structure

```
services/
└── <Service>/
    ├── README.md              ← service index, experience summary, scenario list
    ├── docs/                  ← knowledge base
    │   ├── Cases-Worked.md    ← enterprise case patterns by industry
    │   └── <Topic>.md         ← deep dive per topic
    └── scenarios/
        └── NET-XXX_name/
            ├── README.md      ← tier, problem, architecture, findings
            ├── architecture.drawio
            └── lab/           ← Lab tier only
                └── deploy_lab.py   ← deploy | status | test | teardown
```

## Running a Lab scenario

Lab scenarios take a profile and create real, billable resources. Every one of them has a teardown path — use it.

```bash
cd services/<Service>/scenarios/<scenario>/lab
python deploy_lab.py deploy      # create everything
python deploy_lab.py status      # show current state
python deploy_lab.py teardown    # destroy everything
```

The two oldest scenarios (NET-001, NET-003) predate that convention and use separate scripts:

```bash
python deploy.py --profile <your-aws-profile> --account-id <your-account-id>
python cleanup.py --profile <your-aws-profile>
```

Account IDs, resource IDs and public IPs are placeholders throughout. Deployed-state files and run logs are deliberately excluded from this repository.

## Author

Rreyes Cloud — Network & Cloud Security Engineer
