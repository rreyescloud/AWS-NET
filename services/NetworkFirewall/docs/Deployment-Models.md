# AWS Network Firewall - Deployment Models

## Single-AZ Deployment

- Simplest model: one firewall endpoint in one AZ
- Traffic routed to firewall via VPC route tables
- No cross-AZ resilience (single point of failure)
- Use case: Dev/test environments, cost-sensitive workloads

---

## Multi-AZ Deployment

- Firewall endpoint deployed in each AZ
- Each AZ's traffic is inspected by its own local endpoint
- Provides high availability and fault tolerance
- Route tables per AZ direct traffic to the local firewall endpoint

### Key Architecture Points

- Firewall endpoints sit in dedicated **firewall subnets** (one per AZ)
- Protected workload subnets route through the local firewall endpoint
- Internet Gateway ingress routing directs return traffic to the correct AZ's endpoint
- **Critical:** Traffic must be routed symmetrically - request and response must pass through the **same** firewall endpoint

---

## Centralized Inspection with Transit Gateway

The most common enterprise pattern: inspect all traffic from multiple VPCs through a central firewall.

### Architecture Pattern

```
Spoke VPC --> TGW --> Inspection VPC (Firewall Subnet) --> TGW --> Internet/On-premises
```

### Detailed Flow

```
+------------------+     +------------------+     +------------------+
|   Spoke VPC A    |     |   Spoke VPC B    |     |   Spoke VPC C    |
|  (Workloads)     |     |  (Workloads)     |     |  (Workloads)     |
+--------+---------+     +--------+---------+     +--------+---------+
         |                         |                         |
         +------------+------------+------------+------------+
                      |
              +-------v--------+
              | Transit Gateway|
              +-------+--------+
                      |
         +------------v-------------+
         |     Inspection VPC       |
         |                          |
         |  +--------------------+  |
         |  | Firewall Subnet    |  |
         |  | (NF Endpoints)     |  |
         |  +--------------------+  |
         |                          |
         |  +--------------------+  |
         |  | TGW Subnet         |  |
         |  +--------------------+  |
         |                          |
         |  +--------------------+  |
         |  | NAT GW Subnet      |  |
         |  | (for egress)       |  |
         |  +--------------------+  |
         +--------------------------+
                      |
              +-------v--------+
              | Internet GW /  |
              | NAT Gateway    |
              +----------------+
```

### Routing Table Configuration

| Route Table | Destination | Target | Purpose |
|------------|-------------|--------|---------|
| TGW RT (Spoke association) | 0.0.0.0/0 | Inspection VPC attachment | Send all spoke traffic to inspection |
| TGW RT (Inspection association) | Spoke CIDRs (e.g., 10.1.0.0/16) | Respective spoke attachment | Return traffic to correct spoke |
| Inspection VPC - TGW Subnet RT | 0.0.0.0/0 | Firewall endpoint (local AZ) | Force traffic through firewall |
| Inspection VPC - Firewall Subnet RT | 0.0.0.0/0 | NAT Gateway or IGW | Egress after inspection |
| Inspection VPC - Firewall Subnet RT | Spoke CIDRs | Transit Gateway | Return east-west traffic to TGW |
| Inspection VPC - NAT GW Subnet RT | 0.0.0.0/0 | Internet Gateway | Internet access |
| Inspection VPC - NAT GW Subnet RT | Spoke CIDRs | Firewall endpoint (local AZ) | Return traffic through firewall |

---

## Asymmetric Routing Problem

### The Problem

Asymmetric routing occurs when request and response traffic take different paths. This is critical for Network Firewall because:

- Network Firewall is **stateful** (maintains connection state)
- If Request goes through Firewall Endpoint A
- And Response goes through Firewall Endpoint B
- Endpoint B does NOT have the connection state
- **Result: Response is DROPPED**

### The Solution

1. **Appliance Mode on TGW**: Enable appliance mode on the Inspection VPC's TGW attachment
   - This ensures both directions of a flow use the same AZ
   - Even if source and destination are in different AZs

2. **Correct route table design**: Each AZ's route table must point to its LOCAL firewall endpoint
   - Never route traffic cross-AZ to a firewall endpoint in another AZ

