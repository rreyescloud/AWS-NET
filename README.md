# AWS Networking — Troubleshooting Cases & Enterprise Solutions

Portfolio of real-world AWS networking cases covering VPC connectivity, Route 53, WAF, and Network Firewall. Each case includes architecture diagrams, step-by-step implementation, infrastructure-as-code (deploy/cleanup scripts), and lessons learned from production environments.

## Cases

| ID | Service | Title | Industry |
|---|---|---|---|
| NET-001 | VPC | [S3 Private Cross-Account Cross-Region Connectivity](cases/VPC/NET-001_s3_crossaccount/) | Fintech |
| NET-002 | Route 53 | [Resolver Query Logging — Common Issues](cases/R53/NET-002_r53_query_logging/) | General |
| NET-003 | Route 53 / ARC | [ARC Region Switch + Aurora Global Database Failover](cases/R53/ARC/NET-003_arc_region_switch_off_fails/) | Financial Services |

## What Each Case Includes

- **README.md** — Problem statement, business context, architecture, step-by-step implementation, things not to do, and references
- **architecture.drawio** — Visual diagrams (open with draw.io or diagrams.net)
- **deploy.py** — Infrastructure-as-code to reproduce the entire environment
- **cleanup.py** — Teardown script to avoid ongoing costs
- **correspondence/** — Example support communications (when applicable)

## Services Covered

- **VPC** — PrivateLink, Gateway/Interface Endpoints, cross-region connectivity, private-only architectures
- **Route 53** — ARC (Application Recovery Controller), Region Switch, routing controls, health checks, failover DNS, Resolver Query Logging
- **Aurora** — Global Database, managed switchover/failover, multi-region DR

## How to Use

Each case is self-contained. To reproduce:

```bash
cd cases/<service>/<case-folder>
python deploy.py --profile <your-aws-profile> --account-id <your-account-id>
```

To clean up after testing:

```bash
python cleanup.py --profile <your-aws-profile>
```

## Author

Rodrigo Chias — Cloud Network Engineer
