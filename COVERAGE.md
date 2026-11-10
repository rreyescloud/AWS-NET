# Service Coverage

Each scenario focuses on a primary networking service but touches others as part of the
architecture. This index shows which services appear across the portfolio and in what capacity.

## Identity & Access Management

- **IAM Policy Evaluation** — NET-016: SCP explicit deny vs identity policy, permission boundaries as SCP simulation, SimulatePrincipalPolicy gap
- **STS AssumeRole** — NET-016: credential forwarding vs role assumption patterns, why `aws:CalledVia` and `aws:ViaAWSService` behave differently
- **Organizations SCPs** — NET-016: network-perimeter SCP blocking service-assumed roles
- **IAM SLR** — NET-002: service-linked role for Vended Logs delivery, resource policy on destination
- **Cross-account IAM** — NET-001: S3 bucket policy for cross-account access via VPC endpoint

## Compute & Containers

- **EKS** — NET-010: VPC CNI with external SNAT disabled, container attribute referencing; NET-009: Karpenter reconciliation loops, zonal shift recovery; NET-025: AWS Load Balancer Controller owning the ALB web ACL association (`wafv2-acl-arn` annotation)
- **EC2** — NET-004, NET-005, NET-011, NET-015, NET-017: test instances, SSM Session Manager access; NET-029: SSM `send-command` test client behind the firewall
- **Lambda** — NET-006: Bedrock-backed chatbot function behind API Gateway; NET-023: custom OAuth token endpoint enforcing a per-account failure limit

## Database & Streaming

- **Aurora Global Database** — NET-003: managed planned failover during ARC Region Switch
- **Aurora MySQL** — NET-007: Serverless v2 ACU sizing, storage bandwidth saturation
- **DMS** — NET-007: CDC replication to Kafka, ParallelApply tuning, LOB handling
- **MSK (Kafka)** — NET-007: cross-VPC target latency, SASL/SCRAM vs IAM auth with DMS
- **Amazon Data Firehose** — NET-028: WAF log delivery stream (`aws-waf-logs-` prefix); ingestion cost shaped by the WAF logging filter, dropped records never delivered
- **DynamoDB** — NET-020: 1 MB Scan page applied before the filter, `Scan` + filter vs a `Status` GSI, history under TTL inflating a table-wide read; NET-023: per-account login failure counter (atomic ADD, one-minute window, TTL cleanup)

## Application Integration

- **API Gateway** — NET-006: REST API with WAF integration; NET-023: REGIONAL web ACL sees the request only, never the Lambda response; NET-016: mock regional endpoints for failover testing
- **CloudFront** — NET-006: WAF-protected distribution; NET-012, NET-014: origin for WAF evaluation; NET-020: OAI to the STNO console bucket; NET-022: two distributions on one S3 origin, one web ACL each for a side-by-side comparison; NET-024: custom error response for 403 as the single block page (precedence below WAF custom responses); NET-026: CLOUDFRONT-scope web ACL in front of ALBs, origin-facing prefix list + secret header against direct-to-origin bypass, standard logs for scan impact; NET-027: edge-generated 403 (`X-Cache: Error from cloudfront`) identified from a HAR
- **AppSync** — NET-020: VTL DynamoDB Scan resolver, `limit`/`nextToken` pagination contract between resolver and client
- **Step Functions** — NET-020: STNO approval workflow driving TGW associate/propagate
- **EventBridge** — NET-020: custom bus with per-spoke-account rules reacting to VPC and subnet tags
- **Cognito** — NET-020: user and identity pool fronting the STNO console, Admin vs ReadOnly groups

## Monitoring & Logging

- **CloudTrail** — NET-009: zonal shift timeline reconstruction; NET-016: denied-call forensics; NET-027: `UpdateWebACL` payload diff to date a rule action override change
- **CloudWatch Logs** — NET-002: query log destination and Insights queries; NET-011, NET-015: resolver query logging; NET-022: WAF logs per web ACL, labels compared per User-Agent by run tag; NET-029: Network Firewall FLOW/ALERT logs, where `ALERT` mode tags connections that fail resolved IP validation
- **CloudWatch Metrics** — NET-007: storage bandwidth exceeded events; NET-012: per-rule WAF metrics during DDoS simulation; NET-028: `CountedRequests` vs `AllowedRequests` to size the extra log volume before a filter change

