"""NET-029 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, TEAL, MUTED  # noqa: E402

d = Diagram(2000, 1220)

# --- Groups
d.group(20, 20, 1640, 880, "cloud", "AWS Cloud")
d.group(50, 80, 1580, 800, "region", "Region")
d.group(80, 140, 520, 720, "account", "App account (spokes)")
d.group(110, 205, 460, 450, "vpc", "Spoke VPC · 10.10.0.0/16")
d.group(140, 265, 400, 240, "private_subnet", "Workload subnet")
d.group(110, 690, 460, 140, "vpc", "Spoke VPC · 10.20.0.0/16")
d.text(340, 790, "own .2 Resolver · same anchor association", 14, 400, "#6A35D9", italic=True)
d.group(630, 140, 970, 720, "account", "Network account")
d.group(720, 205, 850, 520, "vpc", "Inspection VPC · 10.0.0.0/16")
d.group(750, 265, 390, 240, "private_subnet", "Firewall subnet")
d.group(1160, 265, 390, 240, "public_subnet", "Public subnet")
d.group(1690, 230, 290, 560, "internet", "Internet")

# --- Nodes
d.node("Amazon-EC2_Instance", 300, 410, ["Workload", "10.10.1.10"], pos="left")
d.node("Amazon-Route-53_Resolver", 300, 580, ["Route 53 Resolver", "VPC .2"], pos="left")
d.node("AWS-Transit-Gateway", 665, 410, "TGW", pos="above")
d.node("AWS-Network-Firewall_Endpoints", 940, 410, ["Network Firewall", "Route53ResolvedIPValidation"],
       weight=600, pos="above")
d.node("Amazon-VPC_NAT-Gateway", 1290, 410, "NAT gateway")
d.node("Amazon-VPC_Internet-Gateway", 1460, 410, "IGW", size=48)
d.box(740, 600, 250, 110, "Resolved IP cache",
      ["198.51.100.20 ↔", "sftp.partner.example.com", "kept longer than TTL"],
      color="#8C4FFF", fill="#F7F3FF", size=13)
d.node("Amazon-Route-53_Resolver-DNS-Firewall", 800, 790, ["DNS Firewall rule group", "anchor (may be empty)"],
       size=48, pos="right")
d.node("Amazon-CloudWatch_Logs", 1260, 790, ["FLOW + ALERT logs"], size=48, pos="right")
d.node("Server", 1835, 350, ["Partner SFTP", "sftp.partner.example.com", "198.51.100.20"])
d.node("Server", 1835, 620, ["Unresolved IP", "203.0.113.99", "hardcoded · DoH · spoofed SNI"],
       color="#A3260F")

# --- Flow 1: DNS query
d.arrow([(300, 440), (300, 546)], ORANGE)
d.badge(300, 493, 1, ORANGE)
# --- Flow 2: answer recorded in the firewall cache
d.arrow([(330, 600), (690, 600), (690, 655), (734, 655)], BLUE)
d.badge(520, 600, 2, BLUE)
d.text(520, 635, "198.51.100.20", 14, 600, BLUE, bg=True)
# --- Flow 3: TCP 22 to the resolved IP
d.arrow([(330, 410), (633, 410)], GREEN)
d.arrow([(697, 410), (906, 410)], GREEN)
d.badge(480, 410, 3, GREEN)
d.text(445, 385, "TCP 22 → 198.51.100.20", 14, 600, GREEN, bg=True)
# --- Flow 4: lookup in the cache
d.arrow([(925, 444), (925, 594)], PURPLE, both=True)
d.badge(925, 520, 4, PURPLE)
# --- Flow 5: PASS out to the partner
d.arrow([(974, 410), (1258, 410)], TEAL)
d.arrow([(1322, 410), (1432, 410)], TEAL)
d.arrow([(1488, 410), (1650, 410), (1650, 350), (1798, 350)], TEAL)
d.badge(1080, 410, 5, TEAL)
d.text(1150, 385, "PASS · .partner.example.com", 14, 600, TEAL, bg=True)
# --- Flow 6: not in cache
d.arrow([(968, 425), (1040, 425), (1040, 545), (1650, 545), (1650, 620), (1797, 620)], RED, dash=True)
d.badge(1040, 495, 6, RED)
d.mark(1250, 545, False)
d.text(1420, 575, "not in cache → DROP / REJECT / ALERT", 14, 700, RED, bg=True)

# --- Supporting: anchor association, logs
d.arrow([(776, 790), (615, 790), (615, 672), (300, 672), (300, 616)], MUTED, dash=True)
d.text(455, 677, "associated with every spoke VPC", 13, 600, MUTED, bg=True)
d.arrow([(955, 440), (1010, 440), (1010, 790), (1228, 790)], MUTED, dash=True)

# --- Notes
d.note(20, 930, 630, 110, "Two checks", [
    "Policy: was this IP resolved for ANY domain?",
    "Rule: resolved for THIS domain? (any protocol)",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")
d.note(680, 930, 640, 110, "Key settings", [
    "SourceIPRanges: set spoke CIDRs in TGW models ([] = none)",
    "DNSExpiryExceptionPolicy CONTINUE for long sessions",
])
d.note(1350, 930, 630, 110, "Rollout", [
    "ALERT → observe 1–2 weeks → REJECT",
    "enabling / changing the rule group ID may restart the engine",
], color=RED, fill="#FDF0EE", title_color=RED)

d.legend([
    (ORANGE, 1, "DNS query to the VPC Resolver"),
    (BLUE, 2, "Answer recorded in the resolved IP cache"),
    (GREEN, 3, "TCP 22 to the resolved IP via TGW"),
    (PURPLE, 4, "Firewall looks up the destination IP"),
    (TEAL, 5, "In cache + domain rule → PASS"),
    (RED, 6, "Not in cache → policy Action applies"),
], cols=3)

d.save(HERE / "architecture")
