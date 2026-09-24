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

- **EKS** — NET-010: VPC CNI with external SNAT disabled, container attribute referencing; NET-009: Karpenter reconciliation loops, zonal shift recovery
- **EC2** — NET-004, NET-005, NET-011, NET-015: test instances, SSM Session Manager access
- **Lambda** — NET-006: Bedrock-backed chatbot function behind API Gateway

## Database & Streaming

- **Aurora Global Database** — NET-003: managed planned failover during ARC Region Switch
- **Aurora MySQL** — NET-007: Serverless v2 ACU sizing, storage bandwidth saturation
- **DMS** — NET-007: CDC replication to Kafka, ParallelApply tuning, LOB handling
- **MSK (Kafka)** — NET-007: cross-VPC target latency, SASL/SCRAM vs IAM auth with DMS

## Application Integration

- **API Gateway** — NET-006: REST API with WAF integration; NET-016: mock regional endpoints for failover testing
- **CloudFront** — NET-006: WAF-protected distribution; NET-012, NET-014: origin for WAF evaluation

## Monitoring & Logging

- **CloudTrail** — NET-009: zonal shift timeline reconstruction; NET-016: denied-call forensics
- **CloudWatch Logs** — NET-002: query log destination and Insights queries; NET-011, NET-015: resolver query logging
- **CloudWatch Metrics** — NET-007: storage bandwidth exceeded events; NET-012: per-rule WAF metrics during DDoS simulation

## Networking (secondary to the primary service)

- **Transit Gateway** — NET-005: centralized inspection with RNAT; NET-011: spoke/inspection VPC topology
- **VPC Peering** — NET-007: cross-VPC Kafka latency; NET-015: cross-region for resolver forwarding chain
- **VPC Endpoints** — NET-001: S3 gateway endpoint; NET-015: SSM interface endpoints in private subnets
- **Regional NAT Gateway** — NET-005: zonal affinity, RNAT route table, chaining with firewall
- **VPC Routing** — NET-005: per-AZ return routing; NET-011: TGW + firewall + NAT insertion order

## Security Services

- **Shield Advanced** — NET-012: L7AM vs AntiDDoS AMR activation parity, known-offender rate-based rule
- **WAF Bot Control** — NET-006: targeted bot management; NET-014: Monetize action for AI traffic
- **DNS Firewall** — NET-011: related pattern (domain allowlisting), planned SEC-001

## Directory & Hybrid Identity

- **AWS Managed Microsoft AD** — NET-015: domain join failure caused by missing forwarding rule association and DHCP search domain suffix appending
- **DHCP Option Sets** — NET-015: search domain suffix corrupting DNS queries, producing intermittent NXDOMAIN during AD domain join

## Other

- **ACM** — NET-004: cross-signed certificate import rejection, TLS inspection configuration
- **RAM** — NET-002: sharing query log configs across accounts; NET-015: forwarding rule sharing and the association gap when a shared rule is replaced
- **S3** — NET-001: cross-account private access; NET-014: origin for monetized content
- **Blockchain** — NET-014: EIP-3009 USDC transfer on Base Sepolia, web3.py
