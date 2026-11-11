"""NET-011 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, GREEN, RED, MUTED, INK  # noqa: E402

d = Diagram(2000, 1330)

# --- Groups
d.group(20, 20, 1680, 700, "cloud", "AWS Cloud")
d.group(50, 80, 1620, 620, "region", "Region · centralized egress inspection")
d.group(80, 140, 430, 540, "account", "Spoke account")
d.group(110, 205, 370, 360, "vpc", "Spoke VPC · 10.212.0.0/16")
d.group(140, 265, 310, 260, "private_subnet", "Workload · 10.212.2.0/24")
d.group(540, 140, 1100, 540, "account", "Network account (inspection)")
d.group(700, 205, 910, 360, "vpc", "Inspection VPC · 10.208.0.0/16")
d.group(720, 265, 270, 260, "private_subnet", "TGW · 10.208.11.0/24")
d.group(1010, 265, 280, 260, "private_subnet", "Firewall · 10.208.1.0/24")
d.group(1310, 265, 280, 260, "public_subnet", "Public · 10.208.21.0/24")
d.text(1155, 590, "same three subnets in AZ b", 14, 400, MUTED, italic=True)
d.group(1730, 250, 250, 280, "internet", "Internet")

# --- Nodes
d.node("Amazon-EC2_Instance", 295, 400, ["Test host", "10.212.2.x"])
d.node("AWS-Transit-Gateway", 615, 400, ["Transit Gateway", "appliance mode"])
d.node("AWS-Transit-Gateway_Attachment", 855, 400, ["Inspection", "attachment"])
d.node("AWS-Network-Firewall_Endpoints", 1150, 400, ["Firewall endpoint", "STRICT_ORDER policy"], weight=600)
d.node("Amazon-VPC_NAT-Gateway", 1450, 400, "NAT gateway")
d.node("Amazon-VPC_Internet-Gateway", 1610, 620, "IGW", size=48, pos="above")
d.node("Globe", 1855, 390, ["iam.amazonaws.com", "sts.amazonaws.com", ":443"])
d.node("Amazon-CloudWatch_Logs", 900, 630, ["ALERT + FLOW logs"], size=48, pos="right")

# --- Flow 1: SYN, killed at the firewall
d.arrow([(325, 385), (585, 385)], ORANGE)
d.arrow([(645, 385), (825, 385)], ORANGE)
d.arrow([(885, 385), (1116, 385)], ORANGE)
d.badge(385, 385, 1, ORANGE)
d.mark(1188, 370, False)
d.text(1150, 350, "SYN dropped · sid 30000", 14, 700, RED, bg=True)

# --- Flow 2: after the fix, handshake + ClientHello reach the internet
d.arrow([(325, 415), (585, 415)], GREEN)
d.arrow([(645, 415), (825, 415)], GREEN)
d.arrow([(885, 415), (1118, 415)], GREEN)
d.arrow([(1182, 415), (1418, 415)], GREEN)
d.arrow([(1482, 415), (1540, 415), (1540, 620), (1580, 620)], GREEN)
d.arrow([(1640, 620), (1712, 620), (1712, 390), (1820, 390)], GREEN)
d.badge(525, 415, 2, GREEN)
d.badge(1300, 415, 2, GREEN)

# --- Supporting: logs
d.arrow([(1150, 490), (1150, 545), (900, 545), (900, 600)], MUTED, dash=True)

# --- Rule evaluation for the first packet
d.band(20, 750, 1200, 420, "Strict-order evaluation of packet 1 — the TCP SYN (no TLS state yet)",
       color="#ED7100", label_color="#B85A00", fill_opacity=0.05)
rows = [
    ("Priority 10 · net011-010Probes (alert only)",
     "alert ip 10.212.0.0/16 any -> any any  (sid 10003) · tls.sni probes 10001/10002",
     "sid 10003 fires · continues", GREEN),
    ("Priority 99 · allowlist",
     'pass tls $HOME_NET any -> $EXTERNAL_NET 443 (tls.sni; dotprefix; ".amazonaws.com")',
     "no match · tls.sni absent", RED),
    ("Priority 100 · AttackInfrastructureStrictOrder (AWS managed)",
     "threat signatures",
     "no match", MUTED),
    ("Priority 999 · net011-999DefaultDeny",
     "drop ip any any -> any any (flow:to_server; sid:30000)",
     "MATCHES SYN → DROP", RED),
]
for i, (title, rule, verdict, vc) in enumerate(rows):
    y = 800 + i * 88
    hot = i == 3
    d.box(110, y, 1080, 72, title, [rule], align="start", size=14,
          color=RED if hot else "#7D8998", fill="#FDF0EE" if hot else "#FFFFFF")
    d.text(1170, y + 44, verdict, 15, 700, vc, "end")
d.arrow([(65, 800), (65, 1100), (104, 1100)], ORANGE)
d.badge(65, 850, 1, ORANGE)

# --- Notes
d.note(1250, 750, 730, 130, "What the ALERT log shows", [
    "drop sid 30000 · TCP/443 · no tls object, no app_proto, no SNI",
    "→ dropped at connection setup: diagnose by what is ABSENT",
], color=RED, fill="#FDF0EE", title_color=RED)
d.note(1250, 895, 730, 130, "Fix adopted (Option 1)", [
    "delete the 999 catch-all · default actions aws:drop_established +",
    "aws:alert_established → SYN passes, SNI exists, sid 20000 matches",
], color=GREEN, fill="#EEF7EE", title_color="#1F6B1F")
d.note(1250, 1040, 730, 130, "Also found (latent)", [
    "round-1 rule: startswith + endswith = exact match, never true",
    "pass at 99 bypasses the managed threat group at 100",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")

d.legend([
    (ORANGE, 1, "Broken: TCP SYN reaches the firewall, priority 999 drop ip kills it"),
    (GREEN, 2, "Fixed: handshake completes, ClientHello SNI matches pass sid 20000"),
    (MUTED, None, "ALERT / FLOW logs to CloudWatch Logs", "dash"),
], cols=2)

d.save(HERE / "architecture")
