# Deployment Models for AWS Network Firewall with VPC Routing Enhancements

Source: https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall-with-vpc-routing-enhancements/

## Key Principle

> "Traffic return-path must be symmetric; asymmetric traffic will not be returned to source"
> "Keep the inter-AZ traffic inspection local to client's AZ"

---

## Model 1: East-West (Inter-Subnet within Same VPC)

Inspects traffic between subnets (web tier ↔ app tier ↔ DB tier).

**Flow:** App Subnet → FW Endpoint → DB Subnet (and vice versa)

**Route Tables:**
- Public RT: routes toward public workloads
- Firewall Subnet RT: retains original local VPC route (ensures symmetry)
- Private RT (app/db): 0.0.0.0/0 → FW endpoint

**Multi-AZ:** Each AZ has its own FW endpoint. Two unique subnet RTs per AZ + common firewall subnet RT.

**Key:** SG referencing cannot be used when FW endpoint is inserted; must use IP addresses.

---

## Model 2: Outbound/Egress (Private Workloads → NAT GW → Internet)

Inspects egress traffic BEFORE NAT — preserves original source IPs for 5-tuple rules.

**Flow:** Private Subnet → FW Endpoint → NAT GW → IGW → Internet

**Route Tables (3 per AZ):**
1. Private Subnet RT: `0.0.0.0/0 → FW endpoint`
2. Firewall Subnet RT: `0.0.0.0/0 → NAT GW` + local route for return
3. Public Subnet RT: local route target **replaced** with FW endpoint (VPC routing enhancement)

**Return Traffic:** Internet → IGW → NAT GW → (Public Subnet RT points to FW endpoint) → FW → Private Subnet

**Multi-AZ:** Per-AZ NAT GW + per-AZ FW endpoint + per-AZ route tables = symmetric routing guaranteed.

**Critical:** Placing FW before NAT gives "complete visibility of IP addresses of the workloads."

---

## Model 3: Ingress/Shared Services VPC with Transit Gateway

Inspects traffic entering from TGW before reaching services, or from internet before traversing TGW.

**Sub-case A (Ingress VPC):**
IGW → Public Subnet (ALB/NLB) → FW Endpoint → TGW Subnet → TGW → Other VPCs

**Sub-case B (Shared Services VPC):**
TGW → TGW Subnet → FW Endpoint → Shared Services Subnets

**Route Tables:**
1. Public/Service Subnet RT: routes to other networks → FW endpoint
2. Firewall Subnet RT: routes to TGW subnet (common across AZs)
3. TGW Subnet RT: **more specific routes** → local AZ FW endpoint (critical for symmetric return)

**Return from TGW:** Must use more-specific routes in TGW subnet RT pointing to LOCAL AZ FW endpoint.

**Multi-AZ:** Three unique subnet RTs per AZ + common FW subnet RT.

**Key:** "Using the guiding principle, configure more specific routes in each Transit Gateway subnet route tables to ensure traffic goes to local AWS Network Firewall endpoint where the destination belongs."

---

## General Considerations

| Item | Detail |
|------|--------|
| SG Referencing | Cannot use when FW inserted; must use IP |
| NLB Target Type | Must use IP (not instance) for routes to work |
| Route Specificity | More-specific routes must match existing subnet CIDRs |
| Dedicated Subnet | FW always needs its own subnet |
| VPC Peering | Cannot inspect peering traffic |
| Route Limits | Default 50/table, max 1,000 |
| Firewall Limits | Default 5 per account per Region |
