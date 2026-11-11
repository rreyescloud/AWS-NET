"""NET-027 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, GREEN, RED, MUTED, INK  # noqa: E402

d = Diagram(1960, 1060)

# --- Groups
d.group(260, 20, 1680, 760, "cloud", "AWS Cloud")
d.group(290, 80, 800, 670, "generic", "Edge · web ACL scope CLOUDFRONT (us-east-1)")
d.group(1120, 80, 790, 670, "account", "Account <ACCOUNT_A> (production)")
d.group(1150, 140, 730, 580, "region", "Region <region>")
d.group(1180, 200, 670, 490, "vpc", "VPC")

# --- Browser + evidence
d.node("User", 110, 385, ["Browser", "dashboard click"])
d.note(20, 500, 225, 140, "HAR: 3 × 403", [
    "Request blocked page",
    "Server: CloudFront",
    "X-Cache: Error from",
    "cloudfront · all carry",
    "up_zqltext",
], color=RED, fill="#FDF0EE", title_color=RED, size=13)

# --- Edge
d.node("Amazon-CloudFront", 400, 385, ["CloudFront", "jira.example.com"], weight=600)
d.box(305, 500, 200, 150, "Request", [
    "/jira/issues/?jql=",
    'project = "…" and',
    "issuetype = Test …",
    "gadgets: up_zqltext=…",
], color=ORANGE, fill="#FFF6EC", size=13)

d.band(520, 130, 545, 590, None, color="#DD344C")
d.node("AWS-WAF", 570, 185, ["web ACL <webacl-jira>", "default Allow · no pinned versions"],
       size=48, pos="right", weight=600, label_size=14)
rows = [
    ("0  custom rule group", None),
    ("1  fallback-URL rule group", None),
    ("2  CommonRuleSet (most rules excluded)", None),
    ("3  SQLiRuleSet → SQLi_QUERYARGUMENTS", "hit"),
    ("fix: label SQLi_QueryArguments AND NOT", "fix"),
    ("4  KnownBadInputsRuleSet", None),
    ("5  LinuxRuleSet", None),
    ("6  AmazonIpReputationList", None),
    ("default action: Allow", "default"),
]
y = 240
for text, kind in rows:
    if kind == "hit":
        d.box(545, y, 495, 44, text, color=RED, fill="#FDF0EE", title_color=RED, size=13, align="start")
    elif kind == "fix":
        d.box(545, y, 495, 52, text, ["tracker paths → Block (after the group)"], color=GREEN,
              fill="#F0F8F0", title_color=GREEN, size=12, dash="6,4", align="start")
        y += 8
    elif kind == "default":
        d.box(545, y, 495, 40, text, color=MUTED, fill="#F7F9FC", title_color=MUTED, size=13, align="start")
    else:
        d.box(545, y, 495, 40, text, color="#D5DBE3", title_color=INK, size=13, align="start")
    y += 50

# --- Origin
d.node("Elastic-Load-Balancing_Application-Load-Balancer", 1300, 420, ["ALB"])
d.node("Amazon-EC2_Instance", 1640, 420, ["Apache + Jira", "JQL parsed by the app"])

# --- Flows
d.arrow([(150, 370), (370, 370)], ORANGE)
d.arrow([(430, 370), (514, 370)], ORANGE)
d.badge(210, 370, 1, ORANGE)
d.arrow([(540, 397), (432, 397)], RED)
d.arrow([(368, 400), (150, 400)], RED)
d.badge(290, 400, 2, RED)
d.mark(470, 420, False)

d.arrow([(1066, 420), (1266, 420)], GREEN)
d.badge(1225, 420, 3, GREEN)
d.arrow([(1334, 420), (1606, 420)], GREEN)
d.mark(1700, 380, True)

# --- Findings
d.note(1210, 530, 610, 140, "“No changes on our side” = a config change", [
    "08:17  all 8 SQLi sub-rules set to Count",
    "15:21  Admin B: SQLi_QUERYARGUMENTS back to Block",
    "17:17  set to Count again (temporary fix)",
    "BlockedRequests 1–78/h → 694 · 1,192 · 281 → none",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", size=13)
d.note(20, 810, 940, 100, "Why JQL matches", [
    "SQLi_QUERYARGUMENTS (sensitivity LOW) reads every query arg;",
    'matchedData project = "…" and issuetype = field · operator · literal · and',
], color=RED, fill="#FDF0EE", title_color=RED, size=13)
d.note(1000, 810, 940, 100, "Fix: Count + label rule", [
    "the sub-rule in Count still adds its label; a rule AFTER the group blocks",
    "on the label unless the path is /jira/issues · /gadgets/ · /rest/",
], color=GREEN, fill="#F0F8F0", title_color=GREEN, size=13)

d.legend([
    (ORANGE, 1, "Dashboard request with JQL / ZQL in the query"),
    (RED, 2, "SQLi_QUERYARGUMENTS Block → CloudFront 403 (15:21–17:17 UTC)"),
    (GREEN, 3, "Count / no match → origin (200)"),
], cols=3)

d.save(HERE / "architecture")
