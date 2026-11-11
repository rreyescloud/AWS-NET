"""NET-008 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, TEAL, MUTED  # noqa: E402

d = Diagram(1900, 1110)
Y = 440  # main row

# --- Groups
d.group(20, 150, 225, 490, "generic", "Client outside AWS")
d.group(480, 20, 1180, 660, "cloud", "AWS Cloud")
d.group(510, 80, 1130, 580, "account", "Lab account (control plane us-east-2)")
d.group(540, 150, 1070, 490, "generic", "Route 53 Global Resolver (global, anycast)")
d.group(1700, 250, 180, 330, "dc", "On-prem")

# --- Client and egress path
d.node("Client", 125, Y, ["Laptop · on-prem host", "another cloud"])
d.pill(360, 280, 64, 320, "Firewall", ["Network control", "firewall · proxy"])

# --- Global Resolver pipeline
d.node("Amazon-Route-53_Resolver", 640, Y, ["Global Resolver", "anycast IPs", "+ DNS name"], weight=600)
d.node("AWS-Identity-Access-Management_Permissions", 860, Y, ["Access Sources", "source-IP allowlist", "per protocol"])
d.node("Amazon-Route-53", 1080, Y, ["DNS View", "PHZ associations", "same-account only"])
d.node("Amazon-Route-53-Hosted-Zone", 1320, 330, ["Private hosted zone", "lab.internal", "authoritative for suffix"],
       pos="right")
d.node("Globe", 1320, 560, ["Public DNS", "every other suffix"], pos="right")
d.node("Server", 1790, Y, ["Your", "resolvers"])

# --- Flows
d.arrow([(153, Y - 15), (326, Y - 15)], RED, dash=True)
d.badge(210, Y - 15, 1, RED)
d.mark(340, Y - 15, False)
d.text(392, 262, "TIMEOUT", 14, 700, RED, bg=True)
d.text(275, Y - 58, ["Do53", "UDP/TCP 53"], 13, 600, RED, bg=True)

d.arrow([(153, Y + 15), (610, Y + 15)], GREEN)
d.badge(578, Y + 15, 2, GREEN)
d.text(280, Y + 42, ["DoH / DoT", "443 / 853"], 13, 600, GREEN, bg=True)
d.arrow([(668, Y), (830, Y)], GREEN)
d.arrow([(890, Y), (1050, Y)], GREEN)
d.mark(970, Y - 22, True)

d.arrow([(860, 540), (860, 590)], ORANGE)
d.badge(820, 565, 3, ORANGE)
d.text(860, 618, "not listed → REFUSED (RCODE 5)", 14, 700, ORANGE, bg=True)

d.arrow([(1108, Y - 15), (1180, Y - 15), (1180, 330), (1288, 330)], BLUE)
d.badge(1180, 380, 4, BLUE)
d.arrow([(1108, Y + 15), (1220, Y + 15), (1220, 560), (1288, 560)], TEAL)
d.badge(1220, 510, 5, TEAL)

d.arrow([(1610, Y), (1756, Y)], MUTED, dash=True)
d.mark(1680, Y, False)
d.text(1680, Y + 36, "no forwarding", 13, 600, MUTED, bg=True)

# --- Triage by response code
NW, NH, NY = 450, 120, 715
for i, (c, fill, title, lines) in enumerate([
    (RED, "#FDF0EE", "TIMEOUT · never arrived", [
        "path or protocol: Do53 filtered on egress",
        "nothing to change on the resolver",
        "switch to DoH and retest"]),
    (PURPLE, "#F7F3FF", "TLS failure · wrong target", [
        "DoH / DoT sent to a raw anycast IP",
        "there is no hostname to validate",
        "use the resolver DNS name"]),
    (ORANGE, "#FFF6EB", "REFUSED · arrived, rejected", [
        "your real egress IP is not an Access",
        "Source for that protocol",
        "authorisation is per protocol"]),
    (BLUE, "#EEF5FC", "NXDOMAIN · arrived, answered", [
        "a PHZ is authoritative for the suffix",
        "and the record is absent",
        "no fallback to public DNS"]),
]):
    d.note(20 + i * (NW + 20), NY, NW, NH, title, lines, color=c, fill=fill, title_color=c)

d.note(20, 855, 920, 95, "The trap that costs the most time", [
    "The resolver sees your real internet egress IP. Some \"what is my IP\" endpoints report",
    "a different one: allowlist that and every query is REFUSED. Check more than one.",
], color="#E7157B", fill="#FDF1F7", title_color="#B0105C")
d.note(960, 855, 920, 95, "Half of the real cases are billing, not resolution", [
    "The endpoint bills while it exists, not per query: a forgotten proof of concept",
    "is a four-figure bill with zero DNS symptoms. Answer it by listing resolvers.",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E")

d.legend([
    (RED, 1, "Do53 to an anycast IP: dropped on egress"),
    (GREEN, 2, "DoH / DoT to the resolver DNS name: passes"),
    (ORANGE, 3, "Source IP not an Access Source → REFUSED"),
    (BLUE, 4, "PHZ suffix → answer or NXDOMAIN"),
    (TEAL, 5, "Any other suffix → public DNS"),
    (MUTED, None, "No outbound forwarding to your resolvers", "dash"),
], cols=3)

d.save(HERE / "architecture")
