# VPC — Service Integrations

## ELB (Elastic Load Balancing)

- ALB/NLB deployed in VPC subnets
- ALB: Layer 7 (HTTP/HTTPS), requires at least 2 AZs
- NLB: Layer 4 (TCP/UDP), static IPs, ultra-low latency
- GWLB: Layer 3 (for inline appliances like Network Firewall)
- Cross-zone load balancing: distributes traffic evenly across AZs

## ECS (Elastic Container Service)

- **awsvpc network mode**: each task gets its own ENI with private IP
- Tasks in private subnets need NAT or PrivateLink for ECR/CloudWatch
- Required endpoints for private ECS: ecr.api, ecr.dkr, s3 (gateway), logs, ecs-agent

## Lambda

- Can run inside VPC (attached to subnets via ENI)
- Needs NAT Gateway or PrivateLink for internet/AWS service access
- ENI creation adds cold start latency (~1-2s first invocation)
- Use VPC endpoints for S3, DynamoDB, SQS to avoid NAT

## RDS

- Always deployed in VPC (DB Subnet Group = 2+ subnets in different AZs)
- Not publicly accessible by default (must enable + configure SG)
- Multi-AZ: standby in different AZ (same VPC)
- Cross-region read replicas: different VPC/region, needs network path

## API Gateway

- **Regional API**: deployed in a region, accessible from internet
- **Private API**: only accessible from within VPC via Interface Endpoint
- Private API requires: VPC endpoint for execute-api + resource policy

```bash
aws ec2 create-vpc-endpoint \
  --service-name com.amazonaws.us-east-1.execute-api \
  --vpc-endpoint-type Interface \
  --private-dns-enabled
```

## EC2

- Instances live in subnets, get ENI with private IP
- Public IP: auto-assign or EIP
- Placement groups: cluster (low latency), spread (HA), partition
- Enhanced networking: ENA for up to 100 Gbps

## App Runner

- Can connect to VPC resources via VPC Connector
- Outbound only (App Runner → VPC resources)
- Needs VPC Connector specifying subnets and security groups

## CloudFormation

- VPC endpoint available for private deployments
- `AWS::EC2::VPC` resource for IaC

## References

- [ELB documentation](https://docs.aws.amazon.com/elasticloadbalancing/latest/userguide/what-is-load-balancing.html)
- [ECS networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking.html)
- [Lambda VPC](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html)
- [RDS in VPC](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.html)
- [Private API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html)
- [App Runner VPC Connector](https://docs.aws.amazon.com/apprunner/latest/dg/network-vpc.html)

## Additional References (from cases)

### Lambda in VPC
- [Lambda intermittent DNS errors](https://www.repost.aws/knowledge-center/lambda-intermittent-consistent-dns-error)
- [Lambda VPC troubleshoot timeout](https://repost.aws/knowledge-center/lambda-vpc-troubleshoot-timeout)
- [Lambda troubleshoot function failures](https://repost.aws/knowledge-center/lambda-troubleshoot-function-failures)
- [Lambda networking troubleshooting](https://docs.aws.amazon.com/lambda/latest/dg/troubleshooting-networking.html)

### DNS in VPC
- [VPC DNS settings](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-dns.html)
- [Route 53 private hosted zone failover](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-private-hosted-zones.html)
- [Health checks with CloudWatch](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/health-checks-creating-cloudwatch.html)
- [High availability design for Oracle Data Guard with Route 53](https://aws.amazon.com/blogs/architecture/setup-a-high-availability-design-for-oracle-data-guard-fast-start-failover-using-amazon-route-53/)
- [Simple failover configs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-simple-configs.html)

### DMS
- [DMS endpoints](https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Endpoints.html)
