#!/usr/bin/env python3
"""NET-020 — STNO console hides pending requests once the table outgrows one Scan page.

Minimal reproduction of the read path only: a DynamoDB table shaped like STNO's
(PK SubnetId + SK Version, no GSI, 90-day history) and an AppSync API exposing two
resolvers over it — the upstream v3.3.28 template, and the same template with
pagination added. No STNO stack, no Transit Gateway, no second account.

deploy [--with-gsi]         Create role + table (+ optional Status GSI) + items + AppSync API
seed [ITEMS]               Wipe and re-seed the table with ITEMS items (default 3400)
status                     Table size, item count, API ids, resolver inventory
compare                    Upstream resolver vs paginated resolver vs full Scan baseline
scan-stats                 Per-page ScannedCount / Count / bytes, to show filter-after-read
search STRING              Client-side filter over the upstream result, like the SPA search box
rcu                        Consumed RCUs: unpaginated Scan, full Scan, GSI Query (if present)
teardown                   Delete everything and verify

Usage:
    python deploy_lab.py deploy [--profile PROFILE]
"""

import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

RESOURCE_FILE = Path(__file__).parent / "resources.json"

REGION = "us-east-1"
PREFIX = "NET-020"
TABLE = f"{PREFIX}-action-items"
API_NAME = f"{PREFIX}-stno-repro"
ROLE_NAME = f"{PREFIX}-appsync-ddb"
GSI_NAME = "Status-index"

# STNO keeps request history in the live table under a 90-day TTL. That history is what
# pushes the table past the 1 MB Scan page while only a few hundred rows are actionable.
DEFAULT_ITEMS = 3400          # ~1.7 MB at the item size below
TARGET_ITEM_BYTES = 510       # observed average in the customer's table
PENDING_RATIO = 0.125         # ~430 of ~3400 match the pending filter
HISTORY_RATIO = 0.65          # share of items that are history (Version != "latest")

PENDING_STATUSES = ["requested", "processing", "failed"]
DONE_STATUSES = ["approved", "declined", "deleted", "auto-approved"]
ROUTE_TABLES = ["HUB-SPOKE-TGW-RTB", "FROM-On-premises-Shared", "Dev",
                "Flat", "Isolated", "Infrastructure"]
AZS = ["us-east-1a", "us-east-1b", "us-east-1c"]

TAGS = {"Project": PREFIX}

# ── VTL ────────────────────────────────────────────────────────────────────────
# The filter is the one shipped in v3.3.28: latest version, pending-ish statuses.
# Version and Status are DynamoDB reserved words, hence expressionNames.
_FILTER = """
  "filter": {
    "expression": "#v = :version and #s IN (:s1, :s2, :s3)",
    "expressionNames": { "#v": "Version", "#s": "Status" },
    "expressionValues": {
      ":version": { "S": "latest" },
      ":s1": { "S": "requested" },
      ":s2": { "S": "processing" },
      ":s3": { "S": "failed" }
    }
  }"""

# Upstream: one Scan, no limit, no nextToken. DynamoDB stops at 1 MB of table data
# read and only then applies the filter, so anything past that page is never returned.
REQ_UPSTREAM = """{
  "version": "2017-02-28",
  "operation": "Scan",
%s
}""" % _FILTER

# Fixed: the same Scan, with the page size and the continuation token wired through.
REQ_PAGINATED = """{
  "version": "2017-02-28",
  "operation": "Scan",
  "limit": $util.defaultIfNull($ctx.args.limit, 100),
  #if( $ctx.args.nextToken )
  "nextToken": "$ctx.args.nextToken",
  #end
%s
}""" % _FILTER

# Alternative fix: Query a GSI on Status instead of scanning the whole table.
REQ_GSI = """{
  "version": "2017-02-28",
  "operation": "Query",
  "index": "%s",
  "limit": $util.defaultIfNull($ctx.args.limit, 100),
  #if( $ctx.args.nextToken )
  "nextToken": "$ctx.args.nextToken",
  #end
  "query": {
    "expression": "#s = :status",
    "expressionNames": { "#s": "Status" },
    "expressionValues": { ":status": { "S": "$ctx.args.status" } }
  }
}""" % GSI_NAME

RESP = "$util.toJson($ctx.result)"

