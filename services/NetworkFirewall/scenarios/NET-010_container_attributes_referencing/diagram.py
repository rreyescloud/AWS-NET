"""NET-010 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, MUTED  # noqa: E402

d = Diagram(1960, 1290)

# --- Groups
d.group(20, 20, 1920, 1110, "cloud", "AWS Cloud")
d.group(50, 80, 1860, 1030, "region", "Region us-east-1")
d.group(80, 140, 1800, 950, "account", "Lab account")
d.group(110, 200, 760, 860, "vpc", "EKS VPC")
d.group(140, 260, 700, 560, "generic", "EKS cluster nfw-container-lab · 1.31")
d.node("Amazon-Elastic-Kubernetes-Service", 790, 310, size=44)
d.group(170, 360, 640, 430, "ec2", "Node group ng-1 · t3.small")
d.band(200, 430, 580, 330, "namespace payments · team=payments", color="#ED7100", label_color="#B85A00")

# --- Pods
for x, name, ip in ((300, "web-1", "192.168.49.243"), (490, "web-2", "192.168.62.171"),
                    (680, "web-3", "192.168.33.241")):
    d.node("Amazon-Elastic-Container-Service_Container-1", x, 540, [name, ip, "app=web", "tier=frontend"],
           size=48, label_size=14)

d.box(140, 860, 700, 130, "VPC CNI · AWS_VPC_K8S_CNI_EXTERNALSNAT = true", [
    "External SNAT disabled: each pod keeps its real VPC address,",
    "so the firewall sees the pod, not the node.",
], color="#ED7100", fill="#FFF5EB", title_color="#B85A00", size=14)

# --- Network Firewall side
d.node("AWS-Network-Firewall", 960, 355, ["Network", "Firewall"], weight=600, pos="above")
d.box(1030, 250, 440, 210, "Container association", [
    "eks-payments-monitor",
    "Type = EKS (immutable)",
    "ClusterArn = nfw-container-lab",
    "AttributeFilters: namespace = payments",
    "app = web",
], color="#DD344C", fill="#FFFFFF", title_color="#DD344C", size=14)
d.note(1520, 250, 330, 210, "Status = ACTIVE", [
    "ResolvedCidrCount = 3",
    "3 pods matched, 3 running.",
    "Scale the deployment",
    "and the set tracks it.",
], color=GREEN, fill="#EEF7EE", title_color=GREEN)
d.line([(1470, 355), (1520, 355)], MUTED, dash=True)

d.box(1030, 560, 820, 160, "Stateful rule group · container-ref-rules", [
    "ReferenceSets.IPSetReferences.CONTAINER_IPS.ReferenceArn = association ARN",
    "",
    'alert tcp @CONTAINER_IPS any -> any any (msg:"traffic from tracked EKS pods"; sid:1000001; rev:1;)',
], color="#DD344C", fill="#FFFFFF", title_color="#DD344C", size=13)

d.note(920, 830, 560, 190, "The one-character gotcha", [
    "@NAME = dynamic IP-set reference",
    "(resource groups, container associations)",
    "$NAME = static variable (RuleVariables.IPSets)",
    "$CONTAINER_IPS → InvalidRequestException:",
    "cannot be null or empty · RuleVariables.IPSets",
], color=RED, fill="#FDF0EE", title_color=RED)
d.note(1510, 830, 340, 190, "Constraints", [
    "EKS: external SNAT disabled",
    "Type ECS | EKS is immutable",
    "ECS: awsvpc network mode",
    "≤ 5 monitoring configs",
    "Delete rule group, then association",
    "API in boto3, not aws-cli v2",
], size=13)

# --- Flows
d.arrow([(960, 388), (960, 500), (786, 500)], ORANGE)
d.badge(880, 500, 1, ORANGE)
d.arrow([(786, 690), (1024, 690)], BLUE)
d.badge(840, 690, 2, BLUE)
d.text(950, 675, "3 pod IPs", 13, 700, BLUE, bg=True)
d.arrow([(1250, 460), (1250, 554)], GREEN)
d.badge(1250, 507, 3, GREEN)
d.text(1280, 512, "@CONTAINER_IPS", 14, 700, GREEN, "start")
d.arrow([(1200, 720), (1200, 824)], RED, dash=True)
d.badge(1200, 772, 4, RED)
d.text(1230, 777, "$CONTAINER_IPS", 14, 700, RED, "start")

d.legend([
    (ORANGE, 1, "Association watches the cluster (namespace=payments, app=web)"),
    (BLUE, 2, "Pod IPs resolve into a live CIDR set (ResolvedCidrCount = 3)"),
    (GREEN, 3, "Rule group references it as @CONTAINER_IPS (works)"),
    (RED, 4, "$CONTAINER_IPS → static-variable lookup → create fails"),
], cols=2)

d.save(HERE / "architecture")
