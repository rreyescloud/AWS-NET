"""NET-001 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, RED, MUTED  # noqa: E402

d = Diagram(2000, 1215)

# --- Groups
d.group(20, 20, 1960, 920, "cloud", "AWS Cloud")
d.group(50, 80, 1420, 840, "region", "Region us-east-1")
d.group(80, 140, 1370, 760, "account", "Account A")
d.group(110, 200, 1070, 680, "vpc", "Lab VPC · 10.0.0.0/16 · no IGW · no NAT")
d.group(140, 260, 760, 600, "private_subnet", "Private subnet · 10.0.1.0/24 · us-east-1a")
d.group(1500, 80, 460, 840, "region", "Region us-east-2")
d.group(1530, 140, 410, 760, "account", "Account B")

# --- Subnet contents
d.node("Amazon-EC2_Instance", 240, 500, ["EC2 instance", "10.0.1.84", "no public IP"])
d.node("Amazon-VPC_Endpoints", 600, 360, ["SSM interface endpoints", "ssm · ssmmessages", "ec2messages"], pos="right")
d.node("Amazon-VPC_Endpoints", 600, 660, ["S3 interface endpoint", "--service-region us-east-2", "ENI 10.0.1.94"])
d.mark(632, 632, True)

# --- VPC-level
d.node("Amazon-VPC_Endpoints", 1040, 500, ["S3 gateway endpoint", "(free)"], pos="above")
d.box(920, 240, 240, 130, "Route table", ["10.0.0.0/16 → local", "pl-63a5400a → gateway EP", "no 0.0.0.0/0"], size=13)
d.text(240, 840, "curl to the internet → no route", 13, 700, RED)
d.mark(240, 800, False)

# --- Buckets
d.node("Amazon-Simple-Storage-Service_Bucket", 1320, 500, ["Bucket A", "us-east-1"])
d.node("Amazon-Simple-Storage-Service_Bucket", 1735, 660, ["Bucket B", "us-east-2"])
d.note(1555, 760, 370, 120, "Bucket B policy", ["Allow Account A (cross-account)", "Deny non-TLS requests"], size=13)

# --- Flows
d.arrow([(240, 472), (240, 360), (568, 360)], MUTED, dash=True)
d.text(400, 346, "Session Manager", 13, 600, MUTED, bg=True)

d.arrow([(268, 500), (1008, 500)], ORANGE)
d.badge(420, 500, 1, ORANGE)
d.arrow([(1072, 500), (1288, 500)], ORANGE)
d.text(1180, 486, "prefix-list route", 13, 600, ORANGE, bg=True)

d.arrow([(268, 520), (330, 520), (330, 660), (568, 660)], BLUE)
d.badge(330, 600, 2, BLUE)
d.text(450, 646, "s3.us-east-2 → 10.0.1.94", 13, 600, BLUE, bg=True)
d.arrow([(628, 660), (860, 660), (860, 790), (1520, 790), (1520, 660), (1700, 660)], BLUE)
d.text(1180, 776, "cross-Region PrivateLink · AWS backbone", 13, 600, BLUE, bg=True)

# --- Notes
d.note(20, 960, 760, 130, "Key insight", [
    "Gateway endpoint: same Region only (prefix-list match)",
    "Interface endpoint (standard): same-Region DNS override only",
    "Interface endpoint + --service-region: cross-Region works",
    "Private hosted zone overrides s3.us-east-2.amazonaws.com → 10.0.1.94",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", size=13, align="start")
d.note(800, 960, 560, 130, "Test results", [
    "S3 us-east-1 via gateway endpoint — OK (free)",
    "S3 us-east-2 via PrivateLink — OK",
    "SSM via interface endpoints — OK",
    "Internet — blocked (no route), as intended",
], color="#2E8B2E", fill="#EEF7EE", title_color="#2E8B2E", size=13, align="start")
d.note(1380, 960, 600, 130, "Cost at 1 TB/month", [
    "Cross-Region PrivateLink (this): ~$37 — private, simple",
    "EIGW (IPv6): ~$20 — not private",
    "NAT Gateway: ~$102 — expensive, not private",
    "pl-63a5400a holds S3 us-east-1 ranges only",
], size=13, align="start")

d.legend([
    (ORANGE, 1, "Same-Region S3 via gateway endpoint (prefix-list route)"),
    (BLUE, 2, "Cross-Region S3 via interface endpoint (PrivateLink)"),
    (MUTED, None, "Remote access through SSM interface endpoints", "dash"),
], cols=3)

d.save(HERE / "architecture")