SCHEMA = """
type ActionItem {
  SubnetId: String!
  Version: String!
  Status: String
  VpcId: String
  SubnetCidrBlock: String
  AvailabilityZone: String
  AWSSpokeAccountId: String
  AssociationRouteTable: String
  PropagationRouteTables: String
  RequestTimeStamp: String
  Comment: String
  UserId: String
}

type ActionItemsConnection {
  items: [ActionItem]
  nextToken: String
  scannedCount: Int
}

type Query {
  # verbatim behaviour of getActionItemsFromTransitNetworkOrchestratorTables in v3.3.28
  getActionItemsUpstream: ActionItemsConnection
  getActionItemsPaginated(limit: Int, nextToken: String): ActionItemsConnection
  getActionItemsByStatus(status: String!, limit: Int, nextToken: String): ActionItemsConnection
}

schema {
  query: Query
}
"""

TRUST = {
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"Service": "appsync.amazonaws.com"},
                   "Action": "sts:AssumeRole"}],
}


def get_session(profile):
    return boto3.Session(profile_name=profile, region_name=REGION)


def save(r):
    RESOURCE_FILE.write_text(json.dumps(r, indent=2, default=str) + "\n")


def load():
    if RESOURCE_FILE.exists():
        return json.loads(RESOURCE_FILE.read_text())
    return {}


def need(r, *keys):
    missing = [k for k in keys if not r.get(k)]
    if missing:
        raise SystemExit(f"resources.json is missing {missing} — run deploy first")


def tag_list():
    return [{"Key": k, "Value": v} for k, v in TAGS.items()]


# ═══════════════════════════════════════════════════════════════════════════════
#  SYNTHETIC DATA
# ═══════════════════════════════════════════════════════════════════════════════

def item_bytes(item):
    """DynamoDB item size: attribute names plus values, UTF-8 bytes."""
    total = 0
    for name, value in item.items():
        total += len(name.encode())
        total += len(next(iter(value.values())).encode())
    return total


def make_item(i, pending, history):
    """One STNO-shaped action item, padded to the observed average size."""
    subnet = f"subnet-0{i:015x}"
    version = "latest" if not history else str(int(time.time()) - random.randint(1, 90 * 86400))
    status = random.choice(PENDING_STATUSES if pending else DONE_STATUSES)
    item = {
        "SubnetId": {"S": subnet},
        "Version": {"S": version},
        "Status": {"S": status},
        "VpcId": {"S": f"vpc-0{i:015x}"},
        "SubnetCidrBlock": {"S": f"10.{i // 256 % 256}.{i % 256}.0/24"},
        "AvailabilityZone": {"S": random.choice(AZS)},
        "AWSSpokeAccountId": {"S": f"{random.randrange(10 ** 11, 10 ** 12):012d}"},
        "AssociationRouteTable": {"S": random.choice(ROUTE_TABLES)},
        "PropagationRouteTables": {"S": ", ".join(random.sample(ROUTE_TABLES, 2))},
        "RequestTimeStamp": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ")},
        "UserId": {"S": f"operator-{random.randint(1, 40):02d}@example.internal"},
        "TTL": {"N": str(int(time.time()) + 90 * 86400)},
    }
    pad = TARGET_ITEM_BYTES - item_bytes(item) - len("Comment")
    item["Comment"] = {"S": ("tag-driven onboarding request. " * 10)[:max(pad, 1)]}
    return item


def generate(count):
    """Pending items are spread across the keyspace, not clustered at the front:
    that is what makes the truncation invisible rather than obviously wrong."""
    n_pending = max(int(count * PENDING_RATIO), 1)
    pending_idx = set(random.sample(range(count), n_pending))
    items = []
    for i in range(count):
        is_pending = i in pending_idx
        # a pending request is always the current version of that subnet
        is_history = (not is_pending) and random.random() < HISTORY_RATIO
        items.append(make_item(i, is_pending, is_history))
    return items


def _write_one_batch(ddb, batch):
    unprocessed = {TABLE: batch}
    for attempt in range(10):
        resp = ddb.batch_write_item(RequestItems=unprocessed)
        unprocessed = resp.get("UnprocessedItems") or {}
        if not unprocessed:
            return
        time.sleep(0.2 * 2 ** attempt)
    raise RuntimeError(f"{len(unprocessed[TABLE])} items still unprocessed after 10 attempts")


