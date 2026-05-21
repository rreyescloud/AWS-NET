# Route 53 Domains

## Domain Registration

- Route 53 can be used as a domain registrar
- When you register a domain, Route 53 automatically creates a public hosted zone with the same name
- Registration agreement: https://aws.amazon.com/route53/domain-registration-agreement/

### Registration Process
1. Choose a domain name and verify availability
2. Register the domain (Route 53 creates a hosted zone)
3. Confirm email address (registrant contact)
4. Domain becomes active

References:
- To register a new domain using Route 53 - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-register.html
- Resending authorization and confirmation emails - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-click-email-link.html

## Domain Transfer

### Transfer TO Route 53
- Domain must be unlocked at current registrar
- Must have authorization code from current registrar
- Domain must not be within 60 days of initial registration or previous transfer

**Best Practice for Transfers:**
1. Transfer the DNS hosted zone first (update nameservers at current registrar)
2. Wait for DNS cache to clear
3. Then proceed with domain registration transfer

This ensures traffic is directed correctly during the transfer process.

### Transfer FROM Route 53
1. Remove `clientTransferProhibited` status (disable transfer lock)
2. Get authorization code from Route 53 console
3. Initiate transfer at new registrar

References:
- How do I remove the "clientTransferProhibited" status? - https://repost.aws/knowledge-center/route-53-fix-clienttransferprohibited
- Transferring a domain from Route 53 to another registrar - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-transfer-from-route-53.html
- Viewing the status of a domain transfer - https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-transfer-to-route-53-status.html

## DNSSEC

DNSSEC (DNS Security Extensions) adds authentication to DNS responses, protecting against DNS spoofing and cache poisoning.

### Key Points
- Route 53 supports DNSSEC for domain registration and DNS signing
- Can be enabled on public hosted zones
- Adds digital signatures to DNS records

References:
- Step-by-step guide to troubleshoot DNSSEC issues - https://repost.aws/knowledge-center/route-53-fix-dnssec-issues

## Delegation Sets

- A delegation set is the set of four nameservers assigned to a hosted zone
- Reusable delegation sets allow you to use the same set of nameservers for multiple hosted zones
- Useful when migrating many domains to Route 53

## White-Label Nameservers

- You can configure custom nameserver names (e.g., ns1.example.com instead of ns-xxx.awsdns-xx.com)
- You can change the name of the servers, but not the servers (IPs) themselves
- Reference: https://repost.aws/knowledge-center/route53-white-label-name-server

## Domain with Closed Account

- If your AWS account is closed or permanently closed, your domain is still registered with Route 53
- You need to reopen the account or transfer the domain before permanent closure
- Reference: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/troubleshooting-account-closed.html

## ccTLDs (Country Code Top-Level Domains)

- Some ccTLDs have specific requirements for registration
- Transfer processes may vary by ccTLD
- Some ccTLDs require local presence or specific documentation

## Domain Redirect

To redirect a domain to another domain:
1. Create an S3 bucket configured for website hosting with redirect
2. Create a Route 53 alias record pointing to the S3 bucket
3. (Optional) Use CloudFront for HTTPS redirect

References:
- Redirect a domain to another domain using Route 53 - https://repost.aws/knowledge-center/redirect-domain-route-53
- Redirect one domain to another in Route 53 - https://repost.aws/knowledge-center/route-53-redirect-to-another-domain
- Configuring a webpage redirect (S3) - https://docs.aws.amazon.com/AmazonS3/latest/userguide/how-to-page-redirect.html#redirect-endpoint-host
- Tutorial: Configuring a static website using a custom domain registered with Route 53 - https://docs.aws.amazon.com/AmazonS3/latest/userguide/website-hosting-custom-domain-walkthrough.html#root-domain-walkthrough-configure-redirect
- How can I use an ALB to redirect one domain to another? - https://repost.aws/knowledge-center/elb-redirect-to-another-domain-with-alb

## Additional References (from cases)

- [Register a domain](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-register.html)
- [Click email link (ICANN verification)](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-click-email-link.html)
- [Update domain contacts](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-update-contacts.html)
- [Transfer domain from Route 53](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/domain-transfer-from-route-53.html)
- [Troubleshooting closed account domains](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/troubleshooting-account-closed.html)
- [Fix clientTransferProhibited](https://repost.aws/knowledge-center/route-53-fix-clienttransferprohibited)
- [DNSViz (DNSSEC visualization tool)](https://dnsviz.net/)
- [Pre-cutover stage best practices](https://docs.aws.amazon.com/prescriptive-guidance/latest/best-practices-migration-cutover/pre-cutover-stage.html)