3. **NAT Gateway placement**: Place NAT GW in the same AZ as the firewall endpoint it works with

### When Asymmetric Routing Happens

- Multi-AZ deployments without appliance mode enabled
- Cross-AZ communication between spoke VPCs
- Failover scenarios where traffic shifts AZs
- Incorrect route table configuration pointing to wrong AZ's endpoint
- Multiple paths available (ECMP) without flow pinning

---

## Architecture: AWS to On-Premises (Hybrid with Oracle)

```
+-------------------------------------------------------------+
|                         AWS VPC                              |
|                                                              |
|  +--------------+         +-----------------+              |
|  |  EC2 Instance|-------->|  Route Table    |              |
|  | (cx_Oracle)  |         |  10.0.0.0/8 ->  |              |
|  +--------------+         |  vgw-xxxxx      |              |
|         |                 +-----------------+              |
|         |                          |                        |
|         |                          v                        |
|         |                 +-----------------+              |
|         |                 | Security Group  |              |
|         |                 | Egress: TCP 1521|              |
|         |                 +-----------------+              |
|         |                          |                        |
|         |                          v                        |
|         |                 +-----------------+              |
|         |                 |   Network ACL   |              |
|         |                 | Out: TCP 1521   |              |
|         |                 | In: TCP 1024-   |              |
|         |                 |     65535       |              |
|         |                 +-----------------+              |
|         |                          |                        |
|         v                          v                        |
|  +--------------------------------------+                  |
|  |    Network Firewall (Optional)       |                  |
|  |    Rule: pass tcp any -> Oracle:1521 |                  |
|  +--------------------------------------+                  |
|                     |                                       |
|                     v                                       |
|  +--------------------------------------+                  |
|  |   Virtual Private Gateway (VPN)      |                  |
|  |   or Direct Connect Gateway          |                  |
|  +--------------------------------------+                  |
|                     |                                       |
+---------------------+---------------------------------------+
                      |
                      | VPN Tunnel / Direct Connect
                      |
+---------------------v---------------------------------------+
|                  On-Premises Network                         |
|                                                              |
|  +--------------------------------------+                  |
|  |   Corporate Firewall                 |                  |
|  |   Allow: AWS CIDR -> Oracle:1521     |                  |
|  +--------------------------------------+                  |
|                     |                                       |
|                     v                                       |
|  +--------------------------------------+                  |
|  |   Oracle Database Server (GOS)       |                  |
|  |   Listener on port 1521              |                  |
|  +--------------------------------------+                  |
|                                                              |
+--------------------------------------------------------------+
```

### Routing Table Example for Hybrid Connectivity

| Route Table | Destination | Target | Purpose |
|------------|-------------|--------|---------|
| EC2 Subnet RT | 10.0.0.0/8 (on-prem) | Network Firewall endpoint | Inspect before leaving VPC |
| Firewall Subnet RT | 10.0.0.0/8 (on-prem) | Virtual Private Gateway (vgw) | Forward to on-prem via VPN/DX |
| Firewall Subnet RT | 0.0.0.0/0 | IGW or NAT GW | Internet access |

---

## Centralized Egress with NAT Gateway + Network Firewall

Common pattern for organizations needing egress filtering:

1. Spoke VPCs send all internet-bound traffic to Inspection VPC via TGW
2. Network Firewall inspects and filters egress traffic
3. Allowed traffic passes to NAT Gateway for internet access
4. Return traffic flows back through NAT GW -> Firewall -> TGW -> Spoke

Architecture depends on whether NF is placed before or after NAT Gateway. Common pattern: Public subnet (IGW) -> NAT Gateway -> Firewall subnet -> Private subnets.

---

## References

