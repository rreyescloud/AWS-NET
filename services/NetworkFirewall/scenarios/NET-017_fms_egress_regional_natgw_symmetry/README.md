# NET-017: Centralized Egress Inspection vs Regional NAT Gateway — Firewall Manager Design Blocker

**Tier:** Lab — reproducible end to end
**Status:** Deploy ready — not yet run
**Services:** Network Firewall · Firewall Manager · VPC Regional NAT Gateway · Transit Gateway · VPC Routing · Route 53 Resolver DNS Firewall · Organizations · RAM · EC2

**Builds on [NET-005](../NET-005_asymmetric_routing_regional_natgw/).** NET-005 proved that per-subnet
return routes in the regional NAT gateway route table keep traffic symmetric. That lab had appliance
mode **disabled** and one spoke subnet per AZ. This scenario is the multi-account landing-zone
version: appliance mode enabled, an aggregated `10.0.0.0/8` return route, and Firewall Manager
governance. Under those conditions the NET-005 fix no longer holds.

## Business Context — IT Consulting / Platform Services

A central platform team runs a hub-and-spoke landing zone (Organizations + Control Tower) for
several workload and shared-services accounts. All internet egress leaves through one central
egress VPC. Most of it (8–9 TB/month) comes from CI/CD build runners that pull images, packages
and artifacts.

Requirements:
- **Stateful egress inspection** with a domain allow-list, run first in alert-only mode and then enforcing
- **Central governance** through Firewall Manager from a delegated security account
- **Multi-AZ** (3 AZs)
- **Keep the regional NAT gateway**, so no public subnets are needed
- **Control cost per GB**

## Problem Statement

This is a design blocker, not an outage. The team wants to insert a Firewall Manager-managed
Network Firewall between the spokes and a regional NAT gateway. Firewall subnets and their route
tables were already pre-built, and empty, in the egress VPC.

They could not work out how to send return traffic from the regional NAT gateway back through the
right firewall endpoint, because the NAT gateway's route table is AWS-created and belongs to the
NAT gateway rather than to a subnet.

Open questions from the team:
1. Can the native TGW attachment for Network Firewall be managed by Firewall Manager?
2. Does flow symmetry hold across 3 AZs with that attachment?
3. Does the Network Firewall NAT gateway charge waiver apply to a regional NAT gateway?
4. How is a Firewall Manager DNS Firewall policy billed, and does it auto-apply to new accounts?

## Architecture (customer)

```
 Spoke accounts (x5, RAM-shared TGW)            <ACCOUNT_NETWORK>
 ┌──────────────┐                    ┌───────────────── Egress VPC 10.2.0.0/16 ──────────────────┐
 │ CI runners   │ 0/0 → TGW          │ TGW subnets (AZ1/2/3)   Firewall subnets (AZ1/2/3)          │
 │ 10.x.0.0/16  │──────► TGW ───────►│  RT: 0/0 → RNAT         RT: local only (empty, pre-built)   │
 └──────────────┘  appliance mode ON │                                                             │
                                     │  Regional NAT GW (manual mode, 1 EIP per AZ)                │
                                     │   own RT: 10.0.0.0/8 → TGW, 0/0 → IGW  ───► IGW ───► Internet│
                                     └─────────────────────────────────────────────────────────────┘

 Desired: TGW subnet (AZ x) → vpce-fw-x → RNAT → IGW, and the return must re-enter vpce-fw-x.
 Blocker: RNAT RT has ONE entry for 10.0.0.0/8 → it can point to only ONE firewall endpoint.
```

## Root Cause

A confirmed design constraint, not a fault:

1. **One route table for all AZs.** The regional NAT gateway's route table can hold one target for
   `10.0.0.0/8`. With firewall endpoints in 3 AZs, flows inspected in the other two AZs return
   through the wrong endpoint.
2. **Network Firewall state is per endpoint.** Both directions of a flow must traverse the same
   endpoint. The endpoint that only sees the return half has no state for it.
3. **Appliance mode breaks the NET-005 workaround.** With appliance mode on, the TGW picks the
   egress-VPC AZ with a flow hash, not the source AZ. A flow from a spoke subnet in AZ1 can be
   inspected in AZ2. Per-subnet return routes key on the source AZ, so they still send part of
   the traffic back through the wrong endpoint. With `10.0.0.0/8` aggregated across spokes there
   is no per-AZ prefix to route on at all.
4. **Firewall Manager doesn't manage routes here.** Route table management isn't supported for the
   centralized deployment model. Route monitoring only covers routes toward internet gateways,
   not NAT gateways. So Firewall Manager neither fixes nor flags this.
5. **The symmetric alternative sits outside Firewall Manager.** A Network Firewall with a native
   TGW attachment has appliance mode always on and routes entirely in TGW route tables. It keeps
   the regional NAT gateway. Firewall Manager offers distributed, centralized and import models,
   and the case analysis found that TGW-attached firewalls can't be imported *(not confirmed in
   public docs — verify before quoting)*.

