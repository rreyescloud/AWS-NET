"""NET-025 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, MUTED  # noqa: E402

d = Diagram(1900, 1240)

# --- Groups
d.group(240, 20, 1640, 920, "cloud", "AWS Cloud")
d.group(270, 80, 1580, 840, "region", "Region <region>")
d.group(300, 140, 1520, 760, "account", "Account <ACCOUNT_A> (production)")
d.group(770, 210, 1020, 650, "vpc", "EKS VPC")
d.group(800, 270, 260, 560, "public_subnet", "Public subnets")
d.group(1100, 270, 660, 560, "generic", "Amazon EKS cluster")

# --- Clients (internet)
d.node("Users", 120, 420, ["Clients", "browsers · API clients"])

# --- Web ACL
d.band(330, 210, 400, 400, None, color="#DD344C", label_color="#B0213A")
d.node("AWS-WAF", 530, 270, ["web ACL <webacl-prod>", "REGIONAL · shared by both ALBs"], weight=600)
d.box(350, 360, 360, 230, "Rules by priority", [
    "1  IP sets allow / block",
    "2  IP reputation",
    "3  Rate-based rules",
    "4  KnownBadInputs · Core · SQLi · Linux",
    "5  Bot Control / ATP (scope-down)",
    "    default action",
], color="#DD344C", fill="#FFF5F6", align="start", size=14)

# --- Logs
d.node("Amazon-CloudWatch_Logs", 420, 770, ["WAF logs", "CloudWatch Logs"], size=48, label_size=14)
d.node("Amazon-Simple-Storage-Service", 530, 770, ["S3"], size=48, label_size=14)
d.node("Amazon-Data-Firehose", 640, 770, ["Firehose"], size=48, label_size=14)

# --- ALBs
d.node("Elastic-Load-Balancing_Application-Load-Balancer", 930, 400,
       ["<alb-istio-ingress>", "body inspected ≤ 8 KB"], label_size=14, pos="above")
d.node("Elastic-Load-Balancing_Application-Load-Balancer", 930, 640,
       ["<alb-k8s-ingress>", "body inspected ≤ 8 KB"], label_size=14)

# --- EKS
d.node("AWS-App-Mesh_Virtual-Gateway", 1250, 400, ["Istio ingress", "gateway"], label_size=14)
d.node("Amazon-Elastic-Container-Service_Service", 1250, 640, ["Kubernetes", "ingress"], label_size=14)
d.node("Amazon-Elastic-Container-Service_Container-1", 1520, 400, ["services"], label_size=14)
d.node("Amazon-Elastic-Container-Service_Container-2", 1520, 640, ["services"], label_size=14)
d.node("Amazon-Elastic-Kubernetes-Service", 1580, 760, None, size=40)
d.text(1420, 755, ["AWS Load Balancer Controller", "wafv2-acl-arn annotation"], 14, 600, MUTED)

# --- Flows
d.arrow([(160, 420), (324, 420)], ORANGE)
d.badge(240, 420, 1, ORANGE)
d.text(240, 395, "HTTPS", 14, 600, ORANGE, bg=True)
d.arrow([(736, 400), (896, 400)], BLUE)
d.badge(770, 400, 2, BLUE)
d.arrow([(964, 400), (1216, 400)], BLUE)
d.arrow([(1284, 400), (1486, 400)], BLUE)
d.arrow([(736, 640), (896, 640)], GREEN)
d.badge(770, 640, 3, GREEN)
d.arrow([(964, 640), (1216, 640)], GREEN)
d.arrow([(1284, 640), (1486, 640)], GREEN)

# supporting: logs and controller ownership
d.arrow([(530, 616), (530, 700)], MUTED)
d.text(620, 660, "every request + labels", 13, 600, MUTED, bg=True)
d.text(530, 722, "Log destination (one of)", 14, 600, MUTED)
d.arrow([(1290, 760), (1150, 760), (1150, 520), (930, 520), (930, 436)], MUTED, dash=True)
d.arrow([(930, 520), (930, 604)], MUTED, dash=True)
d.text(1045, 545, "owns ALBs + WAF association", 13, 600, MUTED, bg=True)

# --- Findings
d.note(20, 965, 450, 100, "One shared web ACL", [
    "every exclusion applies to both ALBs;",
    "split per ALB if traffic profiles differ"])
d.note(490, 965, 450, 100, "ALB body limit: first 8 KB", [
    "SizeRestrictions_BODY blocks larger bodies:",
    "protection and top false positive"], color=RED, fill="#FDF0EE", title_color=RED)
d.note(960, 965, 450, 100, "Rate-based key", [
    "behind a proxy/CDN: FORWARDED_IP;",
    "shared NAT: key on token / API-key header"], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")
d.note(1430, 965, 450, 100, "Tuning loop", [
    "Count → measure in logs →",
    "label-based exclusion → Block"], color=GREEN, fill="#F0F8F0", title_color=GREEN)

d.legend([
    (ORANGE, 1, "Clients → web ACL evaluation (HTTPS)"),
    (BLUE, 2, "Allowed → istio ALB → Istio gateway"),
    (GREEN, 3, "Allowed → k8s ALB → Kubernetes ingress"),
    (MUTED, None, "Log record per request"),
    (MUTED, None, "Controller manages ALBs + association", "dash"),
], cols=3)

d.save(HERE / "architecture")
