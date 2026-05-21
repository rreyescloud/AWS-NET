# AWS Networking — Enterprise Solutions & Knowledge Base

Portfolio of real-world AWS networking solutions covering VPC connectivity, Route 53, Network Firewall, and WAF. Each service includes documentation, enterprise case patterns, and reproducible lab scenarios with infrastructure-as-code.

## Services

| Service | Docs | Scenarios | Focus |
|---|---|---|---|
| [VPC](services/VPC/) | Endpoints, Connectivity, Components, Troubleshooting | NET-001 | PrivateLink, cross-region, private architectures |
| [Route 53](services/R53/) | DNS, Resolver, Health Checks, ARC, Region Switch | NET-002, NET-003 | ARC failover, multi-region DR, query logging |
| [Network Firewall](services/NetworkFirewall/) | Rules, Deployment, TLS Inspection, Logging | NET-004 | Suricata, TLS inspection, centralized inspection |
| [WAF](services/WAF/) | Rules, Bot Control, Shield, Firewall Manager | — | DDoS protection, rate limiting, compliance |

## Structure

```
services/
├── <Service>/
│   ├── README.md              ← Index + Overview
│   ├── docs/                  ← Knowledge base
│   │   ├── Cases-Worked.md   ← Enterprise case patterns by industry
│   │   ├── <Topic>.md        ← Deep-dive per topic
│   │   └── ...
│   └── scenarios/             ← Reproducible labs
│       └── NET-XXX_name/
│           ├── README.md      ← Problem, architecture, step-by-step
│           ├── deploy.py      ← Create all infrastructure
│           ├── cleanup.py     ← Teardown to avoid costs
│           └── architecture.drawio
```

## How to Use

Each scenario is self-contained and reproducible:

```bash
cd services/<Service>/scenarios/<scenario-folder>
python deploy.py --profile <your-aws-profile> --account-id <your-account-id>
```

To clean up:

```bash
python cleanup.py --profile <your-aws-profile>
```

## Author

Rreyes Cloud — Cloud Network Engineer
