# Route 53 Health Checks

## Health Check Types

### HTTP/HTTPS Health Checks
- Route 53 must establish TCP connection with endpoint within **4 seconds**
- Endpoint must respond with 2xx or 3xx status within **2 seconds**
- Troubleshooting: `curl -l -vv <endpoint>`

### HTTP/HTTPS with String Matching
- Same TCP and response requirements as HTTP/HTTPS
- After receiving HTTP code, must receive response body within next **2 seconds**
- Route 53 searches body for a specified string
- String must appear entirely within the first **5,120 bytes** of the response body
- Troubleshooting: `curl -r 0-5120` or `curl -sL https://www.site.com | head -c 5120 | grep <string>`

### TCP Health Checks
- Route 53 must establish TCP connection with endpoint within **10 seconds**
- Troubleshooting:
  - Use telnet
  - `nc -vz <endpoint> <port>`

### Calculated Health Checks
- Route 53 adds up the number of child health checks that are healthy
- Compares that number with the minimum number of child health checks that must be healthy
- Parent health check status is based on this calculation

### Health Check Based on CloudWatch Alarm State
- Route 53 monitors the data stream for a CloudWatch alarm
- If State = OK, Health = Healthy
- If State = Alarm, Health = Unhealthy

## Configuration

### Timeout and Interval
- **Request interval:** 10 seconds (standard) or 30 seconds (fast)
- **Failure threshold:** Number of consecutive health checks that must fail (1-10, default 3)

### Health Checker Regions
- Route 53 uses health checkers in multiple AWS Regions
- Health checkers IP ranges: https://ip-ranges.amazonaws.com/ip-ranges.json
- Security Groups and NACLs must allow health checker IPs

### Latency Monitoring
- Enable **Latency graphs** option when creating a health check to view CW graphs
- **NOTE:** You cannot enable latency monitoring for existing health checks
- Reference: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/monitoring-health-check-latency.html

## Failover Behavior

### With Failover Routing
- Primary record requires a health check
- When primary becomes unhealthy, traffic routes to secondary
- For setups with AWS Load Balancer as backend, health check of the backend is done through AWS backbone

### Health Check + Record Association
- Health check must be associated with the DNS record for failover to work
- For alias records pointing to ELB, Route 53 uses the ELB's built-in health checks

## Common Troubleshooting

### Health Check Failure
1. **Network latency** - Check distance between health checkers and endpoint
2. **SG/NACL** - Ensure Route 53 health checker IP ranges are not blocked
3. **Firewall** - If firewall is in place, ensure health checker IPs are allowed
4. **SSL issues** (for HTTPS):
   - Check if SNI is enabled with correct hostname
   - Check SSL/TLS version matching
   - Verify cipher compatibility
   - Verify certificate keys are correct
   - Use openSSL or packet capture to troubleshoot
5. **Endpoint performance** - Check CPU, memory, disk I/O of the target server

### "Health checker could not establish a connection within the timeout limit"
- Common when setting up a new health check
- Check the health check type and verify the response time associated with the HC
- Verify security groups/NACLs allow traffic from health checker IPs

## Cross-Account Health Checks

- Health checks can only be created in the account that owns them
- To reference a health check cross-account, use the health check ID
- For failover routing with external-dns helm: health check ID is mandatory for primary region

## CLI Commands

```bash
# Get health check status
aws route53 get-health-check-status --health-check-id <id>

# List health checks
aws route53 list-health-checks

# Create health check
aws route53 create-health-check --caller-reference <ref> --health-check-config <config>
```

## Key References

- Creating Amazon Route 53 health checks - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating.html
- ChangeResourceRecordSets - https://docs.aws.amazon.com/Route53/latest/APIReference/API_ChangeResourceRecordSets.html
- How can I troubleshoot unhealthy Route 53 health checks? - https://repost.aws/knowledge-center/route-53-fix-unhealthy-health-checks
- Creating records by using the Amazon Route 53 console - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resource-record-sets-creating.html

## Additional References (from cases)

- [Creating health checks](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating.html)
- [ChangeResourceRecordSets API](https://docs.aws.amazon.com/Route53/latest/APIReference/API_ChangeResourceRecordSets.html)
- [Complex failover configs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-complex-configs.html)
- [ExternalDNS with health checks (GitHub)](https://github.com/kubernetes-sigs/external-dns/blob/master/docs/tutorials/aws.md#associating-dns-records-with-healthchecks)
- [Set up ExternalDNS in EKS](https://repost.aws/knowledge-center/eks-set-up-externaldns)
- [EKS DNS failure](https://repost.aws/knowledge-center/eks-dns-failure)
