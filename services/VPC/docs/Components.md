# VPC Components

## ENI (Elastic Network Interface)

- Virtual network card attached to an instance
- Has: private IP, optional public IP, MAC address, security groups
- Can be moved between instances (same AZ only)
- Multiple ENIs per instance for multi-homed configs
- Primary ENI cannot be detached

## EIP (Elastic IP)

- Static public IPv4 address
- Associated with your account (not instance)
- Can be remapped to another instance quickly (failover)
- Charged when NOT associated with a running instance ($0.005/hr)
- Limit: 5 per region (can request increase)

## IPAM (IP Address Manager)

- Centralized IP address management across accounts and regions
- Plan, track, and monitor IP allocations
- Integrates with AWS Organizations
- Detects overlapping CIDRs
- Pool hierarchy: top-level → regional → account pools

```bash
aws ec2 create-ipam --operating-regions '[{"RegionName":"us-east-1"}]'
```

## MTU (Maximum Transmission Unit)

- Default MTU: 1500 bytes (standard Ethernet)
- Jumbo frames: 9001 bytes (supported within VPC and peering)
- Path MTU Discovery: enable to find max MTU along path
- VPN tunnels: limited to 1500 (no jumbo)
- Direct Connect: supports jumbo frames (9001)
- Internet traffic: always 1500

### Troubleshooting MTU Issues

```bash
# Test path MTU (from EC2)
ping -M do -s 8972 <destination-ip>   # 8972 + 28 headers = 9000

# If packet too large, reduce until it works
ping -M do -s 1472 <destination-ip>   # 1472 + 28 = 1500
```

## IP Ranges

- IPv4: VPC supports /16 to /28
- IPv6: VPC gets /56 block from AWS pool
- Reserved per subnet: 5 IPs (.0, .1, .2, .3, .255)
- AWS public IP ranges: https://ip-ranges.amazonaws.com/ip-ranges.json

## BYOIP (Bring Your Own IP)

- Bring your own public IPv4/IPv6 ranges to AWS
- Must own the range (verified via ROA - Route Origin Authorization)
- Range must be /24 or larger (IPv4)
- Can advertise from AWS regions
- Useful for IP reputation preservation during migration

## References

- [ENI](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html)
- [Elastic IP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html)
- [IPAM](https://docs.aws.amazon.com/vpc/latest/ipam/what-it-is-ipam.html)
- [MTU](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/network_mtu.html)
- [AWS IP ranges](https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html)
- [BYOIP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-byoip.html)

## Additional References (from cases)

### NAT Gateway
- [Reduce NAT Gateway transfer costs](https://repost.aws/knowledge-center/vpc-reduce-nat-gateway-transfer-costs)
- [Find traffic sources through NAT Gateway](https://repost.aws/knowledge-center/vpc-find-traffic-sources-nat-gateway)
- [NAT Gateway processing charges breakdown](https://repost.aws/articles/ARVW_A6TwLT12XTXWdjdlgJQ/how-to-figure-out-whether-nat-gateway-processing-charge-is-due-to-internet-bound-traffic-or-within-aws)
- [Break down VPC costs](https://repost.aws/questions/QUelBAhchWSm-9IcmSv3IybQ/how-to-break-down-vpc-costs)

### Security Groups / Prefix Lists
- [Security group rule limits](https://docs.aws.amazon.com/vpc/latest/userguide/security-group-rules.html#security-group-size)
- [Managing prefix lists quota considerations](https://repost.aws/articles/ARSvw6_D8cTkaAQGRMK1L2QA/managing-prefix-lists-in-security-groups-quota-considerations)
- [Increase security group rule limit](https://repost.aws/knowledge-center/increase-security-group-rule-limit)
- [Managed prefix lists](https://docs.aws.amazon.com/vpc/latest/userguide/managed-prefix-lists.html)
- [VPC limits - security groups](https://docs.aws.amazon.com/vpc/latest/userguide/amazon-vpc-limits.html#vpc-limits-security-groups)

### CIDR / Subnets
- [Add/remove VPC CIDR](https://docs.aws.amazon.com/vpc/latest/userguide/add-ipv4-cidr.html)
- [Terraform AWS issue #9592 (CIDR disassociation timing)](https://github.com/hashicorp/terraform-provider-aws/issues/9592)
- [Terraform AWS issue #40791](https://github.com/hashicorp/terraform-provider-aws/issues/40791)

### BYOIP / EIP
- [Fix BYOIP configuration](https://repost.aws/knowledge-center/vpc-fix-byoip-configuration)
- [EC2 port 25 throttle removal](https://repost.aws/knowledge-center/ec2-port-25-throttle)
- [Disassociate VPC CIDR block API](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2/client/disassociate_vpc_cidr_block.html)
