# VPC — Troubleshooting

## Connectivity Checklist

### 1. Route Tables
- [ ] Does the subnet have a route to the destination?
- [ ] Is 0.0.0.0/0 pointing to IGW (public) or NAT (private)?
- [ ] For peering: is there a route for the peer CIDR → pcx-xxx?
- [ ] For TGW: is there a route for the destination → tgw-xxx?

### 2. Security Groups
- [ ] Inbound rule allows the traffic (protocol/port/source)?
- [ ] Outbound rule allows the response? (usually open by default)
- [ ] Remember: SGs are stateful (return traffic auto-allowed)

### 3. Network ACLs
- [ ] Inbound rule allows traffic (check rule NUMBER order)?
- [ ] Outbound rule allows RESPONSE traffic (ephemeral ports 1024-65535)?
- [ ] Remember: NACLs are stateless (must allow both directions explicitly)

### 4. VPC Flow Logs
- [ ] Check for REJECT entries for the source/dest/port
- [ ] Flow Logs show SG/NACL rejections

### 5. DNS Resolution
- [ ] Does the hostname resolve correctly?
- [ ] enableDnsHostnames and enableDnsSupport enabled on VPC?
- [ ] DHCP Options Set pointing to correct DNS?
- [ ] Private Hosted Zone associated with the VPC?

### 6. Endpoints
- [ ] Is the endpoint in the correct VPC?
- [ ] Gateway endpoint: is the prefix list in the route table?
- [ ] Interface endpoint: is private DNS enabled?
- [ ] Endpoint policy allows the action?

## Common Issues

### "No route to host" / Timeout
1. Check route table for the destination
2. Check SG outbound + NACL outbound
3. Check SG inbound on target + NACL inbound on target subnet
4. Check VPC Flow Logs for REJECT

### Cross-Region S3 Access Fails (Private VPC)
- Gateway endpoint only covers same-region S3
- Need Interface endpoint with `--service-region` for cross-region
- Without 0.0.0.0/0 route, cross-region S3 traffic is simply dropped

### Peering Connection Not Working
- Route tables must be updated in BOTH VPCs
- CIDRs cannot overlap
- DNS resolution across peering requires enableDnsHostnames on both sides

### Private Subnet Cannot Reach Internet
- NAT Gateway must be in a PUBLIC subnet (with route to IGW)
- Private subnet route table needs 0.0.0.0/0 → nat-xxx
- NAT Gateway needs an EIP

### VPC Flow Logs Show ACCEPT but Traffic Still Fails
- Issue is likely at application layer (not network)
- Check application logs, firewall on the instance (iptables), service running

## Useful Commands

```bash
# Check routes
aws ec2 describe-route-tables --filters "Name=vpc-id,Values=vpc-xxx"

# Check SG rules
aws ec2 describe-security-group-rules --filters "Name=group-id,Values=sg-xxx"

# Check Flow Logs
aws logs filter-log-events --log-group-name /vpc/flowlogs --filter-pattern "REJECT"

# Test reachability (from EC2)
nc -zv <ip> <port>
curl -v https://<endpoint>
dig <hostname>
traceroute <ip>
```

## References

- [Troubleshoot VPC connectivity](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-troubleshooting.html)
- [VPC Flow Logs](https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs.html)
- [Reachability Analyzer](https://docs.aws.amazon.com/vpc/latest/reachability/what-is-reachability-analyzer.html)

## Additional References (from cases)

- [Troubleshoot VPC connectivity](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-troubleshooting.html)
- [VPC Flow Logs](https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs.html)
- [Reachability Analyzer](https://docs.aws.amazon.com/vpc/latest/reachability/what-is-reachability-analyzer.html)
- [VPC create options](https://docs.aws.amazon.com/vpc/latest/userguide/create-vpc-options.html)
- [EC2 public addresses](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-instance-addressing.html#concepts-public-addresses)
- [WAF service IAM reference](https://docs.aws.amazon.com/waf/latest/developerguide/security_iam_service-with-iam.html#security_iam_action-AssociateWebACL)
- [Service Quotas request increase](https://docs.aws.amazon.com/servicequotas/latest/userguide/request-quota-increase.html)

- [Troubleshoot network issues VPC to on-prem via IGW](https://repost.aws/knowledge-center/network-issue-vpc-onprem-ig)
- [EIC Endpoint only ports 22/3389](https://repost.aws/questions/QUZgD8nmZGTR-G1NL5-zFbaw/ec2-instance-connect-endpoint-blocks-ports-other-than-22-and-3389)
- [Reactivate suspended account](https://repost.aws/knowledge-center/reactivate-suspended-account)
- [Reopen closed account](https://repost.aws/knowledge-center/reopen-aws-account)
- [Data Privacy FAQ](https://aws.amazon.com/compliance/data-privacy-faq/)
- [AWS Regional Services List](https://regions.aws.dev/services/#all)
