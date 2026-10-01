# NET-020: STNO Console Hides Pending Requests Once the Table Outgrows One Scan Page

**Tier:** Lab — reproducible end to end
**Status:** Deploy ready — not yet run
**Services:** Transit Gateway · Network Orchestration for AWS Transit Gateway (STNO) v3.3.28 · AppSync · DynamoDB · Step Functions · EventBridge · Cognito · CloudFront · RAM · Direct Connect

A multi-account hub-and-spoke TGW onboarding workflow stops being usable at scale, and the cause
is not in the network at all. It is one unpaginated DynamoDB `Scan` in an AWS Solutions resolver.
The lab reproduces the failure, and the fix, without deploying the solution or a TGW.

Diagram: [architecture.drawio](architecture.drawio) — page 1 is the lab topology, page 2 the customer
architecture it was generalised from.

## Business Context — Public Sector / Research Computing

A central network team operates a hub-and-spoke Transit Gateway serving 150+ research and
application accounts in one region, built on **Network Orchestration for AWS Transit Gateway**
(STNO, solution ID SO0058). VPC onboarding is tag-driven: a spoke team tags a VPC or subnet with
`Associate-with` / `Propagate-to`, an EventBridge rule in the hub picks up the cross-account event,
and a Step Functions workflow creates the attachment and records the request in DynamoDB. Route
tables flagged `Approval=Yes` wait for a human to approve the request in the STNO web console.

Several VPCs are onboarded every week and the team has internal onboarding SLAs. The deployment
dates from 2020 and was upgraded to v3.3.28 in 2026 through a change set that pinned the version
plus six validated edits.

## Problem Statement

After the upgrade, the console's pending-requests view stopped showing every request, and its
search box could not find the missing ones either. Approvals could not be completed from the
console, new VPCs stayed unattached, and onboarding SLAs were missed. Severity was urgent: a
production onboarding path was broken.

The team's own measurement, taken before the case was opened:

- **~3,300 rows read, ~430 matching** the pending filter
- **With `limit` set to 100**, the first page returned 13 pending items and reported 33 more pages
  to go

Their two questions:

1. Is this a known bug, is a fix planned, or is some configuration missing?
2. Is approving through the CLI with an assumed role, instead of the state machine, a valid
   workaround?

## Architecture

```
 Spoke accounts (150+)                      Network hub account <ACCOUNT_HUB>  us-east-1
 ┌────────────────────┐                ┌──────────────── TransitGatewayHub stack (v3.3.28) ──────────────┐
 │ tag VPC/subnet     │  cross-acct    │                                                                │
 │ Associate-with     │───event───────▶│ EventBridge bus ──▶ Step Functions ──▶ create TGW attachment    │
 │ Propagate-to       │                │                           │                                    │
 └────────────────────┘                │                           ▼                                    │
                                       │             DynamoDB  Status=requested                         │
 Network operators                     │             PK SubnetId + SK Version · no GSI · TTL 90 d        │
 ┌────────────────────┐                │             ~3,400 items · ~1.7 MB                             │
 │ browser            │                │                           ▲                                    │
 └─────────┬──────────┘                │                           │                                    │
           │ HTTPS                     │                      ┌────┴──────┐                             │
           ▼                           │                      │  AppSync  │                             │
   CloudFront ──OAI──▶ S3 (React SPA)  │                      │ (VTL)     │                             │
           │                           │                      └────┬──────┘                             │
           └── Cognito JWT ──▶ WAFv2 ──┼──── GraphQL ──────────────┘                                    │
                                       │   getActionItems...  = ONE Scan + filter, no limit/nextToken ✗  │
                                       │                                                                │
                                       │ Transit Gateway  ASN 64512 · 160+ attachments · auto-accept ON  │
                                       │  HUB-SPOKE-TGW-RTB (Approval=Yes) · FROM-On-premises-Shared     │
                                       │  Dev · Inspection-* (Terraform) · SelfManaged (DX-only)         │
                                       │  Flat / Isolated / Infrastructure (created by the upgrade)      │
                                       └────────────────────────────────────────────────────────────────┘
                                                      │                        │
                                              DXGW attachment          inspection VPC (appliance mode ON)
                                                      │                   <ACCOUNT_INSPECTION>
                                                 Direct Connect ──▶ on-premises
```

Every identifier in this write-up and in the diagram is synthetic. Counts are rounded.

## The Two Paths Through the Console

Reading pending requests and acting on them are different code paths, and only the read is broken:

