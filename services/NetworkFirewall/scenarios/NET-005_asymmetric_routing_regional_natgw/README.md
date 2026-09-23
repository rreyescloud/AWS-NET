# NET-005: Asymmetric Routing — Regional NAT Gateway + Network Firewall Multi-AZ

**Tier:** Lab — reproducible end to end
**Status:** In Progress — lab deployed and tested, customer answer pending

## Objective

Investigate and document the asymmetric routing concern when using a centralized Regional NAT Gateway with AWS Network Firewall endpoints distributed across multiple Availability Zones.


## Business Context — Airlines / Travel Technology

Client in the airline fare data and distribution technology industry.

Architecture goals:
- **Centralized egress** — Single Regional NAT Gateway for internet-bound traffic from multiple AZs
- **Network Firewall inspection** — All outbound traffic inspected by NFW before reaching the internet
- **Multi-AZ resilience** — Workloads distributed across AZs via Transit Gateway


## Problem Statement

Customer is setting up a centralized AWS Network Firewall architecture using a Regional NAT Gateway. The concern is that the Regional NAT Gateway has a single, centralized route table for return traffic. This means return traffic from the internet will always be routed to **one** firewall endpoint, regardless of which endpoint handled the outbound flow.

### Outbound (works correctly — symmetric per AZ):
```
VM in AZ1 → TGW → FW endpoint AZ1 → Regional NAT GW → Internet
VM in AZ2 → TGW → FW endpoint AZ2 → Regional NAT GW → Internet
```

### Return (potential asymmetric routing):
```
Internet → Regional NAT GW → FW endpoint AZ1 → ??? (VM in AZ1 or AZ2)
```

The route table associated with the Regional NAT Gateway subnet can only have one route per destination CIDR pointing to a single firewall endpoint. This creates asymmetric routing for return traffic from instances in other AZs.


## Environment

- Region: us-west-2
- VPC: `<customer-vpc-id>`
- NAT Gateway: `<customer-natgw-id>`
- Account: `<CUSTOMER_ACCOUNT_ID>`
- Architecture: TGW-based centralized inspection


## Key Questions

1. Does the Regional NAT Gateway maintain flow-state awareness to route return traffic to the correct FW endpoint?
2. Does Network Firewall handle asymmetric flows (stateful engine behavior)?
3. What is the recommended architecture for multi-AZ NFW + centralized NAT?


## Research Findings

### Regional NAT Gateway — How It Works

From AWS documentation and blog:

1. **Zonal affinity:** RNAT is NOT a single-AZ resource. It automatically expands across AZs where workloads exist, maintaining **zonal affinity**. Traffic from AZ1 is processed by the RNAT instance in AZ1.

2. **Own route table:** RNAT has its own AWS-managed route table (separate from VPC subnet route tables):
   - Default: `0.0.0.0/0 → IGW` (auto-created)
   - You can add routes for return traffic: `<app-subnet-CIDR> → NFW/GWLB endpoint`
   - Supports TGW as a valid route target

3. **Return traffic flow:** Internet → IGW → RNAT (reverse NAT translation) → RNAT route table evaluated → forwards to NFW/GWLB endpoint based on destination CIDR

4. **The customer's concern (route table single-entry):** The RNAT route table is single — one route per CIDR. But the critical insight is that RNAT expands per-AZ, so:
   - RNAT in AZ1 processes AZ1 traffic → route table can point AZ1 CIDRs to FW endpoint AZ1
   - RNAT in AZ2 processes AZ2 traffic → BUT same route table applies to all AZs

5. **AZ expansion delay:** Up to 60 minutes for RNAT to expand to a new AZ. During this window, traffic is cross-AZ routed randomly.

6. **Key limitation:** The RNAT route table **cannot be per-AZ** — it's a single table for the entire RNAT. This means if app subnets across AZs share the same CIDR range (e.g., 10.0.0.0/8 for TGW spokes), the route can only point to ONE firewall endpoint.

### Confirmed: chaining a firewall behind a regional NAT Gateway

Chaining a firewall appliance behind a NAT Gateway works the same way for the regional flavour as it does for the zonal one: you own the routes that steer return traffic to the right endpoint. The difference is that with a single regional route table you have to make those routes specific enough to disambiguate per AZ yourself.

Verified in the lab (see Test Results below) — **the working pattern requires per-subnet-CIDR routes** in the RNAT route table:
```
RNAT Route Table:
  0.0.0.0/0   → IGW (immutable, auto-created)
  10.1.0.0/24 → vpce-fw-az1 (AZ1 firewall endpoint)
  10.2.0.0/24 → vpce-fw-az2 (AZ2 firewall endpoint)
```

**Supported chaining order:**
```
Outbound: Customer ENI → FW endpoint (same AZ) → RNAT → IGW → Internet
Return:   Internet → IGW → RNAT → [RNAT RT evaluates dest CIDR] → FW endpoint (per AZ) → Customer ENI
```

**Order that does not work:** `ENI → RNAT → GWLBE`. Inspecting after translation means the firewall sees the NAT address instead of the workload address, so per-AZ return routing has nothing left to key on. Inspect before translating, not after.

