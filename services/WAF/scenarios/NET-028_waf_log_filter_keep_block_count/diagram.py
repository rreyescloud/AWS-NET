"""NET-028 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, MUTED  # noqa: E402

d = Diagram(1960, 1020)

# --- Groups
d.group(240, 20, 1700, 740, "cloud", "AWS Cloud")
d.group(270, 80, 1640, 660, "region", "Region (one Firehose stream per Region)")
d.group(300, 140, 1580, 580, "account", "Account <ACCOUNT_A> (production)")

# --- Viewers
d.node("Users", 110, 330, ["Viewers", "streaming app / web"])

# --- Web ACL
d.node("AWS-WAF", 420, 330, None)
d.box(320, 400, 250, 180, "web ACL <webacl-app>", [
    "custom rules", "(some in Count)", "managed groups", "(sub-rules → Count)", "default: Allow",
], color="#DD344C", fill="#FFF5F6", size=13)

# --- Log record
d.box(620, 245, 330, 170, "Log record", [
    "action: ALLOW",
    "nonTerminatingMatchingRules: COUNT",
    "ruleGroupList…COUNT (overridden)",
    "labels: awswaf:managed:aws:…",
], color=BLUE, fill="#EEF4FB", size=13)

# --- Logging filter
d.band(1000, 190, 440, 290, None, color="#DD344C")
d.node("AWS-WAF_Filtering-Rule", 1045, 232, ["Logging filter", "top-down · first match wins"],
       size=44, pos="right", weight=600, label_size=14)
d.box(1020, 285, 400, 86, "1  KEEP · MEETS_ANY", ["BLOCK · COUNT · EXCLUDED_AS_COUNT", "CAPTCHA · CHALLENGE"],
      color=GREEN, fill="#F0F8F0", title_color=GREEN, size=13)
d.box(1020, 390, 400, 60, "default → DROP", color=MUTED, fill="#F7F9FC", title_color=MUTED, size=13)

# --- Delivery
d.node("Amazon-Data-Firehose", 1560, 330, ["Amazon Data Firehose", "aws-waf-logs-…"], label_size=14)
d.node("Amazon-Simple-Storage-Service", 1780, 330, ["Amazon S3", "security log archive"], label_size=14)
d.box(1480, 530, 360, 100, "Dropped before delivery", ["benign ALLOW (no Count match)", "no Firehose / S3 cost"],
      color=MUTED, fill="#F7F9FC", size=13, dash="6,4")

# --- Flows
d.arrow([(150, 330), (388, 330)], ORANGE)
d.badge(195, 330, 1, ORANGE)
d.text(195, 305, "HTTPS", 14, 600, ORANGE, bg=True)
d.arrow([(452, 330), (614, 330)], BLUE)
d.badge(535, 330, 2, BLUE)
d.text(535, 305, "decision", 13, 600, BLUE, bg=True)
d.arrow([(950, 330), (994, 330)], BLUE)
d.arrow([(1420, 328), (1526, 328)], GREEN)
d.badge(1470, 328, 3, GREEN)
d.text(1470, 300, "KEEP", 13, 700, GREEN, bg=True)
d.arrow([(1594, 330), (1746, 330)], GREEN)
d.text(1670, 305, "buffered", 13, 600, GREEN, bg=True)
d.arrow([(1420, 420), (1660, 420), (1660, 524)], MUTED, dash=True)
d.text(1540, 448, "DROP", 13, 700, MUTED, bg=True)

d.note(1000, 520, 440, 125, "Trap: old ALLOW → DROP left on top", [
    "an ActionCondition matches ANY action in",
    "the record, so it matches first and drops",
    "ALLOW + COUNT records. Remove it.",
], color=RED, fill="#FDF0EE", title_color=RED, size=13)
d.note(620, 470, 330, 145, "Why COUNT survives", [
    "the KEEP filter tests COUNT /",
    "EXCLUDED_AS_COUNT, never ALLOW,",
    "so the final ALLOW does not matter",
    "and the default DROP never applies",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", size=13)

# --- Answers
d.note(20, 790, 465, 110, "Q2 · Count rules", [
    "caught whatever the final action;",
    "not if an earlier Allow rule ended the",
    "request (origin-playback: 98.6 %)"], size=13)
d.note(505, 790, 465, 110, "Q3 · action vs label", [
    "start action-based; labels need fully",
    "qualified names · no WCU, no latency;",
    "hybrid: label DROP above the KEEP"], size=13)
d.note(990, 790, 465, 110, "Q4 · cost", [
    "≤ 527 M extra records / month",
    "≈ USD 130–190 / month (≈ 99 % of the",
    "saving kept) · Marketplace hosting = 73 %"], size=13)
d.note(1475, 790, 465, 110, "Also check", [
    "CAPTCHA / CHALLENGE in the KEEP list;",
    "high-volume Count rules erase the saving;",
    "label case: NoUserAgent_Header"], size=13)

d.legend([
    (ORANGE, 1, "Viewer requests → web ACL"),
    (BLUE, 2, "Decision → log record → logging filter"),
    (GREEN, 3, "KEEP → Firehose → S3"),
    (MUTED, None, "DROP: never delivered", "dash"),
], cols=4)

d.save(HERE / "architecture")
