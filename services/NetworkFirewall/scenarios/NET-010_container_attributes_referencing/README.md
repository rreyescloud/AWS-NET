# NET-010: Network Firewall Container Attributes Referencing (EKS)

**Tier:** Lab — reproducible end to end
**Status:** Replicated — working end-to-end

## Objective

Replicate and document AWS Network Firewall **container attributes referencing** (a.k.a. *container associations*), a feature that lets a stateful rule group reference the live pod/task IPs of a Kubernetes/ECS workload — selected by container attributes (namespace + labels for EKS) — instead of a hardcoded CIDR list. The firewall keeps the IP set in sync automatically as pods scale up/down.

Source announcements replicated:
- https://aws.amazon.com/about-aws/whats-new/2026/06/aws-network-firewall-container-attributes-referencing/
- https://docs.aws.amazon.com/network-firewall/latest/developerguide/container-associations.html

## Use Cases

1. **Dynamic microsegmentation** — Write a firewall rule against "all pods with `app=web` in namespace `payments`" and let the association track their IPs as the deployment scales, rolls, or reschedules.
2. **Egress control by workload identity** — Reference the tracked pod IPs as the source of stateful allow/deny rules, so policy follows the workload rather than the subnet.
3. **Observability of container traffic** — Alert on traffic to/from a specific labeled workload without maintaining IP lists by hand.

## Environment

- Account: `<LAB_ACCOUNT_ID>` (Admin) — lab account
- Region: `us-east-1`
- EKS cluster: `nfw-container-lab`, Kubernetes 1.31, single managed nodegroup `ng-1` (t3.small ×1)
- VPC CNI: `AWS_VPC_K8S_CNI_EXTERNALSNAT=true` (**SNAT disabled — required for EKS container associations**)
- Workload: namespace `payments` (label `team=payments`), deployment `web` (3 replicas, labels `app=web`, `tier=frontend`)

## Key Findings

### 1. The API is NOT in aws-cli v2 (2.34.49) — but IS in boto3/botocore 1.43.50
The `network-firewall container-association` commands do not ship in the AWS CLI. The operations exist in boto3 (`create_container_association`, `describe_container_association`, `list_container_associations`, `update_container_association`, `delete_container_association`), so the feature must be driven via the SDK. See `lab/nfw_container.py`.

*(Same pattern observed earlier with the WAF `MONETIZE` action — botocore ships ahead of the CLI.)*

### 2. API shape (confirmed against the botocore model)
`CreateContainerAssociation`:
- `ContainerAssociationName*` (string)
- `Type*` — enum **`ECS` | `EKS`** (immutable)
- `ContainerMonitoringConfigurations*` — list of `{ ClusterArn*, AttributeFilters[] }`
  - `AttributeFilters` — list of `{ Key*, Value* }`. For EKS, `Key="namespace"` and Kubernetes label keys (e.g. `app`).
- `Description`, `Tags` optional

`DescribeContainerAssociation` returns `Status` (`CREATING` | `ACTIVE` | `DELETING`), **`ResolvedCidrCount`**, and `UpdateToken`.

### 3. IP-set REFERENCES use `@NAME`, not `$NAME` — the critical gotcha
The container association is wired into a stateful rule group through `RuleGroup.ReferenceSets.IPSetReferences.<NAME>.ReferenceArn = <association ARN>`.

In the Suricata rule, that reference **must be written with a leading `@`**:

```
alert tcp @CONTAINER_IPS any -> any any (msg:"traffic from tracked EKS pods"; sid:1000001; rev:1;)
```

Using `$CONTAINER_IPS` (the syntax for a plain `RuleVariables.IPSets` variable) fails with:
```
InvalidRequestException: CONTAINER_IPS cannot be null or empty,
context: RuleVariables.IPSets.CONTAINER_IPS
```
`$` = static rule variable; `@` = dynamic IP-set reference (Resource Groups **and** container associations). This distinction is the single easiest thing to get wrong.

### 4. Attribute filters resolved exactly as expected
With filters `namespace=payments` + `app=web`, the association resolved to **`ResolvedCidrCount: 3`** — matching precisely the 3 running `web` pods:
- `192.168.49.243`, `192.168.62.171`, `192.168.33.241`

Because SNAT is disabled, these are real pod IPs from the VPC CIDR (the firewall sees the pod IP, not the node IP) — which is exactly why external SNAT is a hard requirement for EKS.

## Replication Steps

```bash
# 0. Auth (auto-refresh daemon recommended; --once expires in ~1h)
aws sso login --profile lab   # use whatever credential mechanism your lab account needs

# 1. Cluster (already ACTIVE in this lab) + verify SNAT
aws eks describe-addon --cluster-name nfw-container-lab --addon-name vpc-cni \
  --region us-east-1 --query 'addon.configurationValues'
# -> {"env":{"AWS_VPC_K8S_CNI_EXTERNALSNAT":"true"}}
kubectl get ds aws-node -n kube-system \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="AWS_VPC_K8S_CNI_EXTERNALSNAT")].value}'
# -> true

# 2. Deploy the sample workload (namespace + labels are the filter dimensions)
kubectl apply -f lab/workload.yaml
kubectl get pods -n payments -o wide --show-labels

# 3. Create the EKS container association, filtered by namespace + label
python3 lab/nfw_container.py create \
  --cluster-arn arn:aws:eks:us-east-1:<LAB_ACCOUNT_ID>:cluster/nfw-container-lab \
  --namespace payments --label app=web
python3 lab/nfw_container.py wait-active   # -> Status=ACTIVE, ResolvedCidrCount=3

# 4. Create the STATEFUL rule group that references the association (@CONTAINER_IPS)
python3 lab/nfw_container.py rulegroup      # -> RuleGroupStatus=ACTIVE

# Teardown (order matters: delete rule group before the association it references)
python3 lab/nfw_container.py delete-rulegroup
python3 lab/nfw_container.py delete
```

## Resources Created

| Resource | Identifier |
|---|---|
| EKS cluster | `arn:aws:eks:us-east-1:<LAB_ACCOUNT_ID>:cluster/nfw-container-lab` |
| Container association | `arn:aws:network-firewall:us-east-1:<LAB_ACCOUNT_ID>:container-association/eks-payments-monitor` |
| Stateful rule group | `arn:aws:network-firewall:us-east-1:<LAB_ACCOUNT_ID>:stateful-rulegroup/container-ref-rules` |

## Requirements & Constraints (observed / documented)

- **Type is immutable** (`ECS` | `EKS`) — chosen at create time.
- **EKS requires external SNAT disabled** so the firewall observes pod IPs.
- **ECS requires `awsvpc` networking mode** (not exercised in this lab — EKS scope chosen).
- Up to **5 monitoring configurations** per association.
- **Delete protection**: an association referenced by a rule group cannot be deleted until the reference is removed — tear down the rule group first.

## Teardown Note

The EKS cluster (`nfw-container-lab`), the container association, and the rule group are **still running** in account `<LAB_ACCOUNT_ID>` at time of writing. Run the teardown block above, then `eksctl delete cluster --name nfw-container-lab --region us-east-1` to stop node/control-plane charges.

## Files

- `lab/eks-cluster.yaml` — eksctl cluster config (OIDC on, single t3.small nodegroup)
- `lab/workload.yaml` — `payments` namespace + `web` deployment (the attribute-filter dimensions)
- `lab/nfw_container.py` — boto3 driver: create / describe / wait-active / rulegroup / delete-rulegroup / delete
- `lab/eksctl-create.log`, `lab/eksctl-nodegroup.log` — build logs
