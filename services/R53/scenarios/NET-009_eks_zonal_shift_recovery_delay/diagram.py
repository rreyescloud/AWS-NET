"""NET-009 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, TEAL, MUTED  # noqa: E402

d = Diagram(1900, 1315)

# --- Groups
d.group(20, 20, 1860, 790, "cloud", "AWS Cloud")
d.group(50, 80, 1800, 710, "region", "Region · three Availability Zones")
d.group(380, 140, 1440, 620, "generic", "EKS cluster · Karpenter v1.12+ (no managed node groups)")
for i, (lbl, ok) in enumerate([("AZ 1 · healthy", True), ("AZ 2 · healthy", True),
                               ("AZ 3 · shifted away, then recovering", False)]):
    d.group(410 + i * 470, 420, 440, 310, "az", lbl)

# --- Left column: ARC + CloudTrail
d.node("Amazon-Application-Recovery-Controller", 210, 250, ["ARC zonal shift", "1-minute manual shift"], weight=600)
d.node("AWS-CloudTrail", 210, 470, ["CloudTrail", "StartZonalShift", "no event on expiry"])
d.note(70, 600, 285, 135, "1-min shift = edge case", [
    "expires before the chain", "finished reacting to its", "start: two transitions", "overlap",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E")

# --- Cluster control components
d.node("Amazon-Elastic-Kubernetes-Service", 560, 260, ["EKS", "control plane"], weight=600)
d.box(1020, 215, 260, 90, "Karpenter controller", ["independent reconcile loop"], color=PURPLE, fill="#F7F3FF")

# --- AZ contents
for i in range(3):
    x0 = 410 + i * 470
    d.node("Amazon-EC2_Instance", x0 + 140, 520, "node", size=48)
    d.node("Amazon-EC2_Instance", x0 + 300, 520, "node", size=48)
    if i < 2:
        d.text(x0 + 220, 640, ["Pods serving traffic", "EndpointSlices active"], 15, 400, GREEN)
for x in (1490, 1650):
    d.mark(x + 20, 500, False)
d.text(1570, 625, ["During the shift: cordoned · EndpointSlices removed",
                   "no scheduling · no Karpenter launches"], 14, 600, RED)
d.badge(1385, 700, 5, TEAL)
d.text(1410, 706, "EndpointSlices repopulated → traffic returns", 14, 700, TEAL, anchor="start")

# --- Flows
d.arrow([(240, 238), (528, 238)], ORANGE, dash=True)
d.badge(330, 238, 1, ORANGE)
d.mark(420, 238, False)
d.text(470, 212, "expiry: no push", 13, 600, ORANGE, bg=True)
d.arrow([(528, 278), (242, 278)], BLUE)
d.badge(450, 278, 2, BLUE)
d.text(330, 306, "EKS polls", 13, 600, BLUE, bg=True)

d.arrow([(588, 260), (760, 260), (760, 360), (1480, 360), (1480, 414)], GREEN)
d.badge(900, 360, 3, GREEN)
d.text(1040, 350, "remove taints · uncordon", 13, 600, GREEN, bg=True)
d.arrow([(1280, 260), (1700, 260), (1700, 414)], PURPLE)
d.badge(1400, 260, 4, PURPLE)
d.text(1580, 248, "resume provisioning", 13, 600, PURPLE, bg=True)

# --- Timeline: serial polling chain (not to scale)
d.text(40, 865, "Recovery after expiry is serial: every stage waits for its own polling loop (not to scale)",
       16, 700, "#232F3E", anchor="start")
TY = 890
stages = [
    (100, 160, ORANGE, "#FFF6EB", "1 · expiry", ["server-side"]),
    (280, 280, BLUE, "#EEF5FC", "2 · EKS discovers it", ["next poll of ARC"]),
    (580, 320, GREEN, "#EEF7EE", "3 · EKS control plane", ["taints off · uncordon"]),
    (920, 320, PURPLE, "#F7F3FF", "4 · Karpenter", ["next reconcile pass"]),
    (1260, 510, TEAL, "#E8F6F7", "5 · longer reconciliation loops", ["a missed pass waits a full cycle"]),
]
for x, w, c, fill, t, lines in stages:
    d.box(x, TY, w, 90, t, lines, color=c, fill=fill, size=14)
d.arrow([(80, 1020), (1800, 1020)], MUTED, width=2.5)
for x, lbl in [(100, "t0 · StartZonalShift"), (260, "+1 min · expiry (inferred)"), (1770, "~24 min · AZ back in service")]:
    d.line([(x, 990), (x, 1030)], MUTED, width=2)
    d.text(x, 1055, lbl, 14, 600, "#232F3E")
d.text(1050, 1055, "recovery = sum of every polling interval", 14, 700, RED)

d.note(20, 1090, 1180, 75, "Budget recovery as additive, not parallel", [
    "the worst case is not the slowest step; it is the sum of every loop, plus any loop that waits a full cycle",
], color="#E7157B", fill="#FDF1F7", title_color="#B0105C")
d.note(1220, 1090, 660, 75, "Measure it properly", [
    "≥30-min shifts or autoshift practice runs · alert on EndpointSlices",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")

d.legend([
    (ORANGE, 1, "Shift expires in ARC: no notification to EKS"),
    (BLUE, 2, "EKS discovers the expiry by polling ARC"),
    (GREEN, 3, "Control plane removes taints, uncordons nodes"),
    (PURPLE, 4, "Karpenter reconciles and resumes provisioning"),
    (TEAL, 5, "EndpointSlices repopulated, traffic returns"),
], cols=3, y=1185)

d.save(HERE / "architecture")
