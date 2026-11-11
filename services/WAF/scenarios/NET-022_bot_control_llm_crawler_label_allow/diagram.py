"""NET-022 diagrams -> architecture.png (problem) and architecture_fix.png (fix + lab variant)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, GREEN, RED, PURPLE, MUTED  # noqa: E402

GREY_FILL, GREY = "#F4F5F7", "#7D8998"
ORANGE_FILL, RED_FILL, GREEN_FILL = "#FFF4E5", "#FDF0EE", "#EEF7EE"


def rule(d, y, h, title, lines=(), kind="grey", x=790, w=700, dash=None):
    color, fill, tcolor = {
        "grey": (GREY, GREY_FILL, "#232F3E"),
        "orange": (ORANGE, ORANGE_FILL, "#B35F00"),
        "red": (RED, RED_FILL, RED),
        "green": (GREEN, GREEN_FILL, GREEN),
    }[kind]
    d.box(x, y, w, h, title, lines, color=color, fill=fill, title_color=tcolor, size=14,
          align="start", dash=dash)


# =====================================================================
# Figure 1 — problem: two independent blockers
# =====================================================================
d = Diagram(2000, 1260)

d.group(20, 140, 340, 600, "internet", "Internet")
d.group(390, 20, 1590, 1090, "cloud", "AWS Cloud")
d.group(420, 80, 1530, 1010, "region", "Global · CLOUDFRONT scope (WAF in us-east-1)")
d.group(450, 140, 1470, 610, "account", "<ACCOUNT_APP> · application account")
d.group(450, 790, 1470, 280, "account", "<ACCOUNT_SECURITY> · Firewall Manager administrator")

# --- Clients
d.node("AWS-WAF_Bot", 190, 260, ["Flow A · LLM crawler", "ExampleAIBot/1.0", "203.0.113.0/24 · unclassified"], size=64)
d.node("AWS-WAF_Bot", 190, 450, ["Flow B · LLM crawler", "bot:name:<known-ai-bot>", "verified"], size=64)
d.node("Client", 190, 630, ["Human visitor", "browser UA"], size=56)

# --- Edge + origin
d.node("Amazon-CloudFront", 600, 380, ["CloudFront", "www.example.com"], size=80, weight=600, pos="above")
d.node("Server", 600, 620, ["CMS origin", "PHP marketing site"])

# --- Web ACL evaluation stack
d.band(760, 190, 760, 490, "Web ACL (FMS-managed) · evaluated top to bottom · default Allow",
       color="#C7186E", label_color="#B0105C", fill_opacity=0.04)
rule(d, 235, 50, "1 · FMS pre-process custom groups",
     ["PHP probe block · allow-by-IP-set · NAT egress allow (misnamed) · plugin allow"])
rule(d, 300, 100, "2 · AWSManagedRulesBotControlRuleSet · COMMON · version not pinned",
     ["SignalNonBrowserUserAgent → Count (adds signal label, skips verified bots)",
      "CategoryAI → Block — verified and unverified AI bots alike",
      "default Version_1.0: no bot:name:* label for the vendor's crawler"], kind="orange")
rule(d, 415, 50, "3–7 · geo-block · body-size ×4 · IP reputation · CRS · CMS / SQLi / PHP")
rule(d, 480, 110, "8 · FMS post-process <allow_seo_crawler_ua> — action BLOCK, despite the name",
     ["label signal:non_browser_user_agent",
      "AND NOT User-Agent EXACTLY \"<seo-crawler-ua-string>\" (no transformation)",
      "last group in the web ACL (12 WCU)"], kind="red")
rule(d, 605, 46, "default action → Allow", kind="green")

# --- Outcomes
d.box(1560, 300, 330, 100, "403 · blocker B", ["terminatingRule: CategoryAI", "bot:category:ai · bot:verified"],
      color=ORANGE, fill=ORANGE_FILL, title_color="#B35F00", size=14)
d.box(1560, 485, 330, 100, "403 · blocker A (primary)",
      ["terminating: <allow_seo_crawler_ua>", "only signal:non_browser_user_agent"],
      color=RED, fill=RED_FILL, title_color=RED, size=14)

# --- Flows into CloudFront
d.arrow([(225, 260), (500, 260), (500, 355), (556, 355)], RED)
d.badge(380, 260, 1, RED)
d.arrow([(225, 450), (480, 450), (480, 380), (556, 380)], ORANGE)
d.badge(380, 450, 2, ORANGE)
d.arrow([(222, 630), (520, 630), (520, 405), (556, 405)], GREEN)
d.badge(380, 630, 3, GREEN)
d.arrow([(644, 380), (680, 380), (680, 260), (786, 260)], MUTED)
d.text(700, 225, "GET", 13, 600, MUTED, bg=True)

# --- Flows out of the web ACL
d.arrow([(1494, 350), (1556, 350)], ORANGE)
d.badge(1525, 318, 2, ORANGE)
d.arrow([(1494, 535), (1556, 535)], RED)
d.badge(1525, 503, 1, RED)
d.arrow([(786, 628), (700, 628), (700, 620), (632, 620)], GREEN)
d.badge(730, 628, 3, GREEN)

# --- Security account
d.node("AWS-Firewall-Manager", 1000, 940, ["Firewall Manager policy", "<fms-web-policy>"])
d.node("Amazon-Simple-Storage-Service_Bucket", 1200, 940, ["S3 aws-waf-logs-*", "full WAF logs (Athena)"])
d.arrow([(1000, 908), (1000, 684)], MUTED, dash=True)
d.text(1000, 776, "pushes web ACL", 13, 600, MUTED, bg=True)
d.arrow([(1200, 684), (1200, 908)], MUTED, dash=True)
d.text(1200, 860, "logs", 13, 600, MUTED, bg=True)
d.note(1330, 830, 570, 180, "Evidence", [
    "availableLabels (default version): no label for this vendor",
    "Vendor IP ranges: 90–95% terminated by the custom rule",
    "Rule group: BLOCK on label AND NOT exact User-Agent",
    "Lab: Version_6.x classifies the Claude crawlers, 1.0 doesn't",
    "Not confirmed: per-request log samples",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")

# --- Lesson (outside the cloud)
d.note(20, 780, 340, 175, "Lesson", [
    "The group is named \"allow\".",
    "Its action is BLOCK and it sits",
    "in post-process, where nobody",
    "looked first. Read the action",
    "and statement, not the name.",
])

d.legend([
    (RED, 1, "Flow A · unclassified crawler → post-process rule blocks (403)"),
    (ORANGE, 2, "Flow B · classified AI crawler → CategoryAI blocks (403)"),
    (GREEN, 3, "Human visitor → default Allow → origin"),
    (MUTED, None, "FMS pushes the web ACL · WAF logs", "dash"),
], cols=2)

d.save(HERE / "architecture")


# =====================================================================
# Figure 2 — fix and lab variant
# =====================================================================
d = Diagram(2000, 1290)

d.group(20, 140, 330, 560, "internet", "Lab clients")
d.group(390, 20, 1200, 1110, "cloud", "AWS Cloud")
d.group(420, 80, 1140, 1030, "region", "us-east-1 · CLOUDFRONT scope")
d.group(450, 140, 1070, 950, "account", "Lab account (no FMS)")

# --- Clients
d.node("Client", 185, 262, ["Other IP (CloudShell)", "not in the IP set"], size=56)
d.node("Client", 185, 440, ["Lab client", "IP in vendor-crawlers set"], size=56)
d.text(185, 560, ["curl -A ExampleAIBot/1.0", "<ai-ua-classified>", "python-requests · browser"],
       13, 400, "#5A6B7D")

# --- Distributions
d.node("Amazon-CloudFront", 560, 280, ["CloudFront · marketing", "crawlers allowed (GET/HEAD)"],
       size=64, pos="above", weight=600, label_size=14)
d.node("Amazon-CloudFront", 560, 560, ["CloudFront · portal", "stands in for authenticated apps"],
       size=64, weight=600, label_size=14)
d.arrow([(594, 280), (718, 280)], MUTED)
d.arrow([(594, 560), (718, 560)], MUTED)
d.text(655, 545, "same web ACL", 12, 600, MUTED, bg=True)
d.text(655, 265, "same web ACL", 12, 600, MUTED, bg=True)

# --- Web ACL stack
d.band(720, 190, 760, 730, "Web ACL NET-022-acl · default Allow",
       color="#C7186E", label_color="#B0105C", fill_opacity=0.04)
rule(d, 235, 46, "Pre-process slot · empty", ["Exercise 5 moves P1 here: allowed requests then skip IP reputation and CRS"],
     x=750, w=710, dash="6,4")
rule(d, 295, 100, "Bot Control · COMMON",
     ["Step 0: pin a version that labels the crawler (e.g. Version_6.1)",
      "Step 1: CategoryAI → Count — still labels, no longer terminates",
      "every other category rule → Block (python-requests still 403)"], kind="orange", x=750, w=710)
rule(d, 410, 46, "AWSManagedRulesAmazonIpReputationList · AWSManagedRulesCommonRuleSet", x=750, w=710)
d.band(745, 470, 725, 370, "Post-process group (Step 2) · every managed group already inspected the request",
       color="#5A6B7D", label_color="#232F3E", fill_opacity=0.03)
rule(d, 505, 80, "P0 ALLOW · covers current and future verified AI bots",
     ["Host marketing AND GET/HEAD AND ( bot:category:ai AND (bot:verified OR",
      "bot:user_triggered:verified) OR bot:web_bot_auth:verified )"], kind="green", x=765, w=690)
rule(d, 595, 80, "P1 ALLOW (interim) · unclassified crawler only",
     ["Host marketing AND GET/HEAD AND UA CONTAINS ExampleAIBot",
      "AND source IP IN NET-022-vendor-crawlers (UA alone is spoofable)"], kind="green", x=765, w=690, dash="6,4")
rule(d, 685, 60, "P2 BLOCK · label bot:category:ai",
     ["unverified or spoofed AI UAs, other hosts and methods"], kind="red", x=765, w=690)
rule(d, 755, 70, "P3 BLOCK · replaces the exact-UA rule",
     ["signal:non_browser_user_agent AND NOT label bot:name:<seo-crawler>"], kind="red", x=765, w=690)
rule(d, 855, 46, "default action → Allow", kind="green", x=750, w=710)

# --- Supporting resources
d.node("Amazon-Simple-Storage-Service_Bucket", 560, 760, ["S3 origin (private)", "OAC · index.html"])
d.node("Data-Table", 560, 960, ["IP set", "NET-022-vendor-crawlers", "lab client /32"], size=48)
d.node("Amazon-CloudWatch_Logs", 1150, 990, ["CloudWatch Logs", "aws-waf-logs-net022 (1 day)"],
       size=48, pos="right")
d.arrow([(1150, 920), (1150, 962)], MUTED, dash=True)
d.arrow([(586, 960), (700, 960), (700, 660), (761, 660)], PURPLE, dash=True)
d.text(640, 945, "referenced by P1", 12, 600, PURPLE, bg=True)

# --- Flows
d.arrow([(215, 262), (524, 262)], RED)
d.badge(380, 262, 2, RED)
d.arrow([(215, 430), (470, 430), (470, 300), (524, 300)], GREEN)
d.badge(300, 430, 1, GREEN)
d.arrow([(215, 452), (490, 452), (490, 550), (524, 550)], ORANGE)
d.badge(420, 452, 3, ORANGE)
d.arrow([(763, 625), (680, 625), (680, 760), (592, 760)], GREEN)
d.badge(680, 700, 1, GREEN)
d.text(722, 612, "200", 13, 700, GREEN, bg=True)
d.arrow([(1457, 790), (1500, 790), (1500, 1050), (1300, 1050)], RED)
d.badge(1500, 990, 2, RED)
d.badge(1460, 1050, 3, ORANGE)
d.text(1290, 1056, "403", 15, 700, RED, "end")

# --- Notes (right column)
d.note(1610, 140, 375, 195, "Expected after the fix (Ex. 3)", [
    "ExampleAIBot · lab IP · marketing → 200 (P1)",
    "ExampleAIBot · other IP → 403 (P3)",
    "ExampleAIBot · POST → 403 (P3)",
    "ExampleAIBot · portal → 403 (P3)",
    "classified AI UA · lab IP → 403 (P2)",
    "python-requests → 403 (HttpLibrary)",
    "Browser · both distributions → 200",
], color="#2E8B2E", fill="#EEF7EE", title_color="#2E8B2E", align="start")
d.note(1610, 400, 375, 160, "Not reproducible in the lab", [
    "bot:verified and web_bot_auth:verified",
    "need the real crawler infrastructure.",
    "From a lab IP a classified AI UA is",
    "always bot:unverified.",
], color="#E07B00", fill="#FFF4E5", title_color="#B35F00", align="start")
d.note(1610, 590, 375, 180, "Permanent path", [
    "Pin Bot Control to a version that",
    "labels the crawler, or request a",
    "classification via AWS Support.",
    "Then P0 covers it and P1 is removed.",
    "Run P0–P3 in Count 24–48 h first.",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", align="start")

d.legend([
    (GREEN, 1, "Lab client · ExampleAIBot GET on marketing → P1 allow → 200"),
    (RED, 2, "Same UA from another IP → P3 block → 403"),
    (ORANGE, 3, "Same UA on portal (Host ≠ marketing) → P3 → 403"),
    (MUTED, None, "Same web ACL · IP set reference · logs", "dash"),
], cols=2)

d.save(HERE / "architecture_fix")
