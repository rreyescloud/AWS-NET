"""NET-015 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, TEAL, PINK, MUTED  # noqa: E402

ENI = "Amazon-VPC_Elastic-Network-Interface"
d = Diagram(2000, 1260)

# --- Groups
d.group(20, 20, 1960, 920, "cloud", "AWS Cloud")
d.group(50, 80, 1900, 840, "account", "Lab account")
d.group(80, 140, 580, 760, "region", "Region us-west-2 (DR)")
d.group(740, 140, 1180, 760, "region", "Region us-east-1")
d.group(110, 200, 520, 680, "vpc", "VPC-B · DR · 10.1.0.0/16")
d.group(770, 200, 590, 680, "vpc", "VPC-A · Production · 10.0.0.0/16")
d.group(1440, 200, 450, 680, "dc", "Simulated on-prem · 192.168.0.0/16")

# --- VPC-B
d.node("Amazon-EC2_Instance", 230, 320, ["EC2 test", "10.1.10.x"])
d.node("Amazon-Route-53_Resolver", 500, 320, ["VPC-B Resolver", "10.1.0.2"], pos="above", weight=600)
d.band(140, 425, 460, 130, "Outbound endpoint")
d.node(ENI, 260, 490, "10.1.1.20", size=44, label_size=14)
d.node(ENI, 500, 490, "10.1.2.20", size=44, label_size=14)
d.node("Amazon-Route-53_Resolver", 230, 650, ["Rule corp.example.com", "→ VPC-A inbound"])
d.node("Amazon-Route-53-Hosted-Zone", 500, 650, ["PHZ dr.internal", "dr-app → 10.1.10.50"])
d.node("Amazon-Route-53_Resolver-Query-Logging", 370, 790, ["Query log /aws/route53/net015-dr"], size=48)

# --- VPC-A
d.band(800, 255, 530, 125, "Inbound endpoint")
d.node(ENI, 1010, 320, "10.0.1.10", size=44, label_size=14)
d.node(ENI, 1240, 320, "10.0.2.10", size=44, label_size=14)
d.node("Amazon-Route-53_Resolver", 1010, 470, ["VPC-A Resolver", "10.0.0.2"], pos="right", weight=600)
d.node("Amazon-Route-53_Resolver", 1250, 470, ["Rule corp.example.com", "→ <onprem-dns-ip>"])
d.band(800, 565, 530, 125, "Outbound endpoint")
d.node(ENI, 1010, 630, "10.0.1.20", size=44, label_size=14)
d.node(ENI, 1240, 630, "10.0.2.20", size=44, label_size=14)
d.node("Amazon-Route-53-Hosted-Zone", 920, 780, ["PHZ prod.internal", "app · db · cache"])
d.node("Amazon-Route-53_Resolver-Query-Logging", 1200, 780, ["Query log", "/aws/route53/net015-prod"], size=48)

# --- Simulated on-prem
d.node("Amazon-EC2_Instance", 1665, 630, ["EC2 + dnsmasq", "<onprem-dns-ip>"], weight=600)
d.box(1470, 280, 390, 175, "corp.example.com zone", [
    "dc1 → 192.168.1.10 · dc2 → .1.11",
    "filesvr → 192.168.10.50",
    "vpn → 192.168.20.1",
    "_ldap._tcp SRV (~136 B, UDP)",
    "big: 25 TXT (~6.6 KB, forces TCP)",
], size=14)
d.badge(1500, 790, 7, RED)
d.text(1525, 796, ["answer 192.168.10.50 returns", "hop by hop (~60 ms end to end)"], 14, 600, RED, anchor="start")

# --- Connectors
d.pill(672, 420, 56, 230, "Amazon-VPC_Peering-Connection")
d.text(700, 682, ["Cross-region", "VPC peering"], 14, 600, MUTED, bg=True)
d.pill(1372, 560, 56, 240, "Amazon-VPC_Peering-Connection")
d.text(1400, 830, ["VPC peering", "(sim. DX)"], 14, 600, MUTED, bg=True)

# --- Flows
d.arrow([(258, 320), (468, 320)], ORANGE)
d.badge(360, 320, 1, ORANGE)
d.text(360, 296, "dig filesvr.corp.example.com", 13, 600, ORANGE, bg=True)
d.arrow([(500, 350), (500, 462)], BLUE)
d.badge(500, 395, 2, BLUE)
d.arrow([(600, 490), (755, 490), (755, 320), (797, 320)], GREEN)
d.badge(630, 490, 3, GREEN)
d.arrow([(1010, 365), (1010, 438)], PURPLE)
d.badge(970, 405, 4, PURPLE)
d.arrow([(1010, 500), (1010, 602)], TEAL)
d.badge(970, 550, 5, TEAL)
d.arrow([(1330, 630), (1633, 630)], PINK)
d.badge(1530, 630, 6, PINK)

# supporting: cross-region PHZ association
d.arrow([(888, 780), (635, 780)], MUTED, dash=True)
d.text(762, 768, ["also associated", "with VPC-B"], 13, 600, MUTED, bg=True)

# --- Where is the break? (query log correlation)
NW, NY, NH = 475, 965, 135
for i, (c, fill, title, lines) in enumerate([
    (GREEN, "#EEF7EE", "In VPC-B log only", [
        "never reached VPC-A: cross-region",
        "peering route, inbound endpoint SG,",
        "or the endpoint itself"]),
    (PINK, "#FDF1F7", "In both, VPC-A TIMEOUT / SERVFAIL", [
        "arrived at VPC-A, VPC-A could not",
        "resolve: VPC-A outbound → on-prem leg",
        "(peering route, DNS SG, DNS server)"]),
    (RED, "#FDF0EE", "Small answers OK, large time out", [
        "TCP 53 blocked: size decides, not",
        "record type. Big TXT needs TCP,",
        "the 136 B SRV never does (Ex. 2)"]),
    (PURPLE, "#F7F3FF", "Most specific domain wins", [
        "same name: forwarding rule beats PHZ",
        "(Ex. 3) · no system rule protects",
        "amazonaws.com (Ex. 5)"]),
]):
    d.note(20 + i * (NW + 20), NY, NW, NH, title, lines, color=c, fill=fill, title_color=c)

d.legend([
    (ORANGE, 1, "Query to the VPC-B Resolver"),
    (BLUE, 2, "Rule match → VPC-B outbound"),
    (GREEN, 3, "Cross-region peering → VPC-A inbound"),
    (PURPLE, 4, "Handed to the VPC-A Resolver"),
    (TEAL, 5, "Rule match → VPC-A outbound"),
    (PINK, 6, "Peering (simulated DX) → on-prem DNS"),
    (RED, 7, "Response back along the chain"),
    (MUTED, None, "Cross-region PHZ association", "dash"),
], cols=4)

d.save(HERE / "architecture")
