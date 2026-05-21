# Route 53 Troubleshooting

## DNS Resolution Issues

### General DNS Failures
- How does DNS work, and how do I troubleshoot partial or intermittent DNS failures? - https://repost.aws/knowledge-center/partial-dns-failures
- How do I troubleshoot DNS SERVFAIL issues? - https://repost.aws/knowledge-center/route53-dns-servfail-response

### Private Hosted Zone Resolution
- How can I troubleshoot DNS resolution issues with my Route 53 private hosted zone? - https://repost.aws/knowledge-center/route-53-fix-dns-resolution-private-zone
- Ensure VPC has `enableDnsHostnames` and `enableDnsSupport` set to true
- Verify VPC is associated with the private hosted zone

### DNS Propagation
- How do I resolve DNS propagation delays and inconsistencies in Route 53? - https://repost.aws/knowledge-center/route-53-propagate-dns-changes

### UnknownHostException (Java)
- How do I troubleshoot the UnknownHostException error in my Java application? - https://repost.aws/knowledge-center/route-53-fix-unknownhostexception-error

## Resolver Troubleshooting

### Outbound Resolver Issues
- Verify security group allows outbound DNS traffic (UDP and TCP on port 53)
- Check that forwarding rules are associated with the correct VPC
- Verify target DNS server IPs are reachable
- Check route tables for connectivity to target IPs

### Inbound Resolver Issues
- Verify security group allows inbound DNS traffic from source network
- Ensure proper routing from on-premises to the inbound endpoint IPs
- Check VPN/Direct Connect connectivity

### VPC Flow Logs Note
VPC Flow logs will NOT log traffic between Resolver (VPC CIDR+2 or 169.254.169.253) and Inbound/Outbound ENIs.

### Cross-Region Resolver Setup Example
For cross-region DNS resolution (e.g., us-west-2 cluster resolving us-east-1 endpoints):
1. Create Outbound Resolver Endpoint in source region (e.g., us-west-2) with SG permitting outbound DNS (UDP/TCP port 53)
2. Create Inbound Resolver Endpoint in target region (e.g., us-east-1) with SG allowing inbound DNS from source VPC
3. Create Forwarding Rules in source region for target domains (e.g., sts.us-east-1.amazonaws.com) pointing to inbound endpoint IPs
4. Associate forwarding rules with the source VPC

## Health Check Troubleshooting

### Common Failures
- `curl -kIs --http1.1 <URL> | head -1` - Quick HTTP status check
- Verify SG/NACL allows health checker IP ranges
- Check SSL/TLS configuration for HTTPS health checks
- Reference: https://repost.aws/knowledge-center/route-53-fix-unhealthy-health-checks

## Domain Troubleshooting

### Closed Account with Domain
- My AWS account is closed or permanently closed, and my domain is registered with Route 53 - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/troubleshooting-account-closed.html

### Transfer Issues
- clientTransferProhibited status - https://repost.aws/knowledge-center/route-53-fix-clienttransferprohibited

## EKS / CoreDNS Troubleshooting

### Verify CoreDNS Health
```bash
# Verify CoreDNS pods are running
kubectl get pods -n kube-system -l k8s-app=kube-dns

# Check CoreDNS logs for errors
kubectl logs -n kube-system -l k8s-app=kube-dns
```

### SERVFAIL with Third-Party Domains
If SERVFAIL errors only occur with specific third-party hosted domains (not with other domains), and there are no Route 53 hosted zones involved, the issue likely originates from the third-party DNS provider rather than Route 53 or CoreDNS.

References:
- How do I troubleshoot DNS failures with Amazon EKS? - https://repost.aws/knowledge-center/eks-dns-failure
- external-dns helm: https://github.com/kubernetes-sigs/external-dns/blob/v0.15.0/docs/tutorials/aws.md

## Route 53 and Disaster Recovery

- Existing DNS queries continue to work during AWS outages
- New resource provisioning requiring DNS record creation would be impacted
- Do NOT rely on creating, updating, or deleting Route 53 resources in your recovery path
- Reference: https://aws.amazon.com/blogs/networking-and-content-delivery/creating-disaster-recovery-mechanisms-using-amazon-route-53/

## Useful CLI Commands

```bash
# Test DNS answer
aws route53 test-dns-answer --hosted-zone-id <id> --record-name <name> --record-type <type>

# Get health check status
aws route53 get-health-check-status --health-check-id <id>

# List hosted zones
aws route53 list-hosted-zones

# List resource record sets
aws route53 list-resource-record-sets --hosted-zone-id <id>
```

## Key References

- Route 53 Quotas - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/DNSLimitations.html
- AWS IP address ranges - https://docs.aws.amazon.com/vpc/latest/userguide/aws-ip-ranges.html
- IP-ranges JSON - https://ip-ranges.amazonaws.com/ip-ranges.json
- Resolver query logging - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-query-logs.html
- How do I log a query for Amazon Route 53? - https://repost.aws/knowledge-center/route53-log-queries