- [Deployment models for AWS Network Firewall](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/)
- [Deployment models with VPC routing enhancements](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall-with-vpc-routing-enhancements/)
- [Design your firewall deployment for internet ingress traffic flows](https://aws.amazon.com/blogs/networking-and-content-delivery/design-your-firewall-deployment-for-internet-ingress-traffic-flows/?nc1=h_ls)
- [Inspection deployment models with AWS Network Firewall (PDF)](https://d1.awsstatic.com/architecture-diagrams/ArchitectureDiagrams/inspection-deployment-models-with-AWS-network-firewall-ra.pdf)
- [Transit Gateway - How it works](https://docs.aws.amazon.com/vpc/latest/tgw/how-transit-gateways-work.html#TGW_Scenarios)
- [Transit gateway traffic flow and asymmetric routing](https://docs.aws.amazon.com/prescriptive-guidance/latest/inline-traffic-inspection-third-party-appliances/transit-gateway-asymmetric-routing.html)
- [Transit gateway attachment configuration for Network Firewall](https://docs.aws.amazon.com/network-firewall/latest/developerguide/vpc-config-tgw-multi-az.html)
- [Using NAT gateway with Network Firewall for centralized egress](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/using-nat-gateway-with-firewall.html)
- [Architecture with IGW and NAT GW using Network Firewall](https://docs.aws.amazon.com/network-firewall/latest/developerguide/arch-igw-ngw.html)
- [How do I set up an AWS Network Firewall with a NAT gateway?](https://repost.aws/knowledge-center/network-firewall-set-up-with-nat-gateway)

## Additional References (from cases)

### Architecture Patterns
- [Deployment models blog](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/)
- [Centralized traffic filtering with TGW](https://aws.amazon.com/blogs/networking-and-content-delivery/deploy-centralized-traffic-filtering-using-aws-network-firewall/)
- [Centralized inspection with GWLB + TGW](https://aws.amazon.com/blogs/networking-and-content-delivery/centralized-inspection-architecture-with-aws-gateway-load-balancer-and-aws-transit-gateway/)
- [Multi-account with Control Tower](https://aws.amazon.com/blogs/mt/scale-multi-account-architecture-aws-network-firewall-and-aws-control-tower/)
- [How it works](https://docs.aws.amazon.com/network-firewall/latest/developerguide/how-it-works.html)
- [Set up with NAT Gateway](https://repost.aws/knowledge-center/network-firewall-set-up-with-nat-gateway)
- [TGW and inspection VPC routing](https://repost.aws/questions/QUoeutW9kyQsuqS3VyBlDJ3Q/transit-gateway-and-inspection-vpc-routing-for-different-traffic-flows)
- [TGW and NF integration](https://repost.aws/questions/QUK1Yii5o8Q42g7HKkOeEMNg/transit-gateway-and-aws-network-firewall)
- [Multiple VPC endpoints (2025)](https://aws.amazon.com/about-aws/whats-new/2025/05/aws-network-firewall-multiple-vpc-endpoints/)
- [TGW native integration (2025)](https://aws.amazon.com/about-aws/whats-new/2025/06/aws-network-firewall-transit-gateway-native-integration/)

### Firewall Manager
- [Deploy NF with Firewall Manager](https://aws.amazon.com/blogs/security/how-to-deploy-aws-network-firewall-by-using-aws-firewall-manager/)
- [Enforce NF protections at scale](https://aws.amazon.com/blogs/security/enforce-your-aws-network-firewall-protections-at-scale-with-aws-firewall-manager/)
- [FWM network firewall policies](https://docs.aws.amazon.com/waf/latest/developerguide/network-firewall-policies.html)
- [FWM create firewall endpoints](https://docs.aws.amazon.com/waf/latest/developerguide/fms-create-firewall-endpoints.html)
- [FWM resource sets](https://docs.aws.amazon.com/waf/latest/developerguide/fms-resource-sets.html)
- [Import existing NF resources (2022)](https://aws.amazon.com/about-aws/whats-new/2022/11/aws-firewall-manager-import-existing-aws-network-firewall-resources/)

### Subnet / Endpoint Management
- [Endpoint failures troubleshooting](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-troubleshooting-endpoint-failures.html)
- [SubnetMapping API](https://docs.aws.amazon.com/network-firewall/latest/APIReference/API_SubnetMapping.html)
- [Deleting VPC endpoint association](https://docs.aws.amazon.com/network-firewall/latest/developerguide/deleting-vpc-endpoint-association.html)