```
READ   SPA ─▶ AppSync Query  getActionItemsFromTransitNetworkOrchestratorTables
                 └─▶ DynamoDB Scan + filter (Version=latest AND Status IN requested|processing|failed)
                     no limit, no nextToken   ✗ one page only

WRITE  SPA ─▶ AppSync Mutation  updateTransitNetworkOrchestratorTable (AdminAction=accept)
                 └─▶ UpdateItem Status=processing ─▶ Lambda ─▶ StartExecution
                     ─▶ TGW associate / propagate   ✓ works
```

## Root Cause

A design bug in the solution, upstream — not the Transit Gateway, and not customer configuration.
Three things have to line up, and at this customer's scale they do:

1. **The resolver issues one `Scan` with a filter and never paginates.** The v3.3.28 request
   mapping template for the `getActionItems` query is a `Scan` with a filter expression and
   **no `limit` and no `nextToken`**, even though the GraphQL schema accepts both.
2. **DynamoDB applies the 1 MB page limit before the filter.** A `Scan` reads at most 1 MB of
   *table* data per call and filters afterwards. At ~510 B per item, one page covers roughly 2,000
   of ~3,400 items — about 60 % of the table.
3. **The UI makes a single call and ignores `nextToken`.** The action-items page calls the client
   once with no variables and reads only `items`, so even the token the resolver would return
   goes unused.

The table grows because STNO keeps request history with a 90-day TTL, with `SubnetId` as partition
key and `Version` as sort key. Every pending request past the first page is invisible to the
console, and the search box filters client-side over the same truncated list — which is why search
could not find the missing requests either. With the onboarding rate described, the blind spot only
widens.

**No fix was published.** The changelog from 3.3.0 to 3.3.28 and the repository issues contain
nothing about pagination, `Scan` or adding a GSI; v3.3.28 was the latest release at the time.

## Evidence

```
# Resolver (v3.3.28) — request mapping template
{ "version": "2017-02-28",
  "operation": "Scan",
  "filter": { "expression": "Version = :version and #s IN (:s1,:s2,:s3)", ... } }
#                                 ^ no "limit", no "nextToken"

# UI — action-items page
client.graphql({ query: getActionItemsFromTransitNetworkOrchestratorTables })
#  one call, no variables, nextToken from the response never used

# DynamoDB DescribeTable
itemCount ~3,400 · tableSizeBytes ~1.7 MB · globalSecondaryIndexes [] · keySchema SubnetId/Version

# DynamoDB documentation
"A Scan operation can retrieve a maximum of 1 MB of data. This limit applies before
 the filter expression is evaluated."
```

## Resolution Options

- **Paginate, in both halves.** Pass `limit` and `nextToken` in the resolver and loop in the UI
  until the token comes back null. This is the actual fix and it belongs upstream — a local patch
  of the resolver has to be reapplied on every solution upgrade.
- **Add a GSI on `Status`** (or on a composite such as `Version#Status`) and `Query` it instead of
  scanning. This stops reading the whole table to find a few hundred rows, and keeps cost flat as
  history accumulates. Larger change, and it needs a schema migration.
- **Shrink the hot table.** A shorter TTL on history items, or moving history to a separate table,
  pushes the live table back under 1 MB. It buys time rather than fixing anything, and the bug
  returns at the next growth step.
- **Approve outside the console, carefully.** See below.

## The CLI Workaround Is Only Half Safe

Approving outside the console is valid **only if it re-enters the same path the console uses** —
the AppSync mutation, or the state machine directly. Both end in `UpdateItem` followed by
`StartExecution`, so DynamoDB and the TGW stay in agreement.

Calling the Transit Gateway APIs directly (`associate-transit-gateway-route-table`,
`enable-transit-gateway-route-table-propagation`) **bypasses STNO entirely**: the attachment ends
up associated while the item stays `requested`, the audit trail of who approved what is lost, and
the next tag change on that VPC is evaluated against a state the orchestrator believes to be
different — which shows up later as drift.

A useful tell for this failure mode: an attachment that exists but is **not associated with any
route table** is exactly the state a pending or rejected request leaves behind. Finding one is a
good prompt to compare the TGW against the table.

## Lab — Minimal Reproduction

The bug lives entirely in the read path, so the lab reproduces it without STNO, without a TGW and
without multiple accounts. One region, one account, no hourly charges.

```
 DynamoDB  NET-020-action-items        (PK SubnetId + SK Version, no GSI, on-demand)
   └─ ~3,400 synthetic items, ~510 B each  ──▶  ~1.7 MB  (one Scan page ≈ 60 %)
      · Version = "latest" plus history items
      · ~430 items with Status IN requested | processing | failed, spread across the keyspace

 AppSync  NET-020-stno-repro  (API key auth)
   ├─ getActionItemsUpstream   → VTL copied from v3.3.28: Scan + filter, no limit/nextToken  ✗
   └─ getActionItemsPaginated  → same Scan + filter, with limit and nextToken               ✓

 Baseline: a full paginated Scan straight from the CLI, which is the real number of pending items.
```