The trade-off: pick two of Firewall Manager governance, regional NAT gateway, multi-AZ firewall.

## Resolution Options

**Option A (recommended): keep Firewall Manager, replace the regional NAT gateway with zonal NAT gateways**
1. Create one public subnet per AZ with `0/0 → IGW`, and one zonal NAT gateway per AZ.
2. Create the Firewall Manager policy (centralized, egress VPC, 3 AZs). Firewall Manager places the endpoints.
3. TGW subnet RT (AZ x): `0/0 → vpce-fw-x`.
4. Firewall subnet RT (AZ x): `0/0 → nat-zonal-x`.
5. NAT subnet RT (AZ x): `10.0.0.0/8 → vpce-fw-x`, `0/0 → IGW`.

Each AZ is a closed loop, so appliance mode can stay on. This design also matches the NAT gateway
waiver's same-path requirement.

**Option B: keep the regional NAT gateway, use a TGW-attached firewall (outside Firewall Manager)**
1. Create the firewall with a TGW attachment in the same AZs where the TGW is enabled. Manage its
   policy with IaC from the network account.
2. TGW RTs: spokes `0/0 → fw attachment`. Firewall RT `0/0 → egress attachment`, spokes propagated.
   Egress RT `10.0.0.0/8 → fw attachment`.
3. Leave the RNAT route table as it was (`10.0.0.0/8 → TGW`).
4. Set `HOME_NET` explicitly in the domain list rule groups (required for TGW-attached firewalls).
5. Accept the costs: TGW data processing is charged twice (spoke → firewall → egress), and the
   NAT waiver eligibility for a regional NAT gateway is unconfirmed.

**Option C (not recommended):** firewall endpoint in one AZ only and `10.0.0.0/8 → that endpoint`.
It is symmetric, but all traffic crosses AZs and the firewall becomes a single-AZ dependency.

## Interim Control and Cost Reduction

- **DNS Firewall via Firewall Manager**, scoped by OU with auto-remediation, so new accounts and
  VPCs are associated automatically. It is billed as one Firewall Manager policy per Region plus
  DNS Firewall usage. It only filters queries sent to the Route 53 Resolver. A workload using its
  own DNS server bypasses it.
- **CI egress:**
  - S3 and DynamoDB gateway endpoints in the runner VPC.
  - Interface endpoints for ECR, STS and CodeArtifact, reached without going through the firewall.
  - ECR pull-through cache and CodeArtifact upstream repositories for public images and packages.
- **Alert-only phase:** with strict rule ordering, an `ALERTLIST` domain group only logs. Traffic
  then falls through to the default action, so `aws:drop_established` drops it. Use a default that
  doesn't drop, or pair the alert rules with pass rules, before switching to enforcement.

## Pricing Notes (verify before quoting)

- The public pricing page conditions the NAT gateway waiver on **same Region**, **same account**
  and **same networking path** as the firewall endpoint. It is applied one-to-one against firewall
  endpoint hours and capped at the GB the firewall processed.
- The page **doesn't mention regional NAT gateways or TGW-attached firewalls**. The case analysis
  treated regional NAT gateways as excluded. Confirm with the pricing team before promising a
  number.

## What This Lab Proves

Single account, us-west-2, 2 AZs. Firewall Manager is left out (it needs an Organization). The
routing problem is identical with a directly deployed firewall.

1. **Baseline (broken):** appliance mode ON and RNAT RT `10.10.0.0/16 → vpce-fw-az1`. Expected:
   flows hashed to AZ2 fail from **both** instances, and the ones that succeed show the AZ1 EIP.
2. **NET-005 fix, `routes per-subnet`:** expected to still fail part of the time, because the
   inspection AZ comes from the hash, not the source subnet.
3. **`appliance off` + `routes per-subnet`:** expected 100% (the NET-005 conditions). This shows
   the workaround depends on appliance mode being off.
4. **`routes tgw`:** the return skips the firewall. Expected: all flows fail, because each endpoint
   sees only one direction.
5. **Option A, `fix-zonal`:** expected 100% with appliance mode ON. The public IP matches the
   inspecting AZ.
6. **Option B, `fix-tgw-fw`:** expected 100% while keeping the regional NAT gateway.
7. **Alert phase, `rules alert`:** expected: the allowed domain starts failing under strict order
   + `drop_established`, while alert logs show the match.

Lines marked "expected" are hypotheses until the lab runs. Record the actual results in this README.

## Lab Topology

```
Spoke VPC 10.10.0.0/16                         Egress VPC 10.2.0.0/16
 EC2 AZ1 10.10.1.0/24 ─┐                        TGW sub AZ1 ─► vpce-fw-az1 ─┐
 EC2 AZ2 10.10.2.0/24 ─┴─► TGW ──(hash)──────►  TGW sub AZ2 ─► vpce-fw-az2 ─┴─► RNAT (EIP/AZ) ─► IGW
 SSM endpoints (local)        appliance ON      RNAT RT: 10.10.0.0/16 ─► vpce-fw-az1   ◄── the bug

Option A: + public sub per AZ, zonal NAT per AZ, fw RT (x) 0/0 → nat-x, NAT RT (x) 10.10/16 → vpce-fw-x
Option B: + TGW-attached firewall; spoke RT 0/0 → fw att → egress att; TGW subs 0/0 → RNAT; RNAT RT → TGW
```

