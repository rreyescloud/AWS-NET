# Transit Gateway — Multi-Account Routing & Orchestration

Transit Gateway shows up across this portfolio as the connectivity layer under other services:
centralized inspection in [NET-005](../NetworkFirewall/scenarios/NET-005_asymmetric_routing_regional_natgw/)
and [NET-011](../NetworkFirewall/scenarios/NET-011_suricata_domain_allowlist_syn_drop/).
This section collects the scenarios where the Transit Gateway itself — its route tables, its
attachment lifecycle, or the automation that drives them — is the subject.

## Themes

- **Hub-and-spoke at organization scale** — RAM-shared TGW, auto-accept of shared attachments,
  default association and propagation disabled, one route table per segment
- **Tag-driven attachment lifecycle** — EventBridge and Step Functions reacting to spoke-side tags,
  approval gates on sensitive route tables, and the state drift that appears when the network is
  changed outside that workflow
- **Segmentation** — association and propagation as the real controls, isolated and flat segments,
  blackhole routes, inspection VPCs with appliance mode
- **Hybrid edges** — Direct Connect gateway attachments, allowed prefixes, DX-only spokes
- **AWS Solutions on top of TGW** — what you inherit, and own, when the onboarding workflow is
  someone else's code

## Scenarios

- **[NET-020](scenarios/NET-020_stno_console_scan_pagination/)** — the STNO console stops showing
  pending requests once the table outgrows one DynamoDB Scan page · Public Sector / Research
  Computing · *Lab*

## Documentation

Docs for this service are not written up yet. Transit Gateway material currently lives in the
Network Firewall deployment-model docs
([Deployment Models](../NetworkFirewall/docs/Deployment-Models.md),
[Centralized Egress](../NetworkFirewall/docs/Centralized-Egress-NAT-NFW-Whitepaper.md))
and in the VPC [Connectivity](../VPC/docs/Connectivity.md) notes.
