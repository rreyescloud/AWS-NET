# Firewall Manager — Centralized Security Policy Management

## Enterprise Experience

Implemented Firewall Manager for enterprise customers requiring centralized WAF, Shield, and Network Firewall policy enforcement across multi-account AWS Organizations.

Key highlights:
- Guided **import of existing Network Firewall resources** into FWM for centralized management
- Resolved **Terraform state drift** when FWM modifies CloudFront resources outside Terraform control (known issue #31834)
- Configured **cross-account WAF logging** to centralized S3 bucket
- Identified **AWS Config dependency** — FWM relies on Config for compliance detection
- Managed **FMS Admin disassociation** from suspended accounts

18+ cases

## Documentation

| Topic | Description |
|---|---|
| [FWM Overview](docs/FWM.md) | Policies, cross-account, import, Organizations integration |
| [Cases Worked](docs/Cases-Worked.md) | Policy management, Terraform, cross-account patterns |

## Scenarios

(Coming soon)
