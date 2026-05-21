1# VPC — Core Concepts

## What is a VPC

A Virtual Private Cloud is a logically isolated section of AWS where you launch resources in a virtual network you define.

## Key Components

| Component | Purpose |
|---|---|
| VPC | The network container (CIDR block) |
| Subnet | Segment of VPC in one AZ |
| Route Table | Controls where traffic is directed |
| Internet Gateway (IGW) | Connects VPC to internet (1:1 with VPC) |
| NAT Gateway | Allows private subnets outbound internet (no inbound) |
| Security Group (SG) | Stateful firewall at instance level |
| Network ACL (NACL) | Stateless firewall at subnet level |

## NACL vs Security Group

| Feature | Security Group | NACL |
|---|---|---|
| Level | Instance (ENI) | Subnet |
| Stateful | Yes (return traffic auto-allowed) | No (must allow both directions) |
| Rules | Allow only (implicit deny) | Allow AND Deny |
| Evaluation | All rules evaluated | Rules evaluated in order (lowest # first) |
| Default | Allows all outbound, denies all inbound | Allows all in/out |

## CIDR Planning

- VPC: /16 to /28 (65,536 to 16 IPs)
- Subnet: same range, within VPC CIDR
- AWS reserves 5 IPs per subnet (.0 network, .1 router, .2 DNS, .3 future, .255 broadcast)
- Plan for growth: don't use the maximum CIDR immediately
- Avoid overlapping CIDRs if you plan to peer VPCs

## Default VPC

- Created automatically in every region for every account
- CIDR: 172.31.0.0/16
- One public subnet per AZ
- Has IGW attached
- Instances get public IPs by default

## Secondary CIDRs

- Can add up to 5 secondary IPv4 CIDRs
- Cannot overlap with existing CIDRs or peered VPC CIDRs
- Can remove secondary CIDRs (but not the primary)
- Must remove all subnets in a CIDR block before removing it

## References

- [VPC User Guide](https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html)
- [VPC sizing](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-cidr-blocks.html)
- [Security Groups](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html)
- [Network ACLs](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html)
- [Add/remove CIDR blocks](https://docs.aws.amazon.com/vpc/latest/userguide/add-ipv4-cidr.html)
