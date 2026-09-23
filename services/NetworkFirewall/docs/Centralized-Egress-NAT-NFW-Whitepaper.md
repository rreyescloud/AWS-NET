# Using NAT Gateway with AWS Network Firewall — Centralized IPv4 Egress

Source: https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/using-nat-gateway-with-firewall.html

---

## Architecture Pattern: Centralized Egress with TGW + NFW + NAT GW

NFW endpoint is the **default route target** in the TGW attachment subnet route table for the egress VPC.

### Traffic Flow (Outbound)
```
Spoke VPC → TGW (RT1 default route → egress VPC) → TGW ENI in egress VPC
  → TGW Subnet RT (default route → NFW endpoint in same AZ)
  → NFW inspects traffic
  → FW Subnet RT (default route → NAT GW)
  → NAT GW → IGW → Internet
```

### Return Traffic
> "Ingress routing is not required in this case as return traffic will be forwarded to the NATGW IPs by default."

Return traffic follows: Internet → IGW → NAT GW (reverse NAT) → routed back through the network.

### Key Point — No Appliance Mode Needed for Egress-Only
> "This case doesn't require Transit Gateway appliance mode, because you aren't sending traffic between attachments."

Appliance mode is only needed for East-West (VPC-to-VPC) traffic through inspection VPC.

### Source IP Visibility
> "Because you are using a Transit Gateway, here we can place the firewall prior to NAT gateway. In this model, the firewall can see the source IP behind the Transit Gateway."

NFW sees original source IPs from spoke VPCs (not NATted yet).

---

## Route Table Design

| Subnet | Route | Target |
|--------|-------|--------|
| TGW Attachment Subnet | 0.0.0.0/0 | NFW endpoint (same AZ) |
| Firewall Subnet | 0.0.0.0/0 | NAT Gateway |
| NAT GW / Public Subnet | 0.0.0.0/0 | IGW |
| Spoke VPCs (via TGW RT1) | 0.0.0.0/0 | Egress VPC attachment |

- RT1: Spoke VPC attachments associated; default route → egress VPC
- RT2: Egress VPC attachment; spoke VPC CIDRs propagated
- Blackhole route in RT1 to prevent spoke-to-spoke if needed

---

## Multi-AZ

> "AWS recommends deploying AWS Network Firewall endpoints in multiple Availability Zones. There should be one firewall endpoint in each Availability Zone the customer is running workloads in."

- One NFW endpoint per AZ
- TGW forwards traffic to ENI in one of the AZs in egress VPC
- Default route in TGW subnet sends traffic to local AZ NFW endpoint

---

## Key Considerations

1. **NFW does NOT perform NAT** — NAT GW handles address translation after inspection
2. **Scalability:** NFW scales to 100 Gbps per AZ automatically
3. **Cost optimization:** NAT GW processing charges waived 1:1 with NFW processing charges
4. **HOME_NET:** Must include ALL spoke VPC CIDRs attached to TGW (not just local VPC CIDR)
5. **Firewall subnet isolation:** No other resources in the firewall subnet
6. **Distributed alternative:** AWS Firewall Manager for distributed endpoints without TGW
7. **Test rules before production** — order matters (like NACLs)
8. **Separate Network Services account** recommended for TGW + egress VPC

---

## NFW Does NOT Require Appliance Mode for Egress

This is critical: when traffic flows **only outbound** (spoke → internet), appliance mode is NOT required because traffic doesn't flow between TGW attachments. Appliance mode is needed only for East-West inspection (VPC A → inspection VPC → VPC B).
