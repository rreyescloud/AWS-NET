# NET-009 — EKS Zonal Shift Recovery Delay (~24 min post-expiry)

**Tier:** Case analysis — no deploy script
**Status:** Root cause established, lab replication planned

## Business Context

A financial services company (New Zealand) uses Amazon EKS with Karpenter for container orchestration across multiple Availability Zones. They are evaluating ARC Zonal Shift for production disaster recovery, specifically to automate traffic steering away from impaired AZs.

During pre-production validation, they performed a 1-minute manual zonal shift to verify end-to-end functionality. While ARC correctly started and expired the shift, the Kubernetes-level recovery (node uncordon + endpoint slice restoration) took approximately 24 minutes — far exceeding expectations and blocking their production adoption decision.

The team needs confidence that recovery time is pgeneredictable and bounded before enabling zonal shift in production clusters serving end users.

## Problem Statement

After a zonal shift expires in ARC, EKS takes significantly longer than expected to restore the shifted AZ to full operational state (uncordon nodes, re-add pod endpoints to EndpointSlices, allow scheduling).

**Expected:** Recovery within seconds/low minutes after shift expiry
**Actual:** ~24 minutes before nodes were uncordoned and endpoint slices updated

## Root Cause

The EKS zonal shift recovery mechanism is entirely **polling-based** — there is no event-driven push from ARC to EKS. Recovery requires traversing 5 sequential polling stages, each with its own interval:

1. **ARC (PeRC)** marks shift expired (~0s)
2. **EKS Weight Shift Poller** detects via polling (interval: 30s)
3. **EKS Weight Shift Executor** processes (interval: 15s)
4. **EKS Control Plane (KCP)** polls S3 config + uncordons (budget: ~150s)
5. **Karpenter Controller** detects cleared state (interval: 30s)
6. **AsyncEtcdIR Reconciler** final reconciliation (interval: 10 min) ← likely cause of extended delay

Internal SLA: 5 minutes (WSS + KCP). Karpenter adds ~30s. The 24 minutes exceeds SLA and indicates the AsyncEtcd reconciler or multiple reconciliation passes were involved.

Additionally, a 1-minute shift is an edge case — AWS documentation recommends "at least 60 seconds between zonal shift operations due to the current polling mechanism."

## Architecture

```
Customer Environment:
- EKS Cluster (ap-southeast-2) with Karpenter v1.12+
- Multi-AZ deployment (apse2-az1, apse2-az2, apse2-az3)
- Zonal Shift registered on the EKS cluster resource
- No Managed Node Groups (Karpenter manages all compute)
```

## Key Technical Details

| Component | Detail |
|-----------|--------|
| Service | Amazon EKS + ARC Zonal Shift |
| Region | ap-southeast-2 |
| Cluster provisioner | Karpenter v1.12+ |
| Shift type | Manual, 1-minute expiry |
| Shifted AZ | apse2-az3 |
| Recovery time | ~24 minutes |
| Internal SLA | 5 minutes |
| Recovery model | Polling-based (NOT event-driven) |

## Recovery Chain Timing

| Stage | Component | Polling Interval | Worst Case |
|-------|-----------|-----------------|------------|
| 1 | PeRC marks expired | — | ~0s |
| 2 | WSS Poller detects | 30s | 30s |
| 3 | WSS Executor processes | 15s | 15s |
| 4 | KCP polls S3 + executes | — | ~150s |
| 5 | Karpenter reconciles | 30s | 30s |
| 6 | AsyncEtcdIR (if triggered) | 10 min | 600s |
| **Total (normal)** | | | **~5 min** |
| **Total (with Etcd IR)** | | | **~15 min** |

## CloudTrail Events to Monitor

| Event Source | Event Name | What it tells you |
|-------------|------------|-------------------|
| arc-zonal-shift.amazonaws.com | StartZonalShift | When shift began |
| arc-zonal-shift.amazonaws.com | GetManagedResource | Karpenter polling (every 30s) |
| eks.amazonaws.com | UpdateNodegroupConfig | When EKS re-enabled AZ |
| autoscaling.amazonaws.com | ResumeProcesses | When ASG AZ rebalance restored |
| autoscaling.amazonaws.com | SuspendProcesses | When ASG AZ was suspended |
| ec2.amazonaws.com | RunInstances | Karpenter launching new nodes |

**Note:** Natural shift expiry does NOT generate a CloudTrail event. PeRC removes it server-side.

## Kubernetes Observability

```bash
# Watch endpoint slices during shift
kubectl get endpointslices --all-namespaces \
  -l 'eks-arc-zonal-shift/impaired-zone=ap-southeast-2a'

# Watch node taints
kubectl get nodes -o custom-columns=\
  NAME:.metadata.name,\
  TAINTS:.spec.taints \
  | grep "eks-arc-zonal-shift/impaired-zone"

# Karpenter logs
kubectl logs -n kube-system -l app.kubernetes.io/name=karpenter \
  | grep -i "zonal\|shift\|cleared\|reconcil"

# Kubernetes events
kubectl get events --all-namespaces --sort-by='.lastTimestamp' \
  | grep -i "cordon\|taint\|zonal"
```

## Recommendations

1. **Test with minimum 30-minute shifts** — avoids edge case of recovery > shift duration
2. **Use zonal autoshift practice runs** — purpose-built for validation testing
3. **If 24 min reproduces with longer shifts** — escalate to EKS Harbor team (Weight Shift Service owners)
4. **For production:** set alerts on EndpointSlice label removal as recovery indicator

## Lab Replication Plan

- [ ] Create EKS cluster with Karpenter in test account
- [ ] Enable zonal shift on cluster
- [ ] Perform manual shift (30 min expiry) and measure exact recovery time
- [ ] Capture all CloudTrail events during window
- [ ] Capture Kubernetes events timeline
- [ ] Compare with internal SLA (5 min)
- [ ] Document findings

## References

- [ARC Zonal Shift in EKS](https://docs.aws.amazon.com/eks/latest/userguide/zone-shift.html)
- [How a zonal shift works](https://docs.aws.amazon.com/r53recovery/latest/dg/arc-zonal-shift.how-it-works.html)
- [Best practices for zonal shifts](https://docs.aws.amazon.com/r53recovery/latest/dg/route53-arc-best-practices.zonal-shifts.html)
- [Karpenter v1.12+ zonal shift support](https://github.com/aws/karpenter-provider-aws)
- [Operating resilient workloads on EKS](https://aws.amazon.com/blogs/containers/operating-resilient-workloads-on-amazon-eks)
