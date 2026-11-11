"""NET-019 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, MUTED  # noqa: E402

d = Diagram(1900, 1330)

# --- Groups
d.group(20, 20, 1380, 1000, "cloud", "AWS Cloud")
d.group(50, 80, 1320, 920, "region", "Region us-east-1")
d.group(80, 140, 1260, 840, "vpc", "VPC · 10.60.0.0/16")
d.group(110, 200, 420, 360, "public_subnet", "Public subnet")
d.group(1440, 140, 440, 840, "internet", "Internet")

# --- Workload
d.node("Amazon-EC2_Instance", 320, 300, ["EC2 client (compromised)", "t3.micro · SSM · no inbound rules"], weight=600)
d.box(140, 410, 360, 130, "Test queries", [
    "app.company.internal", "evil.c2-lab.example", "legit.company.internal",
    "<b32>.<b32>.s12.tunnel-lab.example"], size=13, color="#7AA116", fill="#FFFFFF")
d.note(110, 590, 420, 150, "Security group egress", [
    "open: allow all (default)",
    "lockdown: UDP/TCP 53 → 10.60.0.2/32 only",
    "443 → anywhere (SSM, and DoH)",
], color="#DD344C", fill="#FDF0EE", title_color="#DD344C")

# --- Resolver, PHZ, logging
d.node("Amazon-Route-53_Resolver", 680, 300, ["VPC Resolver", "10.60.0.2", "169.254.169.253"], weight=600)
d.node("Amazon-Route-53-Hosted-Zone", 680, 630, ["PHZ company.internal", "app → 10.60.1.50", "sinkhole → 10.60.1.99"])
d.node("Amazon-Route-53_Resolver-Query-Logging", 680, 840, ["Query log → CloudWatch", "/aws/route53/net019"], size=48)

# --- Rule group (evaluation order)
d.band(820, 195, 490, 375, "Rule group NET-019-rules · priority 101 · first match acts",
       color="#DD344C", label_color="#B0213A", fill_opacity=0.05)
rows = [
    (GREEN, "#EEF7EE", "100 ALLOW · allow-business", "*.amazonaws.com · company.internal · TRUST"),
    (RED, "#FDF0EE", "200 BLOCK · block-custom", "c2-lab.example → NXDOMAIN | NODATA | OVERRIDE"),
    (RED, "#FDF0EE", "300 / 310 BLOCK · AWS managed", "malware domains · botnet C2"),
    (ORANGE, "#FFF6EB", "400 / 410 ALERT · Advanced", "DNS_TUNNELING · DGA · confidence LOW"),
    ("#7D8998", "#F7F9FC", "no match → allowed", "fail open: DISABLED (fail-closed)"),
]
for i, (c, fill, t, line) in enumerate(rows):
    d.box(840, 240 + i * 64, 450, 56, t, [line], color=c, fill=fill, size=13, align="start")
d.note(860, 600, 450, 125, "CNAME chain (Exercise 3)", [
    "legit.company.internal → cdn.c2-lab.example",
    "TRUST lets the chain through · INSPECT",
    "checks every hop",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")
d.text(1065, 760, "ALLOW must sit above the blocks it exempts (Exercise 2)", 13, 600, "#B0213A")

# --- Internet side
d.node("Server", 1660, 460, ["Public DNS / attacker's", "authoritative server", "(gets encoded labels", "if not blocked)"])
d.node("Globe", 1660, 870, ["Public resolver 8.8.8.8", "DNS Firewall never sees it", "no query log entry"], pos="above")
d.node("Amazon-VPC_Internet-Gateway", 1340, 935, None, size=48)
d.text(1340, 900, "IGW", 14, 600, "#232F3E", bg=True)

# --- Flows
d.arrow([(348, 300), (648, 300)], ORANGE)
d.badge(580, 300, 1, ORANGE)
d.text(450, 286, "UDP/TCP 53", 13, 600, ORANGE, bg=True)
d.arrow([(712, 300), (834, 300)], BLUE)
d.badge(770, 300, 2, BLUE)
d.arrow([(1290, 460), (1628, 460)], GREEN)
d.badge(1540, 460, 3, GREEN)
d.text(1540, 412, ["allowed / alerted →", "recursive resolution"], 13, 600, GREEN, bg=True)
d.arrow([(680, 395), (680, 598)], PURPLE)
d.badge(680, 480, 4, PURPLE)
d.text(712, 486, "private names", 13, 600, PURPLE, anchor="start", bg=True)
d.arrow([(348, 325), (565, 325), (565, 935), (1316, 935)], RED, dash=True)
d.arrow([(1364, 935), (1660, 935), (1660, 906)], RED, dash=True)
d.badge(565, 760, 5, RED)
d.text(950, 925, "dig @8.8.8.8 · bypass", 13, 700, RED, bg=True)
d.arrow([(840, 570), (840, 840), (712, 840)], MUTED)
d.text(770, 828, "every decision", 13, 600, MUTED, bg=True)

# --- Notes
d.note(20, 1045, 600, 125, "Bypass (Exercise 5)", [
    "SG egress 53 only to the VPC resolver closes Do53",
    "DoH on 443 stays open → egress proxy or",
    "Network Firewall SNI rules, not DNS Firewall",
], color=RED, fill="#FDF0EE", title_color=RED)
d.note(650, 1045, 600, 125, "Three block responses", [
    "NXDOMAIN: the name doesn't exist",
    "NODATA: exists, no record of that type",
    "OVERRIDE: CNAME to a sinkhole / walled garden",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E")
d.note(1280, 1045, 600, 125, "Advanced detects behaviour, not names", [
    "no domain list, no label-length knob",
    "DNS_TUNNELING · DGA · DICTIONARY_DGA",
    "test detection with your own traffic",
], color=ORANGE, fill="#FFF6EB", title_color=ORANGE)

d.legend([
    (ORANGE, 1, "Query to the VPC resolver (UDP/TCP 53)"),
    (BLUE, 2, "Rule group evaluates: first match acts"),
    (GREEN, 3, "Allowed / alerted → recursive resolution"),
    (PURPLE, 4, "Private names → PHZ"),
    (RED, 5, "Direct query to a public resolver: no firewall, no log"),
    (MUTED, None, "Every decision → query log"),
], cols=3)

d.save(HERE / "architecture")
