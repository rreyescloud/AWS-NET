# VPC Connectivity

## VPC Peering

- Direct connection between two VPCs (same or different accounts/regions)
- Uses AWS backbone (no internet, no gateway)
- Non-transitive (A↔B and B↔C does NOT mean A↔C)
- Cannot have overlapping CIDRs
- Must update route tables in both VPCs

```bash
aws ec2 create-vpc-peering-connection \
  --vpc-id vpc-source \
  --peer-vpc-id vpc-target \
  --peer-region us-west-2
```

## VPN (Site-to-Site)

- Encrypted tunnel over internet between VPC and on-premises
- Uses Virtual Private Gateway (VGW) on AWS side
- Uses Customer Gateway (CGW) on customer side
- Supports BGP for dynamic routing
- Two tunnels per connection (HA across two AZs)
- Max throughput: ~1.25 Gbps per tunnel

## VPN (Client VPN)

- Remote access VPN for individual users
- OpenVPN-based managed service
- Supports Active Directory or certificate authentication
- Each user gets a VPN client connection to the VPC

## Direct Connect (DX)

- Dedicated private connection from on-premises to AWS
- Physical fiber (not over internet)
- Port speeds: 1 Gbps, 10 Gbps, 100 Gbps (or hosted connections: 50 Mbps - 10 Gbps)
- Lower latency and consistent bandwidth vs VPN
- Supports virtual interfaces: Public VIF, Private VIF, Transit VIF

| VIF Type | Access to |
|---|---|
| Private | VPC resources (via VGW) |
| Public | AWS public services (S3, DynamoDB, etc.) |
| Transit | Multiple VPCs via Transit Gateway |

## Transit Gateway (TGW)

- Regional hub that connects VPCs, VPNs, and Direct Connect
- Supports transitive routing (A↔TGW↔B means A can reach B)
- Route tables for fine-grained control
- Supports inter-region peering
- Scales to thousands of VPCs

```
VPC-A ──┐
VPC-B ──┼── TGW ── Direct Connect / VPN ── On-Premises
VPC-C ──┘
```

### TGW vs Peering

| Feature | VPC Peering | Transit Gateway |
|---|---|---|
| Transitive | No | Yes |
| Scale | 125 peering per VPC | 5,000 attachments |
| Cost | Free (data transfer only) | $0.05/hr per attachment + data |
| Complexity | Simple for few VPCs | Better for many VPCs |
| Bandwidth | No limit | Up to 50 Gbps per AZ |

## References

- [VPC Peering](https://docs.aws.amazon.com/vpc/latest/peering/what-is-vpc-peering.html)
- [Site-to-Site VPN](https://docs.aws.amazon.com/vpn/latest/s2svpn/VPC_VPN.html)
- [Client VPN](https://docs.aws.amazon.com/vpn/latest/clientvpn-admin/what-is.html)
- [Direct Connect](https://docs.aws.amazon.com/directconnect/latest/UserGuide/Welcome.html)
- [Transit Gateway](https://docs.aws.amazon.com/vpc/latest/tgw/what-is-transit-gateway.html)

## Additional References (from cases)

- [Centralized inspection with NF and TGW](https://aws.amazon.com/blogs/networking-and-content-delivery/centralized-inspection-architecture-with-aws-network-firewall-and-aws-transit-gateway/)
- [TGW route tables](https://docs.aws.amazon.com/vpc/latest/tgw/tgw-route-tables.html)
- [VPC Peering troubleshooting](http://docs.aws.amazon.com/vpc/latest/peering/troubleshoot-vpc-peering-connections.html)
- [VPC peering not working as expected](https://repost.aws/questions/QUpvxhKr-GQo6yHTvS1qdJSg/vpc-peering-not-working-as-expected)
- [Cannot connect to RDS with VPC peering](https://repost.aws/questions/QUOsf8Fe4eTjCfViesKBcK8w/i-can-t-connect-to-rds-with-vpc-peering-with-another-account)
- [VPC pricing](https://aws.amazon.com/vpc/pricing/)
- [NAT Gateway setup with Network Firewall](https://repost.aws/knowledge-center/network-firewall-set-up-with-nat-gateway)