def batch_write(ddb, requests, workers=10):   # botocore pools 10 connections per client
    """BatchWriteItem takes 25 requests per call; run batches in parallel so a
    high-latency link doesn't turn a few thousand writes into many minutes."""
    batches = [requests[i:i + 25] for i in range(0, len(requests), 25)]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed(pool.submit(_write_one_batch, ddb, b) for b in batches):
            fut.result()
            done += 1
            if done % 40 == 0:
                print(f"    {done * 25}/{len(requests)} written", flush=True)


def seed_table(sess, count):
    ddb = sess.client("dynamodb")
    items = generate(count)
    size = sum(item_bytes(i) for i in items)
    pending = sum(1 for i in items
                  if i["Version"]["S"] == "latest" and i["Status"]["S"] in PENDING_STATUSES)
    print(f"  seeding {count} items (~{size / 1024 / 1024:.2f} MB, {pending} match the filter)")
    batch_write(ddb, [{"PutRequest": {"Item": i}} for i in items])
    return {"seeded_items": count, "seeded_bytes": size, "seeded_pending": pending}


def wipe_table(sess):
    ddb = sess.client("dynamodb")
    keys, pages = [], ddb.get_paginator("scan").paginate(
        TableName=TABLE, ProjectionExpression="SubnetId,#v",
        ExpressionAttributeNames={"#v": "Version"})
    for page in pages:
        keys.extend(page["Items"])
    if keys:
        print(f"  deleting {len(keys)} existing items")
        batch_write(ddb, [{"DeleteRequest": {"Key": k}} for k in keys])


# ═══════════════════════════════════════════════════════════════════════════════
#  GRAPHQL CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

