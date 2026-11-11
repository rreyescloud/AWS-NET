"""NET-020 diagrams -> architecture.(svg|png) (lab) and architecture_customer.(svg|png)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, GREEN, RED, PURPLE, TEAL, MUTED  # noqa: E402


# ======================================================================
# Figure 1 — lab: minimal reproduction of the read path
# ======================================================================
def lab():
    d = Diagram(2000, 1140)

    d.group(280, 20, 1700, 950, "cloud", "AWS Cloud")
    d.group(310, 80, 1640, 870, "region", "Region us-east-1")
    d.group(340, 140, 1580, 790, "account", "Lab account (one account · no TGW · no STNO stack)")

    # operator workstation + commands
    d.node("Client", 130, 430, ["Operator workstation", "deploy_lab.py (boto3)", "plays the console SPA"],
           pos="above")
    d.box(20, 560, 230, 200, "deploy_lab.py",
          ["deploy [--with-gsi]", "seed [ITEMS]", "status · compare", "scan-stats · rcu", "search STRING",
           "teardown"], size=14)

    # AppSync
    d.node("AWS-AppSync", 440, 430, ["AppSync GraphQL API", "NET-020-stno-repro", "API key auth"], pos="above")

    # resolvers
    d.band(570, 200, 540, 470, "Resolvers (VTL, unit)", color="#E7157B", label_color="#B0105C")
    d.text(590, 245, "same filter: Version = latest AND", 13, 400, MUTED, "start")
    d.text(590, 262, "Status IN (requested, processing, failed)", 13, 400, MUTED, "start")
    d.box(590, 285, 500, 100, "getActionItemsUpstream — v3.3.28",
          ["Scan + filter · no limit · no nextToken", "stops after 1 MB of table read ✗"],
          color=RED, fill="#FDF0EE", title_color=RED, size=14)
    d.box(590, 410, 500, 100, "getActionItemsPaginated — the fix",
          ["Scan + filter + limit + nextToken", "client loops until nextToken is null ✓"],
          color=GREEN, fill="#EEF7EE", title_color="#1F6B1F", size=14)
    d.box(590, 535, 500, 110, "getActionItemsByStatus — alternative fix",
          ["Query Status-index + limit + nextToken", "reads only matching rows ✓ (--with-gsi)"],
          color=TEAL, fill="#E6F6F7", title_color="#006570", size=14, dash="6,4")

    # data source + table
    d.node("AWS-Identity-Access-Management_Role", 1250, 460,
           ["Data source role", "NET-020-appsync-ddb", "Scan · Query · GetItem"])
    d.node("Amazon-DynamoDB_Table", 1430, 460, ["NET-020-action-items", "PK SubnetId · SK Version",
                                                 "no GSI (same as STNO)"], pos="above")
    d.node("Amazon-DynamoDB_Global-secondary-index", 1340, 640, ["Status-index GSI", "optional"],
           size=48, pos="left")

    # Scan page split
    d.text(1760, 345, "One Scan page = 1 MB of table data,", 15, 700, "#B85A00")
    d.text(1760, 365, "read before the filter runs", 15, 700, "#B85A00")
    d.box(1570, 395, 220, 120, "page 1 · ≤ 1 MB", ["≈ 2,050 items (~60 %)", "all upstream ever sees"],
          color=RED, fill="#FDF0EE", title_color=RED, size=13)
    d.box(1800, 395, 100, 120, "page 2", ["≈ 1,350", "never", "read ✗"],
          color="#7D8998", fill="#F2F3F3", size=13, dash="6,4")
    d.note(1570, 560, 330, 145, "Synthetic data (seed 3,400)", [
        "510 B/item → ~1.65 MB table",
        "~425 pending, spread out",
        "seed 1200 → 0.58 MB:",
        "fits one page, bug disappears",
    ], color="#ED7100", fill="#FFF6EC", title_color="#B85A00")

    d.note(370, 790, 1150, 110, "Not deployed — not needed to reproduce", [
        "STNO stacks · TGW · CloudFront + S3 · Cognito · WAF · Step Functions · EventBridge · SNS · Lambda",
        "The write path works in the customer environment; only the read path (AppSync + DynamoDB) is broken.",
    ], color="#7D8998", fill="#F7F9FC", title_color="#5A6B7D", size=13)

    # client -> AppSync
    d.arrow([(160, 425), (406, 425)], MUTED)
    d.text(285, 412, "GraphQL · x-api-key", 13, 600, MUTED, bg=True)

    # flow 1: upstream (red)
    d.arrow([(470, 410), (530, 410), (530, 335), (584, 335)], RED)
    d.arrow([(1090, 335), (1135, 335), (1135, 445), (1218, 445)], RED)
    d.badge(557, 335, 1, RED)
    # flow 2: paginated (green)
    d.arrow([(472, 455), (584, 455)], GREEN)
    d.arrow([(1090, 460), (1218, 460)], GREEN)
    d.badge(530, 455, 2, GREEN)
    # flow 3: GSI query (teal)
    d.arrow([(465, 475), (500, 475), (500, 590), (584, 590)], TEAL)
    d.arrow([(1090, 590), (1135, 590), (1135, 475), (1218, 475)], TEAL)
    d.badge(500, 540, 3, TEAL)
    # role -> table
    d.arrow([(1282, 460), (1398, 460)], MUTED)
    d.text(1340, 448, "Scan / Query", 13, 600, MUTED, bg=True)
    d.line([(1415, 490), (1415, 640), (1366, 640)], MUTED, dash=True)
    # table -> page split
    d.line([(1462, 460), (1564, 460)], RED, dash=True)
    # ground truth (baseline)
    d.arrow([(160, 445), (265, 445), (265, 750), (1445, 750), (1445, 494)], MUTED, dash=True)
    d.text(800, 742, "ground truth: boto3 Scan with ExclusiveStartKey until LastEvaluatedKey is empty",
           13, 600, MUTED, bg=True)

    d.legend([
        (RED, 1, "Upstream read: one Scan page, truncates silently"),
        (GREEN, 2, "Fix: paginated Scan with limit + nextToken"),
        (TEAL, 3, "Alternative: Query on the Status GSI"),
        (MUTED, None, "Baseline read straight from DynamoDB (truth)", "dash"),
    ], cols=2)
    d.save(HERE / "architecture")


# ======================================================================
# Figure 2 — customer hub-and-spoke (generalised)
# ======================================================================
def customer():
    d = Diagram(2200, 1460)

    d.group(200, 20, 1760, 1290, "cloud", "AWS Cloud")
    d.group(230, 80, 1700, 1210, "region", "Region us-east-1")
    d.group(260, 140, 1100, 1090, "account", "Network hub account")
    d.group(1390, 140, 510, 500, "account", "150+ spoke accounts (STNO spoke stack)")
    d.group(1390, 680, 510, 220, "account", "Inspection account")
    d.group(1990, 930, 190, 200, "dc", "On-premises")

    # --- STNO hub stack
    d.band(285, 200, 610, 1010, "TransitGatewayHub stack · STNO v3.3.28", color="#E7157B",
           label_color="#B0105C", fill_opacity=0.03)
    d.node("Users", 100, 310, ["Network", "operators"])
    d.node("Amazon-CloudFront", 390, 310, ["CloudFront", "+ CF Function"])
    d.node("Amazon-Simple-Storage-Service", 590, 310, ["S3 console", "React SPA"])
    d.node("Amazon-Cognito", 800, 310, ["Cognito", "user + identity pool"])
    d.node("AWS-WAF", 390, 510, ["WAFv2 WebACL", "+ IPSet allowlist"])
    d.node("AWS-AppSync", 590, 510, ["AppSync", "VTL resolvers"])
    d.node("AWS-Lambda", 770, 510, ["Lambda CR", "(orchestrator)"])
    d.node("Amazon-DynamoDB", 590, 720, ["DynamoDB table", "~3,400 items · ~1.7 MB", "PK SubnetId + SK Version",
                                         "no GSI · TTL 90 d"], pos="left", label_size=14)
    d.node("AWS-Step-Functions", 770, 720, ["Step", "Functions"], pos="right")
    d.node("Amazon-EventBridge", 390, 930, ["EventBridge bus", "one rule per spoke"])
    d.node("AWS-Lambda", 770, 930, ["Lambda", "(SM fn)"], pos="right")
    d.node("Amazon-Simple-Notification-Service", 770, 1100, ["SNS", "approvals"], pos="right")
    d.note(355, 1020, 330, 145, "BUG (v3.3.28, upstream)", [
        "one Scan + filter, no limit /",
        "nextToken · reads ≤ 1 MB ≈ 60 %",
        "UI calls once, ignores nextToken",
        "→ pending past page 1 invisible",
    ], color=RED, fill="#FDF0EE", title_color=RED, size=13)

    # --- Transit Gateway
    d.band(920, 200, 420, 1010, None, color="#8C4FFF", fill_opacity=0.04)
    d.node("AWS-Transit-Gateway", 965, 250, ["Transit Gateway · ASN 64512", "160+ attachments · 150+ accounts",
                                             "assoc/prop defaults OFF · auto-accept ON"],
           size=48, pos="right", label_size=13)
    d.node("AWS-Resource-Access-Manager", 965, 335, ["RAM share", "external principals allowed"],
           size=44, pos="right", label_size=13)
    rts = [
        ("HUB-SPOKE-TGW-RTB · STNO · Approval=Yes", "most spoke VPCs · ~30 VPC + DXGW propagated"),
        ("FROM-On-premises-Shared · STNO · Approval=Yes", "assoc DXGW + hub VPCs · prop every spoke"),
        ("Dev · STNO · Approval=Yes", "a handful of dev VPCs"),
        ("Inspection-Spoke-RT-prod · Terraform", "public-facing VPCs · 0/0 → inspection VPC"),
        ("Inspection-Firewall-RT-prod · Terraform", "inspection VPC · static /24 · 100.64/10 blackhole"),
        ("SelfManaged-SpokeVPCs-TGWRT · separate CFN", "1 VPC · 0/0 and ::/0 → DXGW (DX-only)"),
        ("Flat · Isolated · Infrastructure · v3.3.28", "Approval=No · created by the upgrade · unused"),
    ]
    ys = []
    for i, (t, l) in enumerate(rts):
        y = 395 + i * 86
        ys.append(y + 37)
        d.box(935, y, 390, 74, t, [l], size=12, align="start", color="#8C4FFF")
    d.box(935, 1000, 390, 74, "1 unassociated attachment (dev VPC)", [
        "= what a pending / rejected request leaves"], size=12, align="start",
        color="#ED7100", fill="#FFF6EC", title_color="#B85A00")
    d.note(935, 1090, 390, 105, "CLI workaround", [
        "AppSync mutation or state machine: OK",
        "direct TGW APIs: bypass STNO → drift",
    ], size=12)

    # --- Spoke accounts
    d.group(1420, 200, 450, 70, "vpc", "most VPCs → HUB-SPOKE")
    d.group(1420, 290, 450, 70, "vpc", "public-facing VPCs → Inspection-Spoke")
    d.group(1420, 380, 450, 70, "vpc", "dev → Dev · 1 VPC → SelfManaged")
    d.box(1420, 480, 450, 110, "Tags on VPC / subnet",
          ["Associate-with · Propagate-to", "→ event to the hub bus (cross-account)"], size=13)
    d.group(1420, 745, 450, 120, "vpc", "Inspection VPC · appliance mode ON")
    d.text(1645, 830, "contents not verified", 14, 400, MUTED, italic=True)

    # --- DX
    d.node("AWS-Direct-Connect_Gateway", 1560, 1030, ["Direct Connect gateway", "ASN 65001 · hub-owned"])
    d.node("Office-building", 2085, 1040, ["Customer", "data center"])

    # --- console path (supporting)
    d.arrow([(130, 310), (358, 310)], MUTED)
    d.text(245, 298, "HTTPS", 13, 600, MUTED, bg=True)
    d.arrow([(422, 310), (558, 310)], MUTED)
    d.text(490, 298, "OAI", 13, 600, MUTED, bg=True)
    d.arrow([(100, 278), (100, 245), (800, 245), (800, 278)], MUTED, dash=True)
    d.text(650, 245, "Cognito login (JWT)", 13, 600, MUTED, bg=True)
    d.arrow([(100, 385), (100, 510), (358, 510)], MUTED)
    d.text(220, 498, "GraphQL + JWT", 13, 600, MUTED, bg=True)
    d.arrow([(422, 510), (558, 510)], MUTED)

    # flow 1: failing read
    d.arrow([(590, 585), (590, 688)], RED)
    d.badge(590, 605, 1, RED)
    d.text(575, 645, ["Scan ≤ 1 MB", "no nextToken"], 13, 700, RED, "end", bg=True)
    # flow 2: approval path
    d.arrow([(622, 510), (738, 510)], GREEN)
    d.badge(680, 510, 2, GREEN)
    d.text(680, 480, "accept / reject", 13, 600, GREEN, bg=True)
    d.arrow([(770, 585), (770, 688)], GREEN)
    d.text(782, 630, "StartExecution", 13, 600, GREEN, "start", bg=True)
    d.arrow([(738, 700), (622, 700)], GREEN)
    d.text(680, 688, "Status=requested", 13, 600, GREEN, bg=True)
    d.arrow([(770, 752), (770, 898)], GREEN)
    # flow 3: network data plane
    d.arrow([(802, 950), (905, 950), (905, 250), (935, 250)], PURPLE)
    d.badge(905, 600, 3, PURPLE)
    d.arrow([(1420, 235), (1365, 235), (1365, 432), (1331, 432)], PURPLE)
    d.text(1365, 300, ["spoke", "attachments"], 13, 600, PURPLE, bg=True)
    d.arrow([(1325, ys[3]), (1375, ys[3]), (1375, 805), (1414, 805)], PURPLE)
    d.text(1375, 760, "0/0", 13, 600, PURPLE, bg=True)
    d.arrow([(1325, ys[1]), (1350, ys[1]), (1350, 1030), (1528, 1030)], PURPLE)
    d.text(1440, 1018, "DXGW attachment", 13, 600, PURPLE, bg=True)
    d.arrow([(1592, 1030), (2053, 1030)], PURPLE)
    d.text(1800, 1018, "Direct Connect", 13, 600, PURPLE, bg=True)

    # async events (grey dashed)
    d.arrow([(422, 940), (700, 940), (700, 740), (738, 740)], MUTED, dash=True)
    d.text(560, 952, "tag event", 13, 600, MUTED, bg=True)
    d.arrow([(770, 962), (770, 1066)], MUTED, dash=True)
    d.arrow([(1870, 545), (1915, 545), (1915, 1260), (320, 1260), (320, 930), (356, 930)], MUTED, dash=True)
    d.text(1100, 1260, "tag events (cross-account)", 13, 600, MUTED, bg=True)

    d.legend([
        (RED, 1, "Pending-requests read: single-page Scan (fails)"),
        (GREEN, 2, "Approval path: mutation → Lambda → Step Functions"),
        (PURPLE, 3, "Network data plane: attachments, routes, DX"),
        (MUTED, None, "Console path · async events and notifications"),
    ], cols=2)
    d.save(HERE / "architecture_customer")


lab()
customer()