Firewall policy: strict order, default `aws:drop_established` + `aws:alert_established`, one domain
group allowing `checkip.amazonaws.com` (it returns the public IP, which tells you which NAT AZ
handled the flow), `HOME_NET = 10.0.0.0/8`. `www.example.com` is the negative test.

## Running the Lab

```
cd lab
python deploy_lab.py deploy               # ~15 min
python deploy_lab.py test                 # 1. baseline
python deploy_lab.py routes per-subnet && python deploy_lab.py test    # 2
python deploy_lab.py appliance off && python deploy_lab.py test        # 3
python deploy_lab.py appliance on
python deploy_lab.py routes tgw && python deploy_lab.py test           # 4
python deploy_lab.py fix-zonal && python deploy_lab.py test            # 5
python deploy_lab.py fix-tgw-fw && python deploy_lab.py test           # 6 (~5 min)
python deploy_lab.py rules alert && python deploy_lab.py test          # 7
python deploy_lab.py rules allow
python deploy_lab.py status
python deploy_lab.py teardown             # same day
```

Each `test` opens N new TCP flows per instance (default 20). The TGW hash is per flow, so a single
curl proves nothing.

**Estimated cost:** ~$1.3/h baseline (2 firewall endpoints dominate). ~$2.3/h with Option B
running (2 more endpoints). Tear down the same day.

## Troubleshooting Path

1. `aws ec2 describe-nat-gateways --nat-gateway-ids <nat>` — `AvailabilityMode=regional` plus a
   `RouteTableId` means one table for every AZ.
2. `aws ec2 describe-route-tables --route-table-ids <rnat-rt>` — count how many targets the
   spoke CIDR can have (one per prefix).
3. `aws ec2 describe-transit-gateway-vpc-attachments` — if `ApplianceModeSupport=enable` on the
   egress attachment, per-subnet return routes can't guarantee symmetry.
4. Network Firewall flow logs — look for one-direction flows on the "wrong" endpoint. Compare the
   `availability_zone` field with the source subnet's AZ.
5. `checkip.amazonaws.com` from the workload — the EIP returned tells you which NAT AZ handled
   the flow.
6. Reachability Analyzer — spoke instance → IGW for the forward path, and NAT gateway → spoke
   CIDR for the return hop.
7. With a TGW-attached firewall: `aws ec2 search-transit-gateway-routes` on each TGW RT.
8. Firewall Manager: compliance view, keeping in mind that centralized policies have no route
   monitoring.
9. Cost Explorer, grouped by usage type: NAT gateway hours/bytes vs firewall endpoint hours/GB,
   to see whether the waiver is being applied.

## Learning Exercises

1. Run all 7 lab steps and replace "expected" with the measured success ratio per step.
2. Compare the monthly cost of Option A vs Option B for 9 TB of egress in 3 AZs, including TGW
   double processing and the waiver (with and without it for the regional NAT gateway).
3. Add an S3 gateway endpoint to the spoke and download an S3 object. Confirm the flow never
   reaches the firewall or NAT logs.
4. Deploy a Firewall Manager DNS Firewall policy on an OU, move a new account in, and confirm the
   automatic association. Then bypass it with a custom resolver.
5. Build the safe alert-to-enforce rollout: `ALERTLIST` at a higher priority, `ALLOWLIST` below it,
   a non-dropping default, then switch to `drop_established` and validate with the alert logs.

## Files

- `README.md` — this document
- `lab/deploy_lab.py` — `deploy | test | routes | appliance | rules | fix-zonal | fix-tgw-fw | status | teardown`

## References

- [Regional NAT gateways](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateways-regional.html) — own route table, TGW is a valid target, manual vs automatic mode
- [Transit gateway-attached firewalls — considerations](https://docs.aws.amazon.com/network-firewall/latest/developerguide/tgw-firewall-considerations.html) — appliance mode always on, TGW route tables only, `HOME_NET`
- [Firewall Manager Network Firewall policies](https://docs.aws.amazon.com/waf/latest/developerguide/network-firewall-policies.html) — distributed, centralized, import
- [Firewall Manager route table management](https://docs.aws.amazon.com/waf/latest/developerguide/fms-manage-vpc-route-tables.html) — not supported for centralized; IGW-only monitoring
- [Domain list rule groups](https://docs.aws.amazon.com/network-firewall/latest/developerguide/stateful-rule-groups-domain-names.html) — `HOME_NET` for traffic from other VPCs, Alert behavior
- [Network Firewall pricing](https://aws.amazon.com/network-firewall/pricing/) — NAT gateway waiver conditions
- [Using NAT gateway with a firewall (whitepaper)](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/using-nat-gateway-with-firewall.html)
