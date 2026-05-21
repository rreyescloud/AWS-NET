# NET-004: Network Firewall TLS Inspection — Cross-Signed Certificate Rejection

## Status: In Progress

## Objective

Troubleshoot and document the limitations of cross-account certificate usage with AWS Network Firewall TLS Inspection, providing a clear path for enterprise multi-account architectures that require centralized certificate management with inbound traffic inspection.


## Business Context — Capital Markets / Financial Technology

Client in the capital markets fintech industry providing post-trade processing, securities lending, and fund administration platforms for banks and asset managers globally. Their workloads process sensitive financial transactions and require:

- **Regulatory compliance (APRA, FCA, PCI-DSS)** — Inbound TLS inspection to detect and prevent data exfiltration, malicious payloads, and unauthorized API calls reaching their trading platforms
- **Centralized security architecture** — Multi-account Landing Zone where certificates are managed centrally and distributed to workload accounts
- **Infrastructure-as-Code** — All deployments via Terraform through CI/CD pipelines (no manual console operations)
- **Defense in depth** — Network Firewall as the first layer of inspection before traffic reaches application load balancers and ECS services


## Use Cases

1. **Inbound TLS Inspection for Trading APIs** — Inspect HTTPS traffic from external clients (brokers, custodians) before it reaches post-trade processing systems to detect injection attacks, malformed payloads, or C2 traffic.

2. **Centralized Certificate Management** — Enterprise security team manages SSL certificates in a shared services account and distributes them to workload accounts for consistent TLS policies.

3. **Compliance Evidence** — Regulators require proof that encrypted traffic is inspected for threats. Network Firewall TLS Inspection provides this visibility without breaking end-to-end encryption at the application layer.

4. **Multi-Environment Consistency** — Same TLS inspection configuration across nonproduction and production environments, deployed via Terraform with consistent certificate policies.


## Problem Statement

Client is unable to use an imported ACM certificate for inbound TLS Inspection in AWS Network Firewall. The `CreateTLSInspectionConfiguration` API returns two distinct errors depending on how the certificate is imported:

1. `ServerCertificate chain is invalid and doesn't support the certificate body`
2. `ServerCertificate contains a root certificate that Network Firewall can't validate. Network Firewall can't validate cross-signed root certificates, such as Let's Encrypt certificates.`


## Environment

- Region: eu-west-1
- Deployment: Terraform via CodeBuild (CI/CD pipeline)
- TLS Inspection type: Inbound
- Scope: Private subnets → destination 0.0.0.0/0:443
- Total certificates in configuration: 4
- Environment type: nonproduction (shared)


## Customer Architecture

Client wants to:
1. Have ACM certificates in a central account (Account A)
2. Export/import those certificates into the workload account (Account B)
3. Use the imported certificates for Network Firewall inbound TLS Inspection


## Investigation Timeline

### Attempt 1 (12:55:12 UTC)
```
Error: ServerCertificate chain is invalid and doesn't support the certificate body
```
Certificate chain was incomplete or in wrong order during import.

### Attempt 2 (13:24:47 UTC)
```
Error: ServerCertificate contains a root certificate that Network Firewall can't validate.
Network Firewall can't validate cross-signed root certificates, such as Let's Encrypt certificates.
```
Client fixed the chain order, but NF now detects the root is cross-signed.


## Key Findings

1. **Network Firewall does not support cross-signed root certificates for IMPORTED certificates.** This includes Let's Encrypt (ISRG Root X1 cross-signed with DST Root CA X3) and any other externally-issued cert with a cross-signed chain.

2. **ACM public certificates (AMAZON_ISSUED) ARE the exception.** Per AWS documentation: "AWS Certificate Manager public certificates are cross-signed but can be used for TLS inspection." NF makes a special exception for AMAZON_ISSUED certs only.

3. **ACM public certificates cannot be exported with the private key.** You can export the cert body + chain, but not the key. Without the key, you cannot import it into another account for inbound TLS inspection (NF needs the private key to terminate TLS).

4. **ACM Private CA certificates CAN be exported with the private key** (protected with passphrase).

5. **The error is from Network Firewall, not ACM.** ACM imports the cert fine. NF rejects it at the TLSInspectionConfiguration creation.

6. **The client's cross-account approach is the root issue.** They cannot use a native ACM public cert cross-account (no private key export), so they imported an external cert which has a cross-signed root that NF rejects.

7. **Cross-signed certificates can cause asynchronous failures** even if they initially work — per AWS documentation. This is an additional risk.

### Certificate Compatibility Matrix

| Certificate Type | Cross-signed | Works in NF TLS Inspection |
|---|---|---|
| ACM public (AMAZON_ISSUED, same account) | Yes | YES (special exception) |
| Let's Encrypt (IMPORTED) | Yes | NO |
| Other imported cert with cross-signed root | Yes | NO |
| Imported cert WITHOUT cross-signed root | No | YES |
| ACM Private CA certificate | No | YES |


## Open Questions

- What is the `Type` of the cert in the source account? (AMAZON_ISSUED vs IMPORTED)
- Who is the issuer of the certificate? (Amazon, Let's Encrypt, DigiCert, etc.)
- Can the client issue a new ACM public cert directly in the workload account?
- Does their architecture require cross-account, or can they issue per-account?


## Possible Solutions

### Option A: Issue ACM public certificate directly in the target account
- Simplest path if they own the domain
- ACM public certs used natively (not imported) work with NF TLS Inspection
- No cross-account needed

### Option B: ACM Private CA + RAM sharing
- Create Private CA in central account
- Share via RAM to workload account
- Issue certificates directly in the target account
- Certificates will have a valid non-cross-signed chain

### Option C: ACM Private CA + export/import
- Create Private CA in central account
- Export cert with private key (passphrase protected)
- Import into target account ACM
- Use in NF TLS Inspection

### Option D: External CA (non-cross-signed)
- Use DigiCert, GlobalSign, Comodo, etc.
- Import with full chain (leaf + intermediate + direct root)
- No cross-signed roots in the chain


## References

[1] Network Firewall TLS Inspection certificate requirements
https://docs.aws.amazon.com/network-firewall/latest/developerguide/tls-inspection-certificate-requirements.html

[2] Network Firewall TLS Inspection certificate requirements — Inbound
https://docs.aws.amazon.com/network-firewall/latest/developerguide/tls-inspection-certificate-requirements.html#tls-inspection-certificate-requirements-inbound-inspection

[3] TLS Inspection considerations (cross-signed exception for ACM public certs)
https://docs.aws.amazon.com/network-firewall/latest/developerguide/tls-inspection-considerations.html

[4] ACM Export public certificate
https://docs.aws.amazon.com/acm/latest/userguide/export-public-certificate.html

[5] Network Firewall TLS Inspection configurations
https://docs.aws.amazon.com/network-firewall/latest/developerguide/tls-inspection-configurations.html

[6] ACM Private CA — Exporting certificates
https://docs.aws.amazon.com/privateca/latest/userguide/PcaExportCert.html

[7] RAM sharing for ACM Private CA
https://docs.aws.amazon.com/privateca/latest/userguide/pca-resource-sharing.html

[8] Using SSL/TLS certificates with TLS inspection configurations
https://docs.aws.amazon.com/network-firewall/latest/developerguide/tls-inspection-certificate-requirements.html