### Running it

```bash
cd services/TransitGateway/scenarios/NET-020_stno_console_scan_pagination/lab
python deploy_lab.py deploy --profile <profile>      # table + items + AppSync API + both resolvers
python deploy_lab.py status --profile <profile>      # table size, item count, API ids
python deploy_lab.py compare --profile <profile>     # the whole point: upstream vs paginated vs truth
python deploy_lab.py teardown --profile <profile>
```

`deploy` writes `resources.json` (gitignored — it holds live ids).

### Exercises

1. **Reproduce the truncation.** Run `compare`. The upstream resolver returns a fraction of the
   pending items and hands back a `nextToken` nobody asked for; the paginated resolver and the CLI
   baseline agree on the real number.
2. **Find the cliff.** `seed --items 1200` keeps the table under 1 MB and both resolvers agree —
   the bug is invisible below the threshold. Grow the table past 1 MB and the gap appears. This is
   why the deployment worked for years and broke without a config change.
3. **Watch the filter lie.** `scan-stats` reports `ScannedCount` and `Count` per page. The filter
   runs after the 1 MB read, so a page can scan 2,000 items and return a handful — the behaviour
   that makes `Scan` + filter look like it "found everything".
4. **Simulate the search box.** `search <string>` filters client-side over the upstream result, the
   way the SPA does. Items past page one cannot be found, no matter the search term.
5. **Cost the alternatives.** Compare consumed RCUs for the unpaginated `Scan`, the full paginated
   `Scan`, and a `Query` against the optional `Status` GSI (`deploy --with-gsi`).

## Cost

DynamoDB on-demand plus an AppSync API, both billed per request with no hourly component. Seeding
~3,400 small items and running the exercises costs cents. Nothing here keeps running after
`teardown`; there is no TGW, NAT gateway or endpoint in the lab.

## Teardown

```bash
python deploy_lab.py teardown --profile <profile>
```

Deletes the AppSync API (resolvers, data source, schema and key go with it), the DynamoDB table and
the IAM role, then verifies each is gone before reporting success.

## Reusable Takeaways

- **`Scan` + filter is not a search.** The 1 MB page limit applies to the data read, before the
  filter. A filtered `Scan` that returns "few" results may simply have stopped early.
- **Pagination is a two-sided contract.** A resolver that returns `nextToken` fixes nothing if the
  client never sends it back, and a client that sends it fixes nothing if the resolver drops it.
  Both halves have to cooperate, which is why this class of bug survives code review.
- **Latent bugs surface through growth, not change.** This console worked for years. Nothing in the
  upgrade broke it; the table crossing 1 MB did. When an AWS Solutions deployment "breaks after an
  upgrade", check the data volume before the diff.
- **Keeping history in the live table has a read-path cost.** A 90-day TTL on request history makes
  every table-wide read 3× more expensive and eventually pushes the useful rows off the page.
- **Workarounds have to re-enter the supported path.** Driving the underlying API directly resolves
  the symptom and creates a silent divergence between the orchestrator's state and the network's.

## Open Questions

- Whether the six validated edits in the customer's change set touched the resolvers. The resolvers
  observed still pointed at the solution's own S3 templates, so the behaviour was upstream's
  *(not independently confirmed — verify before quoting)*.
- Whether a GSI on `Status` is the right upstream fix or whether the maintainers would prefer
  resolver-side pagination; worth raising as an issue on the repository rather than patching
  locally.

## Files

- `README.md` — this write-up
- `architecture.drawio` — page 1: lab topology (AppSync, three resolvers, data source role, table,
  Scan page split, baseline read); page 2: the generalised customer hub-and-spoke, console path and
  the failing read
- `lab/deploy_lab.py` — `deploy | seed | status | compare | scan-stats | search | teardown`

## References

- [Network Orchestration for AWS Transit Gateway — implementation guide](https://docs.aws.amazon.com/solutions/latest/network-orchestration-aws-transit-gateway/solution-overview.html)
- [GitHub — aws-solutions-library-samples/network-orchestration-for-aws-transit-gateway](https://github.com/aws-solutions-library-samples/network-orchestration-for-aws-transit-gateway) · [CHANGELOG](https://github.com/aws-solutions-library-samples/network-orchestration-for-aws-transit-gateway/blob/main/CHANGELOG.md)
- [Scanning tables in DynamoDB — 1 MB page, filter applied after the read, pagination](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Scan.html)
- [AppSync — DynamoDB Scan resolver mapping template reference](https://docs.aws.amazon.com/appsync/latest/devguide/aws-appsync-resolver-mapping-template-reference-dynamodb-scan.html)
- [DynamoDB — choosing between Query and Scan](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-query-scan.html)
