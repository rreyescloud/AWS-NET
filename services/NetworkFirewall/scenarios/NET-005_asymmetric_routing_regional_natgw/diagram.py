"""NET-005 diagrams -> architecture_asymmetric_problem / architecture_correct_chaining (.svg/.png)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, BLUE, GREEN, RED, PURPLE, TEAL, MUTED  # noqa: E402

ROWS = [  # az, row centre, spoke EC2 IP, spoke CIDR, TGW subnet, FW subnet
    ("AZ1", 410, "10.200.1.168", "10.200.1.0/24", "10.100.10.0/24", "10.100.1.0/24"),
    ("AZ2", 700, "10.200.2.209", "10.200.2.0/24", "10.100.20.0/24", "10.100.2.0/24"),
]
XS = [270, 520, 785, 1105, 1395]  # EC2, TGW, TGW attachment, FW endpoint, RNAT ENI


def segments(d, y, color, reverse=False, dash=False, upto=len(XS) - 1):
    """Arrows between consecutive nodes of a row (outbound left→right, return right→left)."""
    for a, b in zip(XS[:upto], XS[1:upto + 1]):
        if reverse:
            d.arrow([(b - 32, y), (a + 32, y)], color, dash=dash)
        else:
            d.arrow([(a + 32, y), (b - 32, y)], color, dash=dash)


def base(fixed: bool) -> Diagram:
    d = Diagram(1960, 1310)
    d.group(20, 20, 1700, 1140, "cloud", "AWS Cloud")
    d.group(50, 80, 1640, 1060, "region", "Region us-west-2")
    d.group(80, 140, 1580, 980, "account", "Lab account")
    d.group(110, 200, 340, 650, "vpc", "Spoke VPC · 10.200.0.0/16")
    d.group(610, 200, 1020, 650, "vpc", "Inspection VPC · 10.100.0.0/16")
    d.group(1740, 420, 200, 270, "internet", "Internet")

    d.pill(492, 300, 56, 500, "AWS-Transit-Gateway", ["Transit", "Gateway"])
    d.band(1300, 270, 190, 570, "Regional NAT GW", color="#7AA116", label_color="#5E7F0F")

    for az, cy, ip, spoke, tgw, fw in ROWS:
        d.group(140, cy - 130, 280, 250, "private_subnet", f"Spoke {az}")
        d.node("Amazon-EC2_Instance", 270, cy, [ip, spoke], label_size=14)
        d.group(650, cy - 130, 270, 250, "private_subnet", f"TGW subnet {az}")
        d.node("AWS-Transit-Gateway_Attachment", 785, cy, ["TGW attachment", tgw], size=48, label_size=14)
        d.group(960, cy - 130, 300, 250, "private_subnet", f"Firewall subnet {az}")
        d.node("AWS-Network-Firewall_Endpoints", 1105, cy, [f"FW endpoint {az}", fw], label_size=14)
        n = az[-1]
        d.node("Amazon-VPC_NAT-Gateway", 1395, cy, [f"RNAT {az}", f"EIP {n}"], size=48, label_size=14)

    d.node("Amazon-VPC_Internet-Gateway", 1630, 555, "IGW", size=48, pos="above")
    d.node("Internet", 1840, 555, "Internet")
    d.arrow([(1490, 555), (1602, 555)], MUTED, both=True, width=3)
    d.arrow([(1658, 555), (1806, 555)], MUTED, both=True, width=3)
    d.text(1545, 600, ["NAT /", "reverse NAT"], 13, 600, MUTED)

    # outbound, both AZs
    for (az, cy, *_), color, n in zip(ROWS, (GREEN, TEAL), (1, 2)):
        segments(d, cy - 12, color)
        d.badge(395, cy - 12, n, color)
    d.line([(1395, 840), (1395, 880)], MUTED, dash=True)
    return d


def problem():
    d = base(False)
    for az, cy, *_ in ROWS:
        d.arrow([(1365, cy + 14), (1282, cy + 14)], RED, dash=True)
        d.mark(1266, cy + 14, False)
    d.badge(1395, 555, 3, RED)
    d.text(1395, 598, ["no spoke", "route"], 13, 700, RED)

    d.note(110, 890, 470, 170, "Blackholed at the RNAT", [
        "Reverse NAT: EIP → 10.200.1.168",
        "RNAT RT has no route for 10.200.0.0/16",
        "→ dropped, never reaches the firewall",
        "Aggregated 10.0.0.0/8 → fw-az1 instead:",
        "AZ2 returns hit FW AZ1 → no flow → drop",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.note(610, 890, 470, 170, "Lab result (before fix)", [
        "curl https://google.com → HTTP 000 (10 s)",
        "ping 8.8.8.8 → 100% packet loss",
        "NFW flow logs empty (return never arrives)",
        "Outbound works: DNS resolves, SYN sent",
    ])
    d.box(1110, 880, 530, 190, "RNAT route table", [
        "10.100.10.0/24 → vpce-fw-az1",
        "10.100.20.0/24 → vpce-fw-az2",
        "10.100.0.0/16  → local",
        "0.0.0.0/0      → IGW",
        "✗ no route for 10.200.0.0/16 (spoke)",
    ], color=RED, fill="#FFFFFF", title_color=RED, size=14)

    d.legend([
        (GREEN, 1, "Outbound AZ1: spoke → TGW → FW AZ1 → RNAT"),
        (TEAL, 2, "Outbound AZ2: spoke → TGW → FW AZ2 → RNAT"),
        (RED, 3, "Return: reverse NAT, no spoke route → blackholed"),
        (MUTED, None, "RNAT ↔ IGW ↔ Internet (NAT / reverse NAT)"),
    ], cols=2)
    d.save(HERE / "architecture_asymmetric_problem")


def solution():
    d = base(True)
    for (az, cy, *_), color, n in zip(ROWS, (BLUE, PURPLE), (3, 4)):
        segments(d, cy + 14, color, reverse=True, dash=True)
        d.badge(1250, cy + 14, n, color)
        d.mark(300, cy - 30, True)

    d.note(110, 890, 470, 170, "Lab result (after fix)", [
        "HTTP 200 from both AZs (0.10 s / 0.09 s)",
        "EC2 AZ1 → EIP 1 · EC2 AZ2 → EIP 2",
        "Distinct public IPs per AZ",
        "= RNAT zonal affinity",
    ], color=GREEN, fill="#EEF7EE", title_color=GREEN)
    d.note(610, 890, 470, 170, "Key insight", [
        "Per-AZ spoke CIDR routes send each",
        "return to the FW endpoint in its own AZ.",
        "NFW flow state is per endpoint: inspect",
        "before NAT (ENI → FW → RNAT → IGW).",
    ])
    d.box(1110, 880, 530, 190, "RNAT route table (fixed)", [
        "10.200.1.0/24  → vpce-fw-az1  (spoke AZ1)",
        "10.200.2.0/24  → vpce-fw-az2  (spoke AZ2)",
        "10.100.10.0/24 → vpce-fw-az1  (TGW AZ1)",
        "10.100.20.0/24 → vpce-fw-az2  (TGW AZ2)",
        "10.100.0.0/16 → local · 0.0.0.0/0 → IGW",
    ], color=GREEN, fill="#FFFFFF", title_color=GREEN, size=14)

    d.legend([
        (GREEN, 1, "Outbound AZ1: spoke → TGW → FW AZ1 → RNAT"),
        (TEAL, 2, "Outbound AZ2: spoke → TGW → FW AZ2 → RNAT"),
        (BLUE, 3, "Return AZ1: 10.200.1.0/24 → FW AZ1 → TGW → spoke"),
        (PURPLE, 4, "Return AZ2: 10.200.2.0/24 → FW AZ2 → TGW → spoke"),
        (MUTED, None, "RNAT ↔ IGW ↔ Internet (NAT / reverse NAT)"),
    ], cols=3)
    d.save(HERE / "architecture_correct_chaining")


if __name__ == "__main__":
    problem()
    solution()
