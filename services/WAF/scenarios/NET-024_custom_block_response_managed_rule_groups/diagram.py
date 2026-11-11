"""NET-024 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, MUTED  # noqa: E402

FILL = {"grey": ("#7D8998", "#F4F5F7", "#232F3E"), "red": (RED, "#FDF0EE", RED),
        "green": (GREEN, "#EEF7EE", GREEN), "purple": (PURPLE, "#F7F3FF", "#6A35D9"),
        "orange": (ORANGE, "#FFF4E5", "#B35F00"), "blue": (BLUE, "#EEF4FB", BLUE)}


def rule(d, x, y, w, h, title, lines=(), kind="grey", dash=None, align="start"):
    c, f, t = FILL[kind]
    d.box(x, y, w, h, title, lines, color=c, fill=f, title_color=t, size=14, align=align, dash=dash)


d = Diagram(1960, 1220)

# --- Groups
d.group(20, 160, 290, 360, "internet", "Internet")
d.group(340, 20, 1600, 990, "cloud", "AWS Cloud")
d.group(370, 80, 1540, 910, "region", "Global · CLOUDFRONT scope (us-east-1)")
d.group(400, 140, 1480, 560, "account", "<ACCOUNT_APP> · distribution owner")
d.group(400, 730, 1480, 240, "account", "<ACCOUNT_SECURITY> · Firewall Manager administrator")

# --- Viewer, edge, origin
d.node("Client", 165, 330, ["Viewer", "portal.example.com"], size=64)
d.node("Amazon-CloudFront", 560, 330, ["CloudFront distribution", "custom error response 403"],
       size=80, pos="above", weight=600)
d.node("Amazon-Simple-Storage-Service_Bucket", 560, 600, ["Origin (S3 via OAC)", "/errors/403.html"])
d.arrow([(200, 330), (516, 330)], MUTED)
d.text(360, 318, "request", 13, 600, MUTED, bg=True)
d.arrow([(604, 330), (746, 330)], MUTED, both=True)
d.text(675, 318, "verdict?", 13, 600, MUTED, bg=True)

# --- Web ACL (retrofitted)
d.band(720, 190, 760, 480, "Web ACL (CLOUDFRONT) · managed by Firewall Manager · RETROFIT_EXISTING",
       color="#C7186E", label_color="#B0105C", fill_opacity=0.04)
rule(d, 750, 230, 700, 80, "preProcess · AWS managed rule groups (Core, SQLi, Known bad inputs…)",
     ["today: Block → WAF default 403 · no responseCodeSent",
      "fix B: group-level OverrideAction Count → labels only"], "red")
rule(d, 750, 325, 700, 80, "preProcess (last) · BlockManagedMatches — fix B",
     ["label namespaces → Block + custom response 403 (precedence 1)",
      "body 'blockpage' is defined inside the rule group"], "green")
rule(d, 750, 420, 700, 70, "Account's own rules (retrofit)",
     ["rate-based / custom groups with custom response · Allow rules (partners)"], "purple")
rule(d, 750, 505, 700, 70, "postProcess",
     ["wrong place for the label block: an own Allow ends evaluation first"], "grey", dash="6,4")
rule(d, 750, 590, 700, 46, "Default action · Allow", (), "grey")

# --- Outcomes
rule(d, 1530, 230, 320, 80, "403 · WAF default", ["managed rule terminates", "~70% of blocks"], "red", align="middle")
rule(d, 1530, 325, 320, 80, "403 · branded page (WAF)", ["responseCodeSent = 403", "attack still in labels"], "green", align="middle")
rule(d, 1530, 470, 320, 80, "200 · Allow won", ["XSS + partner header", "label block at priority 50"], "orange", align="middle")

d.arrow([(1452, 270), (1526, 270)], RED)
d.badge(1490, 270, 1, RED)
d.arrow([(1452, 365), (1526, 365)], GREEN)
d.badge(1490, 365, 3, GREEN)
d.arrow([(1452, 455), (1490, 455), (1490, 510), (1526, 510)], ORANGE)
d.badge(1490, 482, 4, ORANGE)

# --- Fix A: CloudFront error page
d.arrow([(560, 372), (560, 566)], BLUE, dash=True)
d.badge(560, 470, 2, BLUE)
d.text(580, 440, ["fetch error page", "(not via WAF)"], 13, 600, BLUE, "start")

# --- Firewall Manager
d.node("AWS-Firewall-Manager", 950, 860, ["Firewall Manager policy", "WAFV2 · RETROFIT_EXISTING"])
d.node("AWS-WAF_Filtering-Rule", 1240, 860, ["Rule group (admin account)", "CustomResponseBodies: blockpage"])
d.arrow([(950, 828), (950, 674)], MUTED, dash=True)
d.text(950, 712, "retrofits", 13, 600, MUTED, bg=True)
d.arrow([(1240, 828), (1240, 674)], MUTED, dash=True)
d.text(1240, 712, "last preProcess group", 13, 600, MUTED, bg=True)
d.note(1390, 770, 470, 150, "Firewall Manager run (2026-11-04)", [
    "ruleActionOverrides customResponse → block {}",
    "customResponseBodies in ManagedServiceData → dropped",
    "no PutPolicy error: always GetPolicy + diff",
], color="#C7186E")

# --- Precedence (outside the cloud)
d.note(20, 560, 290, 190, "Who answers a block", [
    "1  WAF custom response",
    "2  CloudFront error page",
    "   (by status code)",
    "3  WAF default 403",
    "never combined",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", align="start")
d.note(20, 780, 290, 140, "Fix A side effect", [
    "the 403 page also replaces",
    "origin 403s; WAF logs",
    "still show no custom code",
], color="#1F6FB8", fill="#EEF4FB", title_color="#1F6FB8", align="start")

d.legend([
    (RED, 1, "Today: managed group Block → WAF default 403"),
    (BLUE, 2, "Fix A: CloudFront serves the 403 page from the origin"),
    (GREEN, 3, "Fix B: label block in last preProcess → WAF custom response"),
    (ORANGE, 4, "Label block in postProcess: own Allow wins → 200"),
    (MUTED, None, "Request / verdict · FMS retrofit · rule group reference", "dash"),
], cols=2)

d.save(HERE / "architecture")
