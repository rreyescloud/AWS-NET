# Route 53 Integrations

## ACM (AWS Certificate Manager)

- Route 53 is used for DNS validation of ACM certificates
- Create a CNAME record in Route 53 to validate domain ownership
- ACM can automatically create the validation record if the domain is in a Route 53 hosted zone
- Reference: https://docs.aws.amazon.com/es_es/acm/latest/userguide/dns-validation.html

## CloudFront

- Use Route 53 alias records to point to CloudFront distributions
- CloudFront distributions require alternate domain names (CNAMEs) configured
- ACM certificates for CloudFront must be in us-east-1

## ECS (Elastic Container Service)

- DNS resolution for ECS tasks uses VPC DNS resolver
- Service discovery uses Route 53 Auto Naming (Cloud Map)
- Troubleshoot latency: https://repost.aws/knowledge-center/ecs-task-latency-troubleshooting
- Troubleshoot high response times: https://repost.aws/knowledge-center/ecs-troubleshoot-high-response-times

## EKS (Elastic Kubernetes Service)

### CoreDNS
- EKS uses CoreDNS for in-cluster DNS resolution
- CoreDNS forwards external queries to VPC DNS resolver
- external-dns helm chart creates Route 53 records: https://github.com/kubernetes-sigs/external-dns/blob/v0.15.0/docs/tutorials/aws.md

### Troubleshooting EKS DNS
```bash
# Verify CoreDNS pods
kubectl get pods -n kube-system -l k8s-app=kube-dns

# Check CoreDNS logs
kubectl logs -n kube-system -l k8s-app=kube-dns
```

- How do I troubleshoot DNS failures with Amazon EKS? - https://repost.aws/knowledge-center/eks-dns-failure

### Failover Routing with EKS
For failover routing policy with external-dns helm, health check ID is mandatory for the primary region. For setups with AWS Load Balancer as backend, the health check of the backend is done through AWS backbone and does not require a custom health check. A dummy UUID for health check ID may be used but can cause duplicate health check ID errors.

## SES (Simple Email Service)

- Route 53 is used to configure domain verification (TXT records)
- DKIM setup requires CNAME records
- SPF configuration uses TXT records
- MX records for receiving email

## Lambda

- Lambda functions in a VPC use the VPC DNS resolver
- Custom domains for Lambda function URLs use Route 53
- Lambda@Edge can be used with CloudFront + Route 53

## RDS

- Route 53 can distribute read requests across multiple RDS read replicas using weighted routing
- Reference: https://repost.aws/knowledge-center/requests-rds-read-replicas

## ElastiCache

- Troubleshooting server-side latency: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/wwe-troubleshooting.html#wwe-troubleshooting.latency
- How do I troubleshoot high latency issues in ElastiCache for Redis? - https://repost.aws/knowledge-center/elasticache-redis-correct-high-latency

## S3 (Static Website Hosting)

- Use alias records to point to S3 website endpoints
- Bucket name must match the domain name for alias records
- Configure website hosting with redirect for domain redirects
- Reference: https://docs.aws.amazon.com/AmazonS3/latest/userguide/website-hosting-custom-domain-walkthrough.html

## ELB (Elastic Load Balancing)

- Use alias records to point to ELB DNS names
- Route 53 evaluates the health of the ELB and its targets
- How can I use an ALB to redirect one domain to another? - https://repost.aws/knowledge-center/elb-redirect-to-another-domain-with-alb

## Additional References (from cases)

### CloudFront + S3 Redirect
- [Redirect domain with Route 53](https://repost.aws/knowledge-center/redirect-domain-route-53)
- [Redirect to another domain](https://repost.aws/knowledge-center/route-53-redirect-to-another-domain)
- [S3 page redirect](https://docs.aws.amazon.com/AmazonS3/latest/userguide/how-to-page-redirect.html)
- [Routing to CloudFront distribution](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-to-cloudfront-distribution.html)
- [S3 website hosting custom domain](https://docs.aws.amazon.com/AmazonS3/latest/dev/website-hosting-custom-domain-walkthrough.html#root-domain-walkthrough-configure-redirect)
- [CloudFront HTTPS requests to S3](https://repost.aws/knowledge-center/cloudfront-https-requests-s3)

### DNS Firewall
- [Resolver DNS Firewall managed domain lists](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-dns-firewall-managed-domain-lists.html)
