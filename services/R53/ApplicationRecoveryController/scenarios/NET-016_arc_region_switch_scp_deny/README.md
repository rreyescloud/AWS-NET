# NET-016 — ARC Region Switch Blocked by Network-Perimeter SCP

**Tier:** Lab — reproducible end to end
**Status:** Deploy ready
**Services:** Application Recovery Controller (ARC) · IAM Policy Evaluation · Organizations SCPs · STS AssumeRole · API Gateway · CloudTrail

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2FApplicationRecoveryController%2Fscenarios%2FNET-016_arc_region_switch_scp_deny%2Farchitecture.drawio)

## Business Context

A global credit bureau and identity analytics provider operates five real-time verification
APIs serving financial institutions, insurers and telecoms. When a consumer applies for credit
or a business opens an account, the lender calls these APIs at the point of decision — the
response determines whether the transaction is approved, declined or flagged for manual review.

The APIs run active-passive across two AWS regions behind Route 53 failover records controlledz+++++
by ARC routing controls. A Region Switch plan automates the failover sequence: flip the routing
controls, fail over the Aurora Global Database, and update EKS workloads — all in a single
orchestrated execution.

The organization enforces a network-perimeter SCP that restricts routing-control operations to
requests originating from corporate IP ranges or VPC endpoints. This guardrail is sound: an
unauthorized `UpdateRoutingControlStates` call redirects every identity verification query the
platform serves. The problem is that ARC Region Switch assumes the execution role and operates
the routing controls from outside that perimeter — so the guardrail that protects against
unauthorized failover also blocks the authorized one.

## Problem Statement

Region Switch plan pre-validation passes but execution fails at the routing-control step with:

```
User: arn:aws:sts::ACCOUNT:assumed-role/EXECUTION-ROLE/RegionSwitchExecution-...
is not authorized to perform: route53-recovery-cluster:UpdateRoutingControlStates
with an explicit deny in a service control policy
```

**Root cause chain:**

1. The SCP denies `route53-recovery-cluster:*` unless the request comes from a known network
   (VPC endpoint or allowlisted source IP) or via an AWS service
2. ARC Region Switch uses `sts:AssumeRole` on the execution role — it gets its own session, not
   a forwarded credential
3. The resulting call appears as the role, not as a service — `aws:ViaAWSService` is false,
   `aws:CalledVia` is not populated, `aws:PrincipalIsAWSService` is false
4. Every condition in the SCP deny evaluates to true → deny fires
5. Pre-validation passed because `iam:SimulatePrincipalPolicy` does not evaluate SCPs

## What This Lab Proves

1. **The SCP blocks ARC** — deploy the SCP, create a Region Switch plan, execute it, observe the
   explicit deny in CloudTrail
2. **SimulatePrincipalPolicy misses it** — run the simulation, observe it passes despite the SCP
3. **The fix works** — add the `ArnNotLike` exemption, re-execute, observe success
4. **`aws:CalledVia` does NOT work** — verify it is not populated for ARC calls (AssumeRole
   pattern, not credential forwarding)

## Architecture

```
                     api.lab-fraud.example.com
                              │
                    Route 53 (failover)
                    ┌─────────┴─────────┐
                    │                   │
           HC (RECOVERY_CONTROL)  HC (RECOVERY_CONTROL)
           → rc-primary (ON)     → rc-secondary (OFF)
                    │                   │
           API GW us-east-1      API GW us-west-2
           (mock /verify)        (mock /verify)


    ARC Region Switch Plan
    ┌──────────────────────────────────────────────┐
    │ Step 1: UpdateRoutingControlStates           │
    │   rc-primary  → OFF                          │
    │   rc-secondary → ON                          │
    │                                              │
    │ Execution role: lab-region-switch-execution   │
    │   Trust: arc-region-switch.amazonaws.com      │
    └──────────────────────────────────────────────┘
                    │
                    ▼
    SCP: DenyRecoveryClusterUnlessKnownNetwork
    ┌──────────────────────────────────────────────┐
    │ Deny route53-recovery-cluster:*              │
    │ UNLESS:                                      │
    │   aws:SourceVpce = vpce-*           (no)     │
    │   aws:SourceIp in [corporate CIDRs] (no)     │
    │   aws:ViaAWSService = true          (no)     │
    │   aws:PrincipalIsAWSService = true  (no)     │
    │                                              │
    │ All conditions true → DENY                   │
    └──────────────────────────────────────────────┘
```

## Lab Resources

- **2 API Gateways** (us-east-1, us-west-2) — mock `/verify` endpoint returning `{"status":"ok","region":"<region>"}`
- **1 ARC cluster** with 1 control panel and 2 routing controls (primary/secondary)
- **2 Route 53 health checks** (type RECOVERY_CONTROL)
- **1 Route 53 hosted zone** with failover records
- **1 execution role** trusting `arc-region-switch.amazonaws.com`
- **1 Region Switch plan** referencing the routing controls
- **1 SCP** (simulated via IAM permission boundary in single-account lab) denying `route53-recovery-cluster:*` with network conditions

**Single-account SCP simulation:** since we cannot create a real SCP without an Organization
management account, we use an **IAM permission boundary** on the execution role that produces
the same deny effect. The CloudTrail error message differs (`permissions boundary` instead of
`service control policy`), but the policy evaluation logic and the fix are identical.

**Estimated cost:** ~$2.50/hour (ARC cluster dominates at $2.50/hr). Tear down same day.

## Files
por 
- `README.md` — this document
- `architecture.drawio` — the failover topology, the SCP condition block, and why each condition is true
- `lab/deploy_lab.py` — `deploy | test-deny | fix | test-allow | teardown`
- `lab/scp_policy.json` — the SCP / permission boundary document
+++
## References

- [ARC Region Switch](https://docs.aws.amazon.com/r53recovery/latest/dg/arc-region-switch.html)
- [Region Switch execution role](https://docs.aws.amazon.com/r53recovery/latest/dg/arc-region-switch.iam.html)
- [SCP evaluation — explicit Deny](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps_evaluation.html)
- [Policy evaluation logic](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)
- [aws:CalledVia, aws:ViaAWSService](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-keys.html)
- [SimulatePrincipalPolicy limitations](https://docs.aws.amazon.com/IAM/latest/APIReference/API_SimulatePrincipalPolicy.html)
- [Routing control data plane](https://docs.aws.amazon.com/r53recovery/latest/dg/routing-control.html)