### Answer to Customer's Concern

The customer's concern IS valid **if** they use an aggregated route (e.g., `10.0.0.0/8 → vpce-fw-az1`). That would send ALL return traffic to one endpoint = asymmetric routing = NFW drops.

**The solution:** configure more-specific routes per AZ in the RNAT route table, each pointing to the local AZ firewall endpoint.

### TGW Centralized Inspection Complication

In the customer's TGW architecture, spoke VPC traffic arrives at the inspection VPC via TGW. The RNAT route table needs routes back to the spoke VPCs through the correct per-AZ firewall endpoint. Two options:

**Option A — Per-spoke-subnet routes (if CIDRs are distinct per AZ):**
```
10.1.1.0/24 → vpce-fw-az1   (spoke subnet in AZ1)
10.1.2.0/24 → vpce-fw-az2   (spoke subnet in AZ2)
```

**Option B — Route return traffic via TGW (bypass FW for return):**
Since the whitepaper states "Ingress routing is not required in this case as return traffic will be forwarded to the NATGW IPs by default" — in the TGW egress-only model, return traffic may not need to traverse the firewall again if stateful tracking handles the return path.

**Option C — Use zonal NAT GWs instead of Regional:**
One NAT GW per AZ + per-AZ route tables = guaranteed symmetry. This is the proven pattern from the whitepaper.

### Allowed Routes in RNAT Route Table (2025 GA)

- IGW (auto-created, immutable)
- VPCE/GWLBE endpoints
- Interface routes
- **NOT supported:** TGW routes, VGW routes (Private NAT not supported)


## Lab Reproduction

Successfully reproduced in us-west-2 (account <LAB_ACCOUNT_ID>) on 2026-05-22.

### Architecture Deployed
- Inspection VPC (10.100.0.0/16): NFW endpoints + Regional NAT Gateway
- Spoke VPC (10.200.0.0/16): test EC2 instances in 2 AZs
- Transit Gateway connecting both VPCs
- Full centralized egress with inspection

### Test Results

**Initial deployment (missing spoke routes in RNAT RT):**
```
RNAT Route Table:
  10.100.10.0/24 → vpce-fw-az1  (TGW subnet AZ1)
  10.100.20.0/24 → vpce-fw-az2  (TGW subnet AZ2)
  0.0.0.0/0      → IGW

Result: curl TIMEOUT — 100% packet loss from both AZs
```

**Root cause:** RNAT performs reverse NAT (EIP → original source 10.200.1.168) but has no route for 10.200.0.0/16. Return packets are blackholed.

**Fix — add per-AZ spoke subnet routes:**
```
RNAT Route Table (corrected):
  10.100.10.0/24 → vpce-fw-az1  (TGW subnet AZ1)
  10.100.20.0/24 → vpce-fw-az2  (TGW subnet AZ2)
  10.200.1.0/24  → vpce-fw-az1  (spoke AZ1 return → FW AZ1)
  10.200.2.0/24  → vpce-fw-az2  (spoke AZ2 return → FW AZ2)
  0.0.0.0/0      → IGW

Result: HTTP 200 from both AZs, symmetric routing confirmed
  EC2 AZ1 (10.200.1.168) → Public IP 54.187.117.215 (RNAT AZ1 EIP)
  EC2 AZ2 (10.200.2.209) → Public IP 44.235.1.54    (RNAT AZ2 EIP)
```

### Key Conclusions

1. **The RNAT route table must have routes for ALL destination CIDRs that return traffic needs to reach** — not just the local inspection VPC subnets, but also spoke VPC subnets.

2. **Each spoke subnet CIDR must point to the firewall endpoint in the corresponding AZ** to maintain symmetric routing.

3. **Different public IPs per AZ confirm zonal affinity** — RNAT processes traffic locally per AZ.

4. **NFW flow state is per-endpoint** — if return traffic hits the wrong AZ endpoint, it gets dropped because there's no matching flow in that endpoint's state table.

5. **The pattern works but requires granular route management** — every spoke subnet per AZ needs its own route entry in the RNAT RT.

### Lab Resources

See `lab/resources.json` for all resource IDs.


## References

[1] Case CASE-12

[2] Introducing Amazon VPC Regional NAT Gateway (AWS Blog)
https://aws.amazon.com/blogs/networking-and-content-delivery/introducing-amazon-vpc-regional-nat-gateway/

[3] Regional NAT Gateways — AWS Documentation
https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateways-regional.html

[4] Using NAT Gateway with Firewall — Centralized Egress Whitepaper
https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/using-nat-gateway-with-firewall.html

[5] How to set up Network Firewall with NAT Gateway (re:Post)
https://repost.aws/knowledge-center/network-firewall-set-up-with-nat-gateway

[6] Deployment Models for NFW with VPC Routing Enhancements (Blog)
https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall-with-vpc-routing-enhancements/

[7] AWS Network Firewall Best Practices
https://aws.github.io/aws-security-services-best-practices/guides/network-firewall/
