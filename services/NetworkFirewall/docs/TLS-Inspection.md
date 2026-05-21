# AWS Network Firewall - TLS Inspection

## Overview

TLS Inspection allows Network Firewall to decrypt, inspect, and re-encrypt TLS/SSL traffic. Without TLS inspection, NF can only see:
- IP addresses (Layer 3)
- Ports (Layer 4)
- TLS SNI field in the ClientHello (unencrypted)

With TLS inspection enabled, NF can inspect the **full payload** of encrypted traffic.

---

## How TLS Inspection Works

### Inbound TLS Inspection (Server Certificate)

NF terminates TLS using the server's certificate:

1. Client sends TLS ClientHello to your server (via NF)
2. NF intercepts and establishes a TLS session with the client using **your server's certificate + private key**
3. NF establishes a separate TLS session with your backend server
4. NF decrypts traffic from client, inspects it, re-encrypts it, and forwards to server
5. Responses follow the reverse path

**Flow:**
```
Client --[TLS]--> Network Firewall --[decrypted inspection]--> re-encrypts --[TLS]--> Server
```

**Use case:** Inspect HTTPS traffic destined for your own servers (e.g., behind ALB)

**Certificate requirement:** Server certificate + private key must be in ACM

### Outbound TLS Inspection (CA Certificate)

NF acts as a CA and generates certificates on-the-fly:

1. Internal client initiates HTTPS connection to external server
2. NF intercepts the TLS handshake
3. NF presents a **dynamically generated certificate** signed by your CA to the client
4. NF establishes a separate TLS session with the destination server
5. NF decrypts, inspects, and re-encrypts traffic in both directions

**Flow:**
```
Client --[TLS with NF-generated cert]--> Network Firewall --[decrypted inspection]--> --[TLS]--> External Server
```

**Use case:** Inspect outbound traffic from your VPC to the internet (egress filtering, DLP)

**Certificate requirement:** CA certificate in ACM that your clients trust

---

## Certificate Requirements Matrix

| Certificate Source | Type | Inbound TLS | Outbound TLS | Notes |
|-------------------|------|-------------|--------------|-------|
| ACM Public (AMAZON_ISSUED) | Public cert | Works | N/A | Works despite cross-signing; ACM manages renewal. NF has access to the private key through ACM integration |
| Let's Encrypt (imported to ACM) | Imported cert | Does NOT work | N/A | Fails - imported certs have private key handling limitations that prevent NF from accessing key material |
| ACM Private CA (aws-pca) | Private CA | Works | Works | Recommended for outbound TLS inspection. CA signs dynamically generated certs |
| External CA (non-cross-signed, imported) | Imported CA | Works | Works | Works because NF can use the CA key material directly. Must distribute CA cert to all clients |
| Self-signed CA (imported) | Imported CA | Works | Works | Must distribute CA cert to all client trust stores |

### Key Points

- **ACM AMAZON_ISSUED** certificates work for inbound TLS even though they are cross-signed. The important factor is that NF has native access to the private key through ACM's integration.
- **Let's Encrypt imported** certificates fail because imported certs in ACM have limitations on private key export/access that NF requires. NF needs native ACM integration to access key material.
- **ACM Private CA** is the recommended approach for outbound TLS inspection - it allows NF to dynamically sign certificates for any destination domain.
- **External non-cross-signed CAs** work when imported directly because NF can use the CA key material to generate per-session certificates.

---

## Cross-Account Certificate Issues

- TLS inspection configurations reference certificates in ACM
- If the certificate is in a different account than the firewall:
  - Use **AWS RAM** to share the ACM Private CA cross-account, OR
  - Replicate/import the certificate into the firewall's account
- Ensure the firewall's execution role has permissions to access the certificate
- Monitor certificate expiration: if the cert expires, TLS inspection fails silently (traffic may be dropped depending on your stream exception policy)

### Common Cross-Account Pitfalls

- Certificate ARN references pointing to wrong account
- Missing `acm:ExportCertificate` permissions
- RAM share not accepted in destination account
- Private CA subordinate not authorized in firewall account

---

## TLS Inspection Configuration in Firewall Policy

- TLS inspection is configured at the **Firewall Policy** level
- Changes to TLS inspection configuration **interrupt matching traffic flows**
- New TLS configuration applies to **new connections** only
- Plan maintenance windows for TLS config changes

### Stream Exception Policy

Defines behavior when TLS decryption fails:
- **DROP**: Drop traffic that cannot be decrypted (more secure)
- **CONTINUE**: Pass traffic without inspection (more permissive)
- **REJECT**: Send TCP RST to client

---

## AWS Network Firewall Proxy (Preview)

AWS announced the Network Firewall Proxy in preview (November 2025):

- The service is in **public preview** and is subject to change
- During the public preview period, the proxy is available **for free** in the **US East (Ohio) / us-east-2** region only
- The proxy provides forward proxy capabilities for HTTP/HTTPS traffic
- No published timeline for regional expansion to other regions (e.g., eu-west-1)
- Monitor AWS What's New announcements and the AWS Regional Services List for updates on regional availability
- Can be tested in us-east-2 to evaluate capabilities and prepare implementation for when it becomes available in other regions

### References for NF Proxy

- [Introducing AWS Network Firewall Proxy in preview](https://aws.amazon.com/about-aws/whats-new/2025/11/aws-network-firewall-proxy-preview/)
- [Architecture overview - Network Firewall Proxy](https://docs.aws.amazon.com/network-firewall/latest/developerguide/proxy-architecture-overview.html)

---

## Best Practices

1. **Use ACM Private CA** for outbound TLS inspection - simplifies certificate management
2. **Distribute the CA certificate** to all workloads that will have their traffic inspected
3. **Set appropriate stream exception policy** - decide whether to drop or pass traffic that cannot be decrypted
4. **Monitor certificate expiration** - set up CloudWatch alarms on ACM certificate expiry
5. **Test thoroughly** before enabling in production - TLS inspection can break applications that use certificate pinning
6. **Plan for exceptions** - some traffic (e.g., mutual TLS, certificate pinning) may need to bypass inspection
7. **Consider latency impact** - decrypt/re-encrypt adds processing overhead

---

## References

- [Firewall policy settings](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-policy-settings.html)
- [AWS Network Firewall Developer Guide](https://docs.aws.amazon.com/network-firewall/latest/developerguide/)
- [TLS inspection configuration](https://docs.aws.amazon.com/network-firewall/latest/developerguide/tls-inspection-configurations.html)
- [Logging with Server-Side Encryption and KMS](https://docs.aws.amazon.com/network-firewall/latest/developerguide/firewall-logging-encrypt-kms.html)
