"""NET-013 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, MUTED  # noqa: E402

d = Diagram(1960, 1290)

# --- Groups
d.group(20, 20, 1440, 1110, "cloud", "AWS Cloud")
d.group(50, 80, 1380, 1030, "region", "Region eu-west-1")
d.group(80, 140, 560, 940, "account", "Consumer account")
d.group(110, 205, 500, 560, "vpc", "Consumer VPC · 172.17.0.0/18")
d.group(140, 265, 240, 300, "private_subnet", "Private subnet")
d.group(680, 140, 720, 940, "account", "DevOps account (rule owner)")
d.group(710, 205, 660, 560, "vpc", "DevOps VPC · 10.4.0.0/16")
d.group(730, 265, 300, 335, "private_subnet", "Subnet · AZ a")
d.group(1050, 265, 300, 335, "private_subnet", "Subnet · AZ b")
d.group(1580, 240, 360, 600, "dc", "On-premises intranet")

# --- Consumer VPC
d.node("Amazon-EC2_Instance", 260, 370, ["EC2 probe", "172.17.38.54"])
d.node("Amazon-Route-53_Resolver", 500, 370, ["Route 53 Resolver", "172.17.0.2"], pos="above", weight=600)
d.box(140, 600, 440, 140, "dig through 172.17.0.2",
      ["api.example.com → TIMEOUT", "api-dev.example.com → TIMEOUT",
       "code.example.com · git.example.cloud → OK"], color="#8C4FFF", fill="#F7F3FF")

# --- Outbound endpoint across both AZs
d.band(720, 330, 640, 120, "Outbound endpoint · OPERATIONAL")
d.node("Amazon-VPC_Elastic-Network-Interface", 820, 390, "ENI 10.4.44.80", size=44, label_size=14)
d.node("Amazon-VPC_Elastic-Network-Interface", 1140, 390, "ENI 10.4.59.249", size=44, label_size=14)
d.mark(860, 368, True)
d.mark(1180, 368, False)
d.text(890, 500, ["verified reachable", "answers direct queries"], 14, 400, GREEN)
d.text(1200, 500, ["never tested:", "route · TGW · NACL ephemeral"], 14, 400, RED)

# --- Shared resources (DevOps account)
d.node("AWS-Resource-Access-Manager", 800, 880, ["AWS RAM share", "ACTIVE (rules only)"])
d.node("Amazon-Route-53_Resolver", 1060, 880, ["Forward rules (CDK)", "api / api-dev.example.com"])
d.note(1170, 830, 210, 110, "Never verified", ["rule TargetIps", "vs. corporate DNS"],
       color=RED, fill="#FDF0EE", title_color=RED)
d.note(710, 980, 660, 80, "Shared rule needs:", [
    "consumer associates it with its own VPC · endpoints are never shared"], size=14)

# --- On-premises
d.node("Server", 1760, 400, ["Corporate DNS", "185.46.212.88 / .89"])
d.box(1610, 600, 300, 110, "Intranet records",
      ["api.example.com → 10.254.28.115", "api-dev.example.com → 10.254.21.159"], size=13)
d.pill(1490, 300, 56, 300, "AWS-Transit-Gateway", ["Route to", "on-prem"])

# --- Flows
d.arrow([(290, 370), (468, 370)], ORANGE)
d.badge(380, 370, 1, ORANGE)
d.arrow([(530, 390), (714, 390)], BLUE)
d.badge(660, 390, 2, BLUE)
d.arrow([(820, 446), (820, 560), (1690, 560), (1690, 410), (1726, 410)], GREEN)
d.badge(1440, 560, 3, GREEN)
d.arrow([(1166, 390), (1508, 390)], RED, dash=True)
d.badge(1420, 390, 4, RED)
d.text(1518, 290, "silent drop?", 14, 700, RED, bg=True)

# supporting: rule share and rule -> endpoint
d.arrow([(770, 846), (770, 792), (500, 792), (500, 772)], MUTED, dash=True)
d.text(640, 812, "associated to consumer VPC", 13, 600, MUTED, bg=True)
d.arrow([(1060, 850), (1040, 850), (1040, 456)], MUTED, dash=True)

d.note(110, 830, 500, 220, "What the timeout proves", [
    "Rule-matched names time out; every",
    "other name resolves through the same .2.",
    "So the shared rule IS matching in the",
    "consumer VPC. The fault is on the",
    "endpoint → on-prem leg (AZ b ENI first).",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")

d.legend([
    (ORANGE, 1, "Query from the consumer VPC to its .2 Resolver"),
    (BLUE, 2, "Rule match → forward via the shared outbound endpoint"),
    (GREEN, 3, "AZ a ENI → corporate DNS (works)"),
    (RED, 4, "AZ b ENI → on-prem (suspected blackhole)"),
], cols=2)

d.save(HERE / "architecture")