## Networking (secondary to the primary service)

- **Transit Gateway** — NET-005: centralized inspection with RNAT; NET-011: spoke/inspection VPC topology; NET-017: appliance mode flow hashing, TGW-attached firewall routing; NET-020 *(primary)*: hub-and-spoke across 150+ accounts, segmentation by route table, approval-gated attachment lifecycle, orchestrator vs network state drift
- **VPC Peering** — NET-007: cross-VPC Kafka latency; NET-015: cross-region for resolver forwarding chain
- **VPC Endpoints** — NET-001: S3 gateway endpoint; NET-015: SSM interface endpoints in private subnets, endpoint-managed PHZs outranking an `amazonaws.com` forwarding rule
- **Regional NAT Gateway** — NET-005: zonal affinity, RNAT route table, chaining with firewall; NET-017: single route table vs multi-AZ firewall, manual mode EIPs per AZ, zonal NAT replacement
- **VPC Routing** — NET-005: per-AZ return routing; NET-011: TGW + firewall + NAT insertion order; NET-017: why per-subnet return routes fail with appliance mode
- **Route 53 Resolver** — NET-029: VPC Resolver answers recorded by Network Firewall as IP ↔ domain provenance; DoH, external DNS and unassociated VPCs fail validation

## Security Services

- **Shield Advanced** — NET-012: L7AM vs AntiDDoS AMR activation parity, known-offender rate-based rule
- **WAF Bot Control** — NET-006: targeted bot management; NET-014: Monetize action for AI traffic; NET-022 *(primary)*: unpinned default version (1.0) leaves Claude crawlers unclassified, `CategoryAI` blocking verified AI bots, label-based allow for LLM crawlers
- **WAF ATP** — NET-023 *(primary)*: `FORM_ENCODED` login inspection, `AttributePasswordTraversal`, response inspection limited to CloudFront, no body-based rate-based key
- **DNS Firewall** — NET-011: related pattern (domain allowlisting), planned SEC-001; NET-017: Firewall Manager DNS Firewall policy as interim egress control; NET-029: rule group used as the anchor (`DNSFirewallRuleGroupId`) that defines which VPCs' resolutions count for Network Firewall resolved IP validation
- **Firewall Manager** — NET-017: centralized Network Firewall model limits (no route management, IGW-only monitoring, no TGW-attached firewalls); NET-022: FMS-managed web ACL, pre/post-process rule group slots; NET-024: `RETROFIT_EXISTING` evaluation order (preProcess → account rules → postProcess), `ruleActionOverrides` custom responses silently removed by `PutPolicy` (lab-verified), admin-account rule group with its own `CustomResponseBodies` as the central fix

## Directory & Hybrid Identity

- **DHCP Option Sets** — NET-015: `domain-name` and `ndots` deciding the queried name, short-name NXDOMAIN, EKS `ndots:5` wasted queries, internal names leaking to public DNS

## Other

- **ACM** — NET-004: cross-signed certificate import rejection, TLS inspection configuration
- **RAM** — NET-002: sharing query log configs across accounts; NET-017: org resource sharing required by Firewall Manager policies; NET-020: TGW shared to 150+ spoke accounts with external principals allowed
- **S3** — NET-001: cross-account private access; NET-014: origin for monetized content; NET-020: console SPA behind CloudFront OAI; NET-022: private origin behind Origin Access Control; NET-028: WAF log archive behind Firehose, storage cost driven by logging filter
- **Direct Connect** — NET-020: DXGW attachment on the hub TGW, propagation into the on-premises route table
- **AWS Solutions** — NET-020: Network Orchestration for AWS Transit Gateway (SO0058) v3.3.28, upstream bug triage and upgrade ownership
- **Blockchain** — NET-014: EIP-3009 USDC transfer on Base Sepolia, web3.py
