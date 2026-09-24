# NET-009 — EKS Zonal Shift Recovery Delay (~24 min after expiry)

**Tier:** Case analysis — no deploy script
**Status:** Root cause established from customer-observable evidence, lab replication planned
**Services:** Application Recovery Controller (ARC) Zonal Shift · EKS · Karpenter · CloudTrail · Kubernetes (EndpointSlices, taints)

## Business Context

A financial services organization running Amazon EKS with Karpenter across three Availability Zones was evaluating ARC Zonal Shift for production disaster recovery, to steer traffic away from an impaired AZ automatically.

During pre-production validation they ran a **1-minute manual zonal shift** to confirm the mechanism worked end to end. ARC started and expired the shift correctly, but the Kubernetes-level recovery — nodes uncordoned, pod endpoints restored to EndpointSlices, scheduling re-enabled — took roughly **24 minutes**. That blocked the production adoption decision: they needed recovery time to be predictable and bounded before enabling zonal shift on clusters serving end users.

## Problem Statement

After a zonal shift expires, EKS takes far longer than expected to return the shifted AZ to full operation.

- **Expected** — recovery within seconds to a couple of minutes after expiry
- **Observed** — ~24 minutes before nodes were uncordoned and EndpointSlices updated

The gap matters because a DR control you cannot time is a DR control you cannot rely on.

## Root Cause

Zonal shift recovery in EKS is **polling-driven, not event-driven**. Nothing pushes a notification from ARC to the cluster when a shift expires. Instead, several independent components each discover the change on their own schedule, and every hop in that chain contributes its own latency:

1. **ARC** marks the shift expired — this happens server-side and immediately
2. **EKS** discovers the expiry by polling and begins removing the AZ restriction
3. **EKS control plane** applies the change: node taints removed, nodes uncordoned
4. **Karpenter** independently reconciles, sees the zone is no longer restricted, and resumes provisioning there
5. **Reconciliation loops** run on longer intervals than the steps above, so a change missed by one pass waits for the next

Because these are serial and independent, the worst case is not the slowest step — it is the sum of every interval, plus any loop that has to wait a full cycle. That is what turns "a few minutes" into tens of minutes.

Two factors made this particular test worse than a production shift would be:

**The 1-minute shift is an edge case.** The shift expired before the chain had finished reacting to its start, so recovery work overlapped with activation work. AWS documentation explicitly recommends allowing at least 60 seconds between zonal shift operations precisely because of the polling mechanism — a 1-minute shift sits right at that boundary.

**Recovery exceeded the shift duration.** When a shift lasts less time than the system takes to converge, the measurement no longer describes steady-state recovery; it describes two overlapping transitions.

## Architecture

[📐 Open diagram in draw.io](https://app.diagrams.net/#Uhttps%3A%2F%2Fraw.githubusercontent.com%2Frreyescloud%2FAWS-NET%2Fmain%2Fservices%2FR53%2Fscenarios%2FNET-009_eks_zonal_shift_recovery_delay%2Farchitecture.drawio)

```
EKS cluster, three AZs, Karpenter-provisioned compute (no managed node groups)
Zonal shift registered against the EKS cluster resource

        ARC Zonal Shift
              │  (expiry is server-side, no event emitted)
              ▼
        EKS  ── polls ──▶ removes AZ restriction
              │
              ▼
        EKS control plane ──▶ remove taints, uncordon nodes
              │
              ▼
        Karpenter ── polls ──▶ resume provisioning in the AZ
              │
              ▼
        EndpointSlices repopulated ──▶ traffic returns
```

Each arrow is a separate polling loop. The total is additive.

## Key Details

- **Services** — Amazon EKS with ARC Zonal Shift
- **Compute** — Karpenter v1.12+ provisioning all nodes, no managed node groups
- **Shift type** — manual, 1-minute expiry
- **Observed recovery** — ~24 minutes to full restoration
- **Recovery model** — polling-based, not event-driven

## CloudTrail Events to Monitor

These are the customer-visible signals that let you time each stage yourself:

- **`StartZonalShift`** (`arc-zonal-shift.amazonaws.com`) — when the shift began
- **`GetManagedResource`** (`arc-zonal-shift.amazonaws.com`) — recurring polls; the cadence reveals the polling interval in play
- **`UpdateNodegroupConfig`** (`eks.amazonaws.com`) — when EKS re-enabled the AZ
- **`SuspendProcesses`** / **`ResumeProcesses`** (`autoscaling.amazonaws.com`) — when AZ rebalance was suspended and restored
- **`RunInstances`** (`ec2.amazonaws.com`) — Karpenter launching replacement nodes

**Important gotcha:** natural shift expiry does **not** generate a CloudTrail event. The shift is removed server-side, so if you are building a timeline, the expiry is the one moment you have to infer rather than read. Anchor on `StartZonalShift` plus the configured duration instead.

## Kubernetes Observability

```bash
# Watch endpoint slices during the shift
kubectl get endpointslices --all-namespaces \
  -l 'eks-arc-zonal-shift/impaired-zone=<az-id>'

# Watch node taints appear and clear
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,\
TAINTS:.spec.taints \
  | grep "eks-arc-zonal-shift/impaired-zone"

# Karpenter reconciliation
kubectl logs -n kube-system -l app.kubernetes.io/name=karpenter \
  | grep -iE "zonal|shift|cleared|reconcil"

# Cluster events, newest last
kubectl get events --all-namespaces --sort-by='.lastTimestamp' \
  | grep -iE "cordon|taint|zonal"
```

## Recommendations

1. **Test with shifts of 30 minutes or more.** This removes the edge case where recovery outlasts the shift itself and lets you measure steady-state convergence.
2. **Use zonal autoshift practice runs.** They exist for exactly this kind of validation and exercise the same path without an arbitrary short expiry.
3. **Alert on EndpointSlice label removal, not on shift expiry.** The label clearing is the signal that traffic is genuinely back; the expiry only means ARC has stopped advertising the shift.
4. **Budget recovery as additive, not parallel.** When sizing your RTO, sum the polling intervals in the chain rather than assuming the slowest single step dominates.
5. **If a long shift still takes ~24 minutes,** open a support case with the CloudTrail timeline and Karpenter logs attached — the per-stage timestamps are what make the delay actionable.

## Lab Replication Plan

- [ ] Create an EKS cluster with Karpenter in a test account
- [ ] Register zonal shift on the cluster resource
- [ ] Run a manual shift with 30-minute expiry and measure recovery precisely
- [ ] Capture the CloudTrail timeline across the full window
- [ ] Capture the Kubernetes event and EndpointSlice timeline
- [ ] Repeat with a 1-minute shift to confirm the short-shift edge case
- [ ] Publish the measured per-stage breakdown

## References

- [ARC Zonal Shift in EKS](https://docs.aws.amazon.com/eks/latest/userguide/zone-shift.html)
- [How a zonal shift works](https://docs.aws.amazon.com/r53recovery/latest/dg/arc-zonal-shift.how-it-works.html)
- [Best practices for zonal shifts](https://docs.aws.amazon.com/r53recovery/latest/dg/route53-arc-best-practices.zonal-shifts.html)
- [Karpenter AWS provider](https://github.com/aws/karpenter-provider-aws)
- [Operating resilient workloads on Amazon EKS](https://aws.amazon.com/blogs/containers/operating-resilient-workloads-on-amazon-eks)
