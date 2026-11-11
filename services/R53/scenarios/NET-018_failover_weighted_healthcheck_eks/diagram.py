"""NET-018 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, PURPLE, TEAL, MUTED  # noqa: E402

ALB = "Elastic-Load-Balancing_Application-Load-Balancer"
d = Diagram(2000, 1330)

# --- Groups
d.group(20, 20, 1960, 840, "cloud", "AWS Cloud")
d.group(50, 80, 1900, 360, "generic", "Route 53 public hosted zone · example.com (delegated lab subdomain)")
d.group(50, 470, 1100, 360, "region", "Region us-east-1 · primary")
d.group(1180, 470, 770, 360, "region", "Region eu-west-1 · secondary")
d.group(80, 530, 1040, 270, "generic", "EKS cluster 1 · AWS LBC")
d.group(1210, 530, 710, 270, "generic", "EKS cluster 2 · AWS LBC")

# --- Hosted zone: owners and record layers
d.node("AWS-CloudFormation", 150, 190, ["IaC", "CloudFormation / Terraform"], weight=600)
d.node("Amazon-Route-53", 150, 355, ["Health checks", "hc-alb1 · 2 · 3"])
d.box(740, 120, 520, 110, "api.example.com · failover (IaC)", [
    "PRIMARY → alias use1.api.example.com · ETH=yes",
    "SECONDARY → alias euw1.api.example.com · ETH=yes"], color=BLUE, fill="#EEF5FC", size=14)
d.box(300, 300, 560, 110, "use1.api.example.com · weighted (ExternalDNS 1)", [
    "alb1 · weight 70 → ALB-1 · ETH=yes · HC=hc-alb1",
    "alb2 · weight 30 → ALB-2 · ETH=yes · HC=hc-alb2"], color=GREEN, fill="#EEF7EE", size=14)
d.box(1180, 300, 520, 110, "euw1.api.example.com · weighted (ExternalDNS 2)", [
    "alb3 · weight 100 → ALB-3 · ETH=yes · HC=hc-alb3"], color=GREEN, fill="#EEF7EE", size=14)
d.note(1730, 120, 200, 175, "Health checks", [
    "HTTPS /healthz", "target = each ALB's", "DNS name, SNI on", "certs not validated",
    "healthy if >18 %", "of checkers OK",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E")

d.arrow([(178, 175), (734, 175)], MUTED, label="owns", label_at=(460, 166))
d.arrow([(150, 272), (150, 323)], MUTED)
d.text(185, 304, "creates", 13, 600, MUTED, anchor="start")
d.arrow([(178, 355), (294, 355)], MUTED, dash=True)

d.arrow([(820, 230), (820, 294)], BLUE)
d.badge(820, 262, 1, BLUE)
d.text(880, 268, "PRIMARY", 13, 700, BLUE, bg=True)
d.arrow([(1220, 230), (1220, 294)], PURPLE, dash=True)
d.badge(1220, 262, 2, PURPLE)
d.text(1145, 268, "SECONDARY", 13, 700, PURPLE, bg=True)

# --- us-east-1 / cluster 1
d.box(100, 600, 230, 100, "ExternalDNS", ["--txt-owner-id=use1", "--domain-filter=use1.api"],
      color=ORANGE, fill="#FFF6EB", size=13)
d.node(ALB, 440, 650, ["ALB-1", "3 pods · us-east-1a/b"], weight=600)
d.node(ALB, 720, 650, ["ALB-2", "2 pods · us-east-1c"], weight=600)
d.note(830, 590, 270, 180, "Shared IngressGroup", [
    "one app with every pod", "failing readiness can make", "the whole ALB unhealthy", "for ETH (Exercise 3)",
], color="#E7157B", fill="#FDF1F7", title_color="#B0105C")

d.arrow([(440, 410), (440, 618)], GREEN)
d.badge(440, 575, 3, GREEN)
d.text(475, 560, "70", 15, 700, GREEN, bg=True)
d.arrow([(720, 410), (720, 618)], GREEN)
d.badge(720, 575, 3, GREEN)
d.text(755, 560, "30", 15, 700, GREEN, bg=True)
d.arrow([(330, 650), (360, 650), (360, 416)], ORANGE)
d.badge(360, 575, 4, ORANGE)
d.text(400, 445, "writes", 13, 600, ORANGE, bg=True)

# --- eu-west-1 / cluster 2
d.node(ALB, 1500, 650, ["ALB-3", "3 pods · eu-west-1a/b"], weight=600)
d.box(1640, 600, 250, 100, "ExternalDNS", ["--txt-owner-id=euw1", "--domain-filter=euw1.api"],
      color=ORANGE, fill="#FFF6EB", size=13)
d.arrow([(1500, 410), (1500, 618)], GREEN)
d.badge(1500, 575, 3, GREEN)
d.text(1542, 560, "100", 15, 700, GREEN, bg=True)
d.arrow([(1765, 600), (1765, 455), (1640, 455), (1640, 416)], ORANGE)
d.badge(1765, 575, 4, ORANGE)
d.text(1700, 445, "writes", 13, 600, ORANGE, bg=True)

# --- Internet: Route 53 health checkers
d.group(20, 880, 1960, 130, "internet", "Internet")
d.node("Amazon-Route-53", 110, 950, ["Route 53 health checkers", "ROUTE53_HEALTHCHECKS ranges"], pos="right")
d.line([(400, 950), (1500, 950)], TEAL, width=3.5)
for x in (440, 720, 1500):
    d.arrow([(x, 950), (x, 732)], TEAL)
d.badge(580, 950, 5, TEAL)
d.text(1110, 985, "HTTPS /healthz to each ALB by DNS name · ALB SGs must allow these ranges · "
       "can't reach private IPs (internal ALB)", 14, 600, TEAL, bg=True)

# --- Rules that drive the behavior
d.note(20, 1030, 970, 145, "Record rules", [
    "one name + type = one routing policy (else InvalidChangeBatch)",
    "ETH + health check on a record: unhealthy if either one fails",
    "nothing healthy → fail-open: failover returns the PRIMARY",
    "a disabled health check counts as healthy; use Inverted to force failure",
], color=BLUE, fill="#EEF5FC", title_color=BLUE, align="start")
d.note(1010, 1030, 970, 145, "Timing and operations", [
    "failover ≈ interval × threshold + 60 s ELB alias TTL + client caching",
    "health check: TCP ≤ 4 s, then 2xx/3xx ≤ 2 s",
    "TXT registry records (--txt-prefix) count toward the record-set quota",
    "teardown: ExternalDNS first, or it recreates the records",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E", align="start")

d.legend([
    (BLUE, 1, "Failover PRIMARY → us-east-1 weighted name"),
    (PURPLE, 2, "Failover SECONDARY → eu-west-1 weighted name"),
    (GREEN, 3, "Weighted alias → ALB (70 / 30 / 100)"),
    (ORANGE, 4, "ExternalDNS writes its regional records"),
    (TEAL, 5, "Health checkers probe each ALB"),
    (MUTED, None, "IaC owns failover records + all health checks"),
], cols=3)

d.save(HERE / "architecture")
