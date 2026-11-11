"""NET-003 diagrams -> architecture.png (failure) and architecture_full.png (steady state + workflow)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, GREEN, RED, PINK, PURPLE, MUTED  # noqa: E402

OK_BOX = dict(color=GREEN, fill="#EEF7EE")
BAD_BOX = dict(color=RED, fill="#FDF0EE", title_color=RED)


def failure():
    """The incident: Step 3 (Off) fails and both routing controls stay ON."""
    d = Diagram(1900, 1345)

    d.node("Users", 100, 700, ["End users /", "applications"])
    d.group(200, 20, 1680, 1000, "cloud", "AWS Cloud")

    # --- Region Switch plan (top)
    d.node("AWS-Identity-Access-Management_Role", 330, 210, ["Execution role", "RegionSwitchExecutionRole"])
    d.band(470, 90, 980, 230, "ARC Region Switch plan · activate us-west-2")
    d.box(500, 150, 260, 120, "Step 1", ["Aurora Global DB", "failover → us-west-2"], **OK_BOX)
    d.box(830, 150, 260, 120, "Step 2", ["Routing control", "us-west-2 → ON"], **OK_BOX)
    d.box(1160, 150, 260, 120, "Step 3", ["Routing control", "us-east-1 → OFF"], **BAD_BOX)
    d.mark(748, 162, True)
    d.mark(1078, 162, True)
    d.mark(1408, 162, False)
    d.arrow([(760, 210), (828, 210)], MUTED)
    d.arrow([(1090, 210), (1158, 210)], MUTED)
    d.arrow([(362, 210), (468, 210)], MUTED)
    d.text(415, 196, "executes", 13, 600, MUTED, bg=True)
    d.node("Amazon-Application-Recovery-Controller", 1690, 210,
           ["ARC cluster", "5 regional endpoints", "API Region us-west-2"])
    d.arrow([(1452, 210), (1658, 210)], MUTED, dash=True)
    d.text(1555, 196, "routing control API", 13, 600, MUTED, bg=True)

    # --- Route 53
    d.node("Amazon-Route-53", 330, 700, ["Route 53", "failover records", "app.example.com"], pos="above")

    # --- Regions
    d.group(460, 420, 680, 580, "region", "us-west-2 · new primary")
    d.group(1180, 420, 680, 580, "region", "us-east-1 · ex-primary")

    d.node("Amazon-Aurora-PostgreSQL-Instance", 780, 560, ["Aurora PostgreSQL", "new writer"])
    d.mark(812, 530, True)
    d.node("Amazon-Route-53_Routing-Controls", 960, 560, ["Routing control", "us-west-2 · ON"], pos="right")
    d.mark(992, 530, True)
    d.node("Elastic-Load-Balancing_Application-Load-Balancer", 960, 880, ["ALB + app", "ECS / EC2"], pos="right")

    d.node("Amazon-Route-53_Routing-Controls", 1520, 560, ["Routing control", "us-east-1 · ON"],
           pos="right", color=RED, weight=600)
    d.mark(1552, 530, False)
    d.text(1625, 615, "should be OFF", 14, 700, RED)
    d.node("Amazon-Aurora-PostgreSQL-Instance", 1760, 880, ["Aurora (now reader)", "read-only"])
    d.node("Elastic-Load-Balancing_Application-Load-Balancer", 1520, 880, ["ALB + app", "ECS / EC2"], pos="left")

    # --- Flows
    d.arrow([(630, 270), (630, 370), (780, 370), (780, 528)], ORANGE)
    d.badge(705, 370, 1, ORANGE)
    d.arrow([(960, 270), (960, 528)], GREEN)
    d.badge(960, 370, 2, GREEN)
    d.arrow([(1290, 270), (1290, 380), (1520, 380), (1520, 528)], RED, dash=True)
    d.badge(1405, 380, 3, RED)
    d.text(1405, 412, "Off never applied", 14, 700, RED, bg=True)

    d.arrow([(128, 700), (298, 700)], PINK)
    d.arrow([(330, 730), (330, 950), (960, 950), (960, 912)], PINK)
    d.arrow([(960, 950), (1520, 950), (1520, 912)], PINK)
    d.badge(640, 950, 4, PINK)
    d.text(1240, 938, "DNS still answers us-east-1", 14, 700, PINK, bg=True)

    d.arrow([(932, 880), (780, 880), (780, 640)], GREEN)
    d.text(780, 700, "writes OK", 13, 600, GREEN, bg=True)
    d.arrow([(1548, 880), (1728, 880)], RED, dash=True)
    d.text(1638, 866, "writes fail", 13, 700, RED, bg=True)

    # --- Notes
    d.note(20, 1050, 860, 130, "Outcome: split traffic", [
        "Both routing controls ON → both health checks healthy",
        "→ DNS answers both Regions → writes reach the",
        "read-only replica in us-east-1 → errors / data-loss risk",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.note(910, 1050, 970, 130, "Likely causes (see Troubleshooting)", [
        "1. Execution role cannot set Off / lacks IAM self-inspection permissions",
        "2. A safety rule blocks the Off (gating rule misconfigured)",
        "3. ON and OFF split into separate steps → wrong region entry or order",
        "4. Step 3 evaluated before Step 2 propagated to all cluster endpoints",
    ], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", align="start")

    d.legend([
        (ORANGE, 1, "Step 1 · Aurora writer moves to us-west-2 (success)"),
        (GREEN, 2, "Step 2 · us-west-2 routing control ON (success)"),
        (RED, 3, "Step 3 · us-east-1 routing control OFF (failed)"),
        (PINK, 4, "Users still resolve to both Regions"),
    ], cols=2)
    d.save(HERE / "architecture")


def full():
    """Steady state: Route 53 failover records driven by ARC routing controls, plus the plan."""
    d = Diagram(2000, 1400)

    d.node("Users", 100, 205, ["End users /", "applications"])
    d.group(200, 20, 1250, 1210, "cloud", "AWS Cloud")

    # --- Route 53
    d.band(240, 80, 1170, 220, "Amazon Route 53 · failover records + health checks",
           color="#8C4FFF", label_color="#6A35D9")
    d.box(260, 150, 260, 110, "PRIMARY record", ["app.example.com", "→ ALB us-east-1"], color="#8C4FFF")
    d.box(580, 150, 200, 110, "Health check", ["RC primary", "healthy"], **OK_BOX)
    d.box(860, 150, 200, 110, "Health check", ["RC standby", "unhealthy"], color=MUTED, fill="#F2F3F3")
    d.box(1120, 150, 260, 110, "SECONDARY record", ["app.example.com", "→ ALB us-west-2"], color="#8C4FFF")
    d.arrow([(578, 205), (522, 205)], MUTED, dash=True)
    d.arrow([(1062, 205), (1118, 205)], MUTED, dash=True)

    # --- ARC cluster / control panel
    d.band(480, 330, 680, 270, "ARC control panel",
           color="#E07B00", label_color="#B35F00", fill_opacity=0.06)
    d.text(820, 580, "ARC cluster: 5 endpoints in 5 Regions (data plane)", 14, 600, "#B35F00")
    d.node("Amazon-Route-53_Routing-Controls", 680, 450, ["Primary-us-east-1", "State: ON"])
    d.node("Amazon-Route-53_Routing-Controls", 960, 450, ["Standby-us-west-2", "State: OFF"])
    d.arrow([(680, 422), (680, 262)], MUTED, dash=True)
    d.arrow([(960, 422), (960, 262)], MUTED, dash=True)
    d.text(680, 310, "drives", 13, 600, MUTED, bg=True)
    d.text(960, 310, "drives", 13, 600, MUTED, bg=True)

    # --- Regions
    d.group(240, 650, 560, 560, "region", "us-east-1 · steady-state primary")
    d.group(840, 650, 570, 560, "region", "us-west-2 · steady-state standby")
    for x, db in ((700, ["Aurora PostgreSQL", "writer (primary)", "+ reader"]),
                  (1300, ["Aurora PostgreSQL", "reader (standby)", "+ reader"])):
        d.node("Elastic-Load-Balancing_Application-Load-Balancer", x, 740, ["Application", "Load Balancer"], pos="left")
        d.node("Amazon-Elastic-Container-Service", x, 890, ["Application", "ECS / EC2"], pos="left")
        d.node("Amazon-Aurora-PostgreSQL-Instance", x, 1050, db, pos="left")
        d.arrow([(x, 770), (x, 860)], ORANGE if x == 700 else MUTED, dash=x != 700)
        d.arrow([(x, 920), (x, 1020)], ORANGE if x == 700 else MUTED, dash=x != 700)

    # --- Flows
    d.arrow([(128, 205), (258, 205)], ORANGE)
    d.badge(193, 205, 1, ORANGE)
    d.arrow([(390, 262), (390, 625), (700, 625), (700, 710)], ORANGE)
    d.text(390, 450, "active", 13, 700, ORANGE, bg=True)
    d.arrow([(1250, 262), (1250, 625), (1300, 625), (1300, 710)], MUTED, dash=True)
    d.text(1250, 560, "inactive", 13, 600, MUTED, bg=True)

    d.arrow([(700, 1080), (700, 1160), (1300, 1160), (1300, 1082)], GREEN, dash=True)
    d.badge(1000, 1160, 2, GREEN)
    d.text(1000, 1140, "Aurora Global Database · async replication", 14, 600, GREEN, bg=True)

    # --- Region Switch plan (right column)
    d.band(1480, 80, 500, 640, "ARC Region Switch plan · activate us-west-2",
           color=PURPLE, label_color="#6A35D9")
    d.node("AWS-Identity-Access-Management_Role", 1730, 170, ["Execution role: RegionSwitchExecutionRole"],
           size=48, label_size=14)
    d.box(1510, 260, 440, 110, "Step 1 · Failover Aurora Global DB",
          ["writer → us-west-2"], **OK_BOX)
    d.box(1510, 410, 440, 110, "Step 2 · Standby routing control ON",
          ["health check healthy → DNS answers us-west-2"], **OK_BOX)
    d.box(1510, 560, 440, 110, "Step 3 · Primary routing control OFF",
          ["health check unhealthy → DNS stops us-east-1"], **BAD_BOX)
    d.mark(1938, 272, True)
    d.mark(1938, 422, True)
    d.mark(1938, 572, False)
    d.arrow([(1730, 370), (1730, 408)], MUTED)
    d.arrow([(1730, 520), (1730, 558)], MUTED)
    d.arrow([(1508, 465), (1162, 465)], PURPLE)
    d.badge(1320, 465, 3, PURPLE)
    d.text(1320, 500, "executes steps", 13, 600, PURPLE, bg=True)

    d.note(1480, 760, 500, 160, "Expected after Step 3", [
        "Aurora writer: us-west-2",
        "RC standby ON · RC primary OFF",
        "DNS → us-west-2 only",
    ], color=GREEN, fill="#EEF7EE", title_color=GREEN, align="start")
    d.note(1480, 950, 500, 160, "Actual (bug)", [
        "Aurora writer: us-west-2",
        "RC standby ON · RC primary still ON",
        "DNS → BOTH Regions (split traffic)",
    ], color=RED, fill="#FDF0EE", title_color=RED, align="start")

    d.legend([
        (ORANGE, 1, "Steady-state traffic: DNS → PRIMARY → ALB → app → writer"),
        (GREEN, 2, "Aurora Global Database async replication"),
        (PURPLE, 3, "Region Switch plan flips DB + routing controls"),
        (MUTED, None, "Routing control drives health check · standby path inactive", "dash"),
    ], cols=2)
    d.save(HERE / "architecture_full")


failure()
full()