def graphql(r, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        r["graphql_url"], data=body,
        headers={"Content-Type": "application/json", "x-api-key": r["api_key"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AppSync HTTP {e.code}: {e.read().decode()[:400]}")
    if payload.get("errors"):
        raise SystemExit(f"GraphQL errors: {json.dumps(payload['errors'])[:600]}")
    return payload["data"]


FIELDS = "items { SubnetId Version Status AssociationRouteTable } nextToken scannedCount"

Q_UPSTREAM = "query { getActionItemsUpstream { %s } }" % FIELDS
Q_PAGINATED = ("query($limit:Int,$nextToken:String){ "
               "getActionItemsPaginated(limit:$limit,nextToken:$nextToken){ %s } }" % FIELDS)
Q_GSI = ("query($status:String!,$limit:Int,$nextToken:String){ "
         "getActionItemsByStatus(status:$status,limit:$limit,nextToken:$nextToken){ %s } }" % FIELDS)


# The resolver's filter, for calls made straight to the DynamoDB API.
PENDING_FILTER = dict(
    FilterExpression="#v = :version AND #s IN (:s1,:s2,:s3)",
    ExpressionAttributeNames={"#v": "Version", "#s": "Status"},
    ExpressionAttributeValues={":version": {"S": "latest"},
                               ":s1": {"S": "requested"},
                               ":s2": {"S": "processing"},
                               ":s3": {"S": "failed"}})

# Fields selected in the GraphQL queries; search compares on these only.
SEARCH_FIELDS = ["SubnetId", "Version", "Status", "AssociationRouteTable"]


def drain(r, query, field, variables=None):
    """Follow nextToken to exhaustion — what the UI should have done."""
    out, token, calls = [], None, 0
    while True:
        v = dict(variables or {})
        v["nextToken"] = token
        data = graphql(r, query, v)[field]
        out.extend(data["items"])
        calls += 1
        token = data.get("nextToken")
        if not token or calls > 200:
            return out, calls


def baseline_scan(sess):
    """Ground truth: the same filter, paginated properly, straight from the API."""
    ddb = sess.client("dynamodb")
    kwargs = dict(
        TableName=TABLE,
        ReturnConsumedCapacity="TOTAL", **PENDING_FILTER)
    items, scanned, rcu, pages, start = [], 0, 0.0, 0, None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = ddb.scan(**kwargs)
        items.extend(resp["Items"])
        scanned += resp["ScannedCount"]
        rcu += resp["ConsumedCapacity"]["CapacityUnits"]
        pages += 1
        start = resp.get("LastEvaluatedKey")
        if not start:
            return {"items": items, "scanned": scanned, "rcu": rcu, "pages": pages}


# ═══════════════════════════════════════════════════════════════════════════════
#  DEPLOY
# ═══════════════════════════════════════════════════════════════════════════════

def deploy(profile, with_gsi=False, items=DEFAULT_ITEMS):
    sess = get_session(profile)
    iam, ddb, appsync = sess.client("iam"), sess.client("dynamodb"), sess.client("appsync")
    acct = sess.client("sts").get_caller_identity()["Account"]
    r = load()
    print(f"NET-020 deploy · account {acct} · {REGION}")

    # ── IAM role for AppSync → DynamoDB ──────────────────────────────────────
    print("\n[1/5] IAM role")
    table_arn = f"arn:aws:dynamodb:{REGION}:{acct}:table/{TABLE}"
    policy = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow",
        "Action": ["dynamodb:Scan", "dynamodb:Query", "dynamodb:GetItem", "dynamodb:DescribeTable"],
        "Resource": [table_arn, f"{table_arn}/index/*"]}]}
    try:
        r["role_arn"] = iam.create_role(
            RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(TRUST),
            Description="NET-020 lab: AppSync read access to the action-items table",
            Tags=tag_list())["Role"]["Arn"]
        print(f"  created {ROLE_NAME}")
    except iam.exceptions.EntityAlreadyExistsException:
        r["role_arn"] = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"  reusing {ROLE_NAME}")
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="ddb-read",
                        PolicyDocument=json.dumps(policy))
    save(r)
    time.sleep(10)   # IAM propagation: AppSync assumes this role on the first query

    # ── DynamoDB table ───────────────────────────────────────────────────────
    print("\n[2/5] DynamoDB table")
    attrs = [{"AttributeName": "SubnetId", "AttributeType": "S"},
             {"AttributeName": "Version", "AttributeType": "S"}]
    kwargs = dict(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "SubnetId", "KeyType": "HASH"},
                   {"AttributeName": "Version", "KeyType": "RANGE"}],
        AttributeDefinitions=attrs,
        BillingMode="PAY_PER_REQUEST",
        Tags=tag_list())
    if with_gsi:
        # Status has only a handful of distinct values, so this GSI concentrates writes on
        # very few partitions. For the real fix prefer a composite key such as Version#Status.
        kwargs["AttributeDefinitions"] = attrs + [{"AttributeName": "Status", "AttributeType": "S"}]
        kwargs["GlobalSecondaryIndexes"] = [{
            "IndexName": GSI_NAME,
            "KeySchema": [{"AttributeName": "Status", "KeyType": "HASH"},
                          {"AttributeName": "Version", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}]
    try:
        ddb.create_table(**kwargs)
        print(f"  creating {TABLE}" + (f" with {GSI_NAME}" if with_gsi else " (no GSI, like STNO)"))
    except ddb.exceptions.ResourceInUseException:
        print(f"  reusing {TABLE}")
    ddb.get_waiter("table_exists").wait(TableName=TABLE)
    try:
        ddb.update_time_to_live(TableName=TABLE,
                                TimeToLiveSpecification={"Enabled": True, "AttributeName": "TTL"})
    except ClientError as e:
        if "TimeToLive is already enabled" not in str(e):
            raise
    r.update({"table": TABLE, "with_gsi": with_gsi, "account": acct})
    save(r)

    # ── seed ─────────────────────────────────────────────────────────────────
    print("\n[3/5] Synthetic action items")
    r.update(seed_table(sess, items))
    save(r)

    # ── AppSync API + schema ─────────────────────────────────────────────────
    print("\n[4/5] AppSync API")
    if r.get("api_id"):
        print(f"  reusing api {r['api_id']}")
    else:
        api = appsync.create_graphql_api(name=API_NAME, authenticationType="API_KEY",
                                         tags=TAGS)["graphqlApi"]
        r["api_id"] = api["apiId"]
        r["graphql_url"] = api["uris"]["GRAPHQL"]
        r["api_key"] = appsync.create_api_key(
            apiId=api["apiId"], description="NET-020 lab",
            expires=int(time.time()) + 7 * 86400)["apiKey"]["id"]
        print(f"  created api {r['api_id']}")
        save(r)

    appsync.start_schema_creation(apiId=r["api_id"], definition=SCHEMA.encode())
    while True:
        st = appsync.get_schema_creation_status(apiId=r["api_id"])
        if st["status"] in ("SUCCESS", "ACTIVE"):
            break
        if st["status"] == "FAILED":
            raise SystemExit(f"schema creation failed: {st.get('details')}")
        print(f"  schema {st['status']}...")
        time.sleep(3)
    print("  schema active")

    ds = f"{PREFIX.replace('-', '_')}_actionItems"
    try:
        appsync.create_data_source(
            apiId=r["api_id"], name=ds, type="AMAZON_DYNAMODB",
            serviceRoleArn=r["role_arn"],
            dynamodbConfig={"tableName": TABLE, "awsRegion": REGION})
        print(f"  data source {ds}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BadRequestException", "ConflictException"):
            raise
        print(f"  data source {ds} already present")
    r["data_source"] = ds

    # ── resolvers ────────────────────────────────────────────────────────────
    print("\n[5/5] Resolvers")
    resolvers = [("getActionItemsUpstream", REQ_UPSTREAM, "unpaginated Scan — reproduces the bug"),
                 ("getActionItemsPaginated", REQ_PAGINATED, "Scan with limit + nextToken — the fix")]
    if with_gsi:
        resolvers.append(("getActionItemsByStatus", REQ_GSI, "Query on the Status GSI"))
    for field, template, note in resolvers:
        args = dict(apiId=r["api_id"], typeName="Query", fieldName=field, dataSourceName=ds,
                    requestMappingTemplate=template, responseMappingTemplate=RESP)
        try:
            appsync.create_resolver(**args)
        except ClientError:
            appsync.update_resolver(**args)
        print(f"  {field} — {note}")
    r["resolvers"] = [f for f, _, _ in resolvers]
    save(r)

    print(f"\nDone. state → {RESOURCE_FILE.name}")
    print("Note: DescribeTable item count and size refresh roughly every 6 hours, so `status`")
    print("      reports the size computed at seed time as well.")
    print("\nNext:  python deploy_lab.py compare --profile <profile>")


# ═══════════════════════════════════════════════════════════════════════════════
#  OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def status(profile, _=None):
    sess, r = get_session(profile), load()
    need(r, "table")
    ddb, appsync = sess.client("dynamodb"), sess.client("appsync")
    t = ddb.describe_table(TableName=TABLE)["Table"]
    print(f"table {TABLE}: {t['TableStatus']} · billing "
          f"{t.get('BillingModeSummary', {}).get('BillingMode')}")
    print(f"  DescribeTable (lags up to ~6 h): {t['ItemCount']} items, "
          f"{t['TableSizeBytes'] / 1024 / 1024:.2f} MB")
    if r.get("seeded_items"):
        print(f"  computed at seed time:          {r['seeded_items']} items, "
              f"{r['seeded_bytes'] / 1024 / 1024:.2f} MB, "
              f"{r['seeded_pending']} matching the pending filter")
        covered = min(1024 * 1024 / r["seeded_bytes"] * 100, 100)
        print(f"  one 1 MB Scan page covers ≈ {covered:.0f}% of the table")
    gsis = [g["IndexName"] for g in t.get("GlobalSecondaryIndexes", [])]
    print(f"  GSIs: {gsis or 'none (same as STNO)'}")
    if r.get("api_id"):
        print(f"\napi {r['api_id']} · {r['graphql_url']}")
        for res in appsync.list_resolvers(apiId=r["api_id"], typeName="Query")["resolvers"]:
            paged = "limit" in res["requestMappingTemplate"]
            print(f"  {res['fieldName']:28s} {'paginated' if paged else 'single page'}")


def compare(profile, _=None):
    sess, r = get_session(profile), load()
    need(r, "graphql_url", "api_key")

    print("1. upstream resolver (v3.3.28 template: one Scan, no limit, no nextToken)")
    up = graphql(r, Q_UPSTREAM)["getActionItemsUpstream"]
    n_up = len(up["items"])
    print(f"   returned {n_up} items · scannedCount {up.get('scannedCount')} · "
          f"nextToken {'present but discarded by the UI' if up.get('nextToken') else 'absent'}")

    print("\n2. paginated resolver (same filter, limit + nextToken wired through)")
    paged, calls = drain(r, Q_PAGINATED, "getActionItemsPaginated", {"limit": 100})
    print(f"   returned {len(paged)} items over {calls} calls")

    print("\n3. baseline — the same filter paginated straight from the DynamoDB API")
    base = baseline_scan(sess)
    n_base = len(base["items"])
    print(f"   returned {n_base} items · scanned {base['scanned']} rows over "
          f"{base['pages']} pages · {base['rcu']:.1f} RCU")

    if r.get("with_gsi"):
        print("\n4. GSI Query per status (the alternative fix)")
        total = 0
        for st in PENDING_STATUSES:
            got, c = drain(r, Q_GSI, "getActionItemsByStatus", {"status": st, "limit": 100})
            latest = [i for i in got if i["Version"] == "latest"]
            total += len(latest)
            print(f"   {st:11s} {len(latest):4d} latest ({len(got)} incl. history, {c} calls)")
        print(f"   total {total} items")

    print("\n" + "─" * 72)
    hidden = n_base - n_up
    if hidden > 0:
        pct = hidden / n_base * 100
        print(f"The console shows {n_up} of {n_base} pending requests. "
              f"{hidden} are invisible ({pct:.0f}%).")
        print("The operator has no way to tell: no error, no empty state, no warning.")
    elif n_base == 0:
        print("No pending items at all — re-seed the table.")
    else:
        print(f"Both paths agree on {n_base} items: the table still fits in one 1 MB Scan page.")
        print("Grow it past 1 MB (seed 3400) and the gap appears without any config change.")


def scan_stats(profile, _=None):
    """ScannedCount vs Count per page: the filter runs after the 1 MB read."""
    sess = get_session(profile)
    ddb = sess.client("dynamodb")
    kwargs = dict(
        TableName=TABLE,
        ReturnConsumedCapacity="TOTAL", **PENDING_FILTER)
    page, start, totals = 0, None, [0, 0]
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        resp = ddb.scan(**kwargs)
        page += 1
        totals[0] += resp["ScannedCount"]
        totals[1] += len(resp["Items"])
        print(f"  page {page}: scanned {resp['ScannedCount']:5d} · matched "
              f"{len(resp['Items']):4d} · {resp['ConsumedCapacity']['CapacityUnits']:6.1f} RCU · "
              f"more pages: {'yes' if resp.get('LastEvaluatedKey') else 'no'}")
        start = resp.get("LastEvaluatedKey")
        if not start:
            break
    print(f"\n  totals: scanned {totals[0]} rows to return {totals[1]}.")
    print("  Page 1 alone is everything the upstream resolver ever sees.")


def search(profile, args):
    if not args:
        raise SystemExit("usage: search STRING")
    needle = args[0].lower()
    r = load()
    need(r, "graphql_url")
    up = graphql(r, Q_UPSTREAM)["getActionItemsUpstream"]["items"]
    hits = [i for i in up if needle in " ".join(str(i.get(f)) for f in SEARCH_FIELDS).lower()]
    print(f"the SPA filters client-side over {len(up)} loaded items → {len(hits)} hits")
    for h in hits[:10]:
        print(f"  {h['SubnetId']} {h['Status']} {h['AssociationRouteTable']}")
    sess = get_session(profile)
    truth = [i for i in baseline_scan(sess)["items"]
             if needle in " ".join(i[f]["S"] for f in SEARCH_FIELDS if f in i).lower()]
    print(f"\nagainst the full table: {len(truth)} hits")
    if len(truth) > len(hits):
        print(f"{len(truth) - len(hits)} requests exist and cannot be found by searching.")


def rcu(profile, _=None):
    sess, r = get_session(profile), load()
    ddb = sess.client("dynamodb")
    one = ddb.scan(TableName=TABLE, ReturnConsumedCapacity="TOTAL", **PENDING_FILTER)
    print(f"single unpaginated Scan : {one['ConsumedCapacity']['CapacityUnits']:7.1f} RCU "
          f"· {one['ScannedCount']} rows read, {len(one['Items'])} returned")
    base = baseline_scan(sess)
    print(f"full paginated Scan     : {base['rcu']:7.1f} RCU · {base['scanned']} rows read, "
          f"{len(base['items'])} returned")
    if r.get("with_gsi"):
        total_rcu, total_items = 0.0, 0
        for st in PENDING_STATUSES:
            start = None
            while True:
                kw = dict(TableName=TABLE, IndexName=GSI_NAME,
                          KeyConditionExpression="#s = :s",
                          ExpressionAttributeNames={"#s": "Status"},
                          ExpressionAttributeValues={":s": {"S": st}},
                          ReturnConsumedCapacity="TOTAL")
                if start:
                    kw["ExclusiveStartKey"] = start
                resp = ddb.query(**kw)
                total_rcu += resp["ConsumedCapacity"]["CapacityUnits"]
                total_items += len(resp["Items"])
                start = resp.get("LastEvaluatedKey")
                if not start:
                    break
        print(f"GSI Query per status    : {total_rcu:7.1f} RCU · {total_items} returned")
    else:
        print("GSI Query               : not deployed (re-run deploy --with-gsi)")


def seed(profile, args):
    count = int(args[0]) if args else DEFAULT_ITEMS
    sess, r = get_session(profile), load()
    need(r, "table")
    wipe_table(sess)
    r.update(seed_table(sess, count))
    save(r)
    print("re-seeded. run compare to see whether the table still fits one page.")


# ═══════════════════════════════════════════════════════════════════════════════
#  TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def teardown(profile, _=None):
    sess, r = get_session(profile), load()
    iam, ddb, appsync = sess.client("iam"), sess.client("dynamodb"), sess.client("appsync")
    print("NET-020 teardown")

    def safe(label, fn, *a, **kw):
        try:
            fn(*a, **kw)
            print(f"  deleted {label}")
        except ClientError as e:
            print(f"  {label}: {e.response['Error']['Code']}")

    if r.get("api_id"):
        # deleting the API removes its resolvers, data source, schema and keys
        safe(f"appsync api {r['api_id']}", appsync.delete_graphql_api, apiId=r["api_id"])

    safe(f"table {TABLE}", ddb.delete_table, TableName=TABLE)
    try:
        ddb.get_waiter("table_not_exists").wait(TableName=TABLE)
    except ClientError:
        pass

    safe("role policy", iam.delete_role_policy, RoleName=ROLE_NAME, PolicyName="ddb-read")
    safe(f"role {ROLE_NAME}", iam.delete_role, RoleName=ROLE_NAME)

    print("\nverifying")
    remaining = []
    try:
        ddb.describe_table(TableName=TABLE)
        remaining.append(f"table {TABLE}")
    except ddb.exceptions.ResourceNotFoundException:
        print(f"  table {TABLE} gone")
    try:
        iam.get_role(RoleName=ROLE_NAME)
        remaining.append(f"role {ROLE_NAME}")
    except iam.exceptions.NoSuchEntityException:
        print(f"  role {ROLE_NAME} gone")
    if r.get("api_id"):
        try:
            appsync.get_graphql_api(apiId=r["api_id"])
            remaining.append(f"api {r['api_id']}")
        except appsync.exceptions.NotFoundException:
            print(f"  api {r['api_id']} gone")

    if remaining:
        print(f"\nSTILL PRESENT: {remaining}")
        raise SystemExit(1)
    RESOURCE_FILE.unlink(missing_ok=True)
    print("\nAll resources removed.")


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    commands = {"deploy": deploy, "seed": seed, "status": status, "compare": compare,
                "scan-stats": scan_stats, "search": search, "rcu": rcu, "teardown": teardown}
    parser = argparse.ArgumentParser(description="NET-020 STNO Scan pagination lab")
    parser.add_argument("command", choices=list(commands))
    parser.add_argument("args", nargs="*")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    parser.add_argument("--with-gsi", action="store_true",
                        help="also create a Status GSI and a Query resolver over it")
    parser.add_argument("--items", type=int, default=DEFAULT_ITEMS)
    a = parser.parse_args()
    if not a.profile:
        raise SystemExit("--profile is required (or set AWS_PROFILE)")
    if a.command == "deploy":
        deploy(a.profile, with_gsi=a.with_gsi, items=a.items)
    else:
        commands[a.command](a.profile, a.args)


if __name__ == "__main__":
    main()
