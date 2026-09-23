# NET-007: DMS CDC Latency — Aurora MySQL → Kafka (14-27 min peaks)

**Tier:** Case analysis — no deploy script
**Status:** In Progress — network layer cleared, DMS investigation pending

## Objective

Investigate and resolve persistent CDC latency spikes in DMS replication from Aurora MySQL to Kafka (MSK), affecting both same-VPC and cross-VPC targets in dev and production environments.


## Business Context — Enterprise Accounting / Financial SaaS

Global enterprise accounting and financial management SaaS provider. Running ledger data replication from Aurora MySQL to Kafka for event streaming across microservices. Critical path for financial transaction processing.

Key requirements:
- **Real-time CDC** — Ledger entries must replicate to Kafka with minimal lag
- **Multi-environment** — Same architecture in sandbox (serverless) and production (provisioned)
- **Cross-account streaming** — Some Kafka targets are in different VPCs/accounts
- **High volume** — Large batch transactions (5M+ records) from ledger operations


## Problem Statement

DMS CDC replication from Aurora MySQL to Kafka experiencing escalating latency peaks:
- Started at 6 seconds (acceptable)
- Grew to 140 seconds (May 20)
- Now at **14 minutes** (local Kafka) and **27 minutes** (remote Kafka)
- Same issue reproduced in **production** with only 2 tables

All infrastructure fixes attempted (ACU increase, ParallelApplyBufferSize 100→500→1000, ParallelApplyQueuesPerThread 4→16) have NOT resolved the problem.


## Environment

### Sandbox (eu-central-1, account <CUSTOMER_SANDBOX_ACCOUNT_ID>)
- **Source:** Aurora MySQL Serverless v2, ACU=24
- **DMS:** dms.c5.4xlarge, 13 tasks (1 per table)
- **Local Kafka:** MSK (same VPC) — peaks 14 min
- **Remote Kafka:** MSK (cross-VPC/cross-account) — peaks 27 min
- **VPC:** <customer-vpc-id>

### Production (separate account)
- **Source:** Aurora MySQL db.r6x.8xlarge (provisioned)
- **DMS:** dms.r7i.4xlarge
- **Kafka:** MSK express.m7g.4xlarge
- **Task:** <dms-task-prod> (only 2 tables, still 14 min peaks)


## Related Cases

| Case | Owner | Focus |
|------|-------|-------|
| CASE-03 | DMS specialist | DMS task settings, CDC config, ParallelApply tuning |
| CASE-10 | Networking (this engineer) | Network path review, storage BW, VPC connectivity |
| CASE-11 | New (Production) | Same issue in prod with only 2 tables |


## Investigation Timeline

- **Day 1** — storage bandwidth saturated (12,540 exceeded events) → recommended an ACU increase
- **Day 4** — ACU raised to 24 → local latency improved, but only temporarily
- **Day 5** — networking engagement opened alongside the database one
- **Day 6** — remote Kafka latency spiked to 140s → batch idle plus cross-VPC amplification identified
- **Day 7** — `ParallelApplyBufferSize` raised to 500 → applied, no improvement
- **Day 8** — network path cleared and VPC peering offered as an alternative
- **Day 8** — reported worse: 14 min local, 27 min remote
- **Day 9** — same behaviour in production with only 2 tables and buffer at 1000 → every
  infrastructure fix exhausted, leaving the replication engine itself as the remaining suspect

Dates are relative and the engineers who worked each stage are not named; what matters for the
diagnosis is the order in which each layer was cleared.


## Key Finding — Network Layer Cleared

**The latency affects BOTH local (same VPC) and remote (cross-VPC) Kafka equally.**

This eliminates network as the root cause:
- If network → local would be fine, remote would be slow
- But BOTH are slow → bottleneck is BEFORE the network hop

Network evidence:
- ✅ storage_bw_out_allowance_exceeded = 0 (fixed with ACU 24)
- ✅ No packet loss or retransmissions in VPC path
- ✅ Cross-VPC connectivity functional (50s baseline is expected overhead)
- ✅ Same issue with local Kafka confirms network is not the cause


## Remaining Suspects (DMS-side)

1. **DMS SOURCE_CAPTURE behavior** — How DMS reads binlog during large transactions. If a transaction writes 5M+ records, DMS waits for COMMIT before processing → appears as latency spike.

2. **LOB handling** — Customer has JSON columns. LobMaxSize reduced to 32KB may cause extra lookups back to source for large JSON values — flagged as a risk during the
   database-side review.

3. **DMS internal batching** — TARGET_APPLY batching to Kafka may be accumulating too many records before flushing. ParallelApplyBufferSize=1000 might be TOO HIGH for this workload.

4. **Kafka producer config** — DMS uses internal Kafka producer settings (batch.size, linger.ms, acks). Large batches + high linger = delayed writes.

5. **Single-table-per-task architecture** — 13 tasks competing for DMS instance resources. Each task has its own thread pool but shares the instance.


## Next Steps

- [x] Respond to customer acknowledging the issue is NOT network
- [ ] Hand off to the database specialty — DMS needs deeper investigation (task logs, SOURCE vs
      TARGET timing)
- [ ] Suggest DMS service team escalation if tuning doesn't resolve
- [ ] Close networking case with summary


## Lab Notes (2026-05-23)

Attempted to replicate in eu-central-1 (<LAB_ACCOUNT_ID>):
- Aurora MySQL Serverless v2 (8.0.mysql_aurora.3.08.2) — deployed OK
- MSK Serverless — deployed OK but DMS doesn't support IAM auth directly (needs SASL/SCRAM)
- DMS t3.medium — deployed OK, source connection to Aurora successful
- S3 as alternative target — required VPC Gateway Endpoint for S3 access
- Lambda in VPC for data loading — worked (created table + 1000 rows)

### Lessons from lab setup:
1. DMS in private VPC needs S3 VPC Gateway Endpoint to reach S3 targets
2. MSK Serverless (IAM auth) is NOT directly compatible with DMS — need MSK Provisioned with SASL/SCRAM
3. Lambda in VPC takes ~60s to provision ENI (Pending state)
4. DMS CDC task with S3 target doesn't support ParallelApply settings
5. Aurora cross-AZ to DMS is normal (~1ms extra, not the cause of large latency spikes)


## References

[1] DMS CDC Best Practices
https://docs.aws.amazon.com/dms/latest/userguide/CHAP_BestPractices.html

[2] DMS LOB Support
https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Tasks.LOBSupport.html

[3] Aurora Enhanced Monitoring
https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/USER_Monitoring.OS.html

[4] MSK Best Practices
https://docs.aws.amazon.com/msk/latest/developerguide/bestpractices.html

[5] DMS CDC Latency Troubleshooting
https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Troubleshooting_Latency.html

[6] DMS Target Latency Troubleshooting
https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Troubleshooting_Latency_Target.html

[7] DMS Kafka Target Configuration
https://docs.aws.amazon.com/dms/latest/userguide/CHAP_Target.Kafka.html

[8] re:Post — Aurora MySQL to S3 with DMS unsuccessful load test
https://repost.aws/questions/QUsBoIFzs7TLi_gvkkn838MA/aurora-mysql-to-s3-with-dms-unsuccessful-load-test

[9] DMS Best Practices — Performance
https://docs.aws.amazon.com/dms/latest/userguide/CHAP_BestPractices.html#CHAP_BestPractices.Performance
