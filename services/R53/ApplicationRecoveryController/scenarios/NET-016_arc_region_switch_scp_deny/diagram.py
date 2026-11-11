"""NET-016 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, GREEN, RED, TEAL, MUTED  # noqa: E402

d = Diagram(2000, 1265)

# --- Groups
d.node("User", 110, 260, ["Engineer on", "corporate network", "203.0.113.x"])
d.group(220, 20, 1760, 920, "cloud", "AWS Cloud")
d.group(250, 80, 1700, 840, "account", "Workload account (SCP attached via AWS Organizations)")
d.group(1560, 140, 360, 180, "region", "us-east-1")
d.group(1560, 340, 360, 180, "region", "us-west-2")

# --- SCP column
d.band(760, 140, 320, 690, "SCP · network perimeter", color="#E7157B", label_color="#B0105C",
       fill_opacity=0.05)
d.node("AWS-Organizations", 920, 390, ["DenyRecoveryCluster-", "UnlessKnownNetwork"], label_size=14)
d.box(775, 500, 290, 200, "Deny route53-recovery-cluster:*", [
    "when ALL are true:",
    "SourceVpce not vpce-*",
    "SourceIp not in allowlist",
    "ViaAWSService = false",
    "PrincipalIsAWSService = false",
], color="#E7157B", fill="#FFFFFF", size=13)
d.text(920, 760, ["lab: simulated with an IAM", "permission boundary"], 13, 400, MUTED, italic=True)

# --- Path 1: engineer (allowed)
d.arrow([(140, 260), (1168, 260)], GREEN)
d.badge(450, 260, 1, GREEN)
d.mark(920, 260, True)
d.text(920, 300, "SourceIp in allowlist → not denied", 13, 600, GREEN, bg=True)

d.node("Amazon-Route-53_Routing-Controls", 1200, 260, ["ARC routing controls", "primary OFF · secondary ON"])
d.node("Amazon-Route-53", 1420, 260, ["Route 53 failover", "api.lab-fraud.example.com"], pos="above")
d.arrow([(1230, 260), (1390, 260)], MUTED, dash=True)
d.text(1310, 246, "health checks", 13, 600, MUTED, bg=True)
d.node("Amazon-API-Gateway", 1650, 240, ["API Gateway", "mock /verify"], size=48, pos="right")
d.node("Amazon-API-Gateway", 1650, 440, ["API Gateway", "mock /verify"], size=48, pos="right")
d.arrow([(1450, 240), (1624, 240)], MUTED, dash=True)
d.text(1595, 228, "OFF", 13, 700, MUTED, bg=True)
d.arrow([(1450, 275), (1520, 275), (1520, 440), (1624, 440)], GREEN)
d.text(1520, 330, "traffic", 13, 700, GREEN, bg=True)

# --- Pre-validation (teal)
d.node("AWS-Identity-Access-Management_Permissions", 320, 420,
       ["iam:SimulatePrincipalPolicy", "→ ALLOWED (SCPs not evaluated)"], pos="right", color=TEAL, weight=600)
d.arrow([(320, 572), (320, 452)], TEAL)
d.badge(320, 512, 3, TEAL)

# --- Path 2: Region Switch (denied)
d.node("Amazon-Application-Recovery-Controller", 320, 600, ["ARC Region Switch", "arc-region-switch", ".amazonaws.com"])
d.node("AWS-Identity-Access-Management_Role", 560, 600, ["Execution role", "session"])
d.arrow([(352, 600), (528, 600)], RED)
d.text(440, 586, "sts:AssumeRole", 13, 600, RED, bg=True)
d.arrow([(592, 600), (773, 600)], RED)
d.badge(680, 600, 2, RED)
d.text(680, 640, "UpdateRouting-", 13, 600, RED)
d.text(680, 657, "ControlStates", 13, 600, RED)
d.mark(1065, 600, False)
d.arrow([(1080, 600), (1118, 600)], RED)
d.box(1120, 545, 380, 110, "AccessDeniedException", [
    "explicit deny in a service control policy",
    "plan fails at the routing-control step",
], color=RED, fill="#FDF0EE", title_color=RED, size=13)

d.note(260, 710, 480, 170, "Assumed-role session context", [
    "aws:SourceIp = AWS service network",
    "aws:SourceVpce = null",
    "aws:ViaAWSService = false",
    "aws:PrincipalIsAWSService = false",
    "aws:CalledVia = not populated",
], color=RED, fill="#FDF0EE", title_color=RED, size=13, align="start")

d.note(1120, 690, 800, 170, "The fix — exempt the execution role by ARN", [
    "Add to the deny statement's Condition block:",
    '"ArnNotLike": { "aws:PrincipalArn":',
    '"arn:aws:iam::*:role/*-region-switch-execution" }',
    "The role no longer matches → not all conditions true",
    "→ deny does not apply → the step succeeds",
], color=GREEN, fill="#EEF7EE", title_color=GREEN, size=13, align="start")

# --- Notes below the cloud
d.note(20, 960, 620, 140, "What does NOT work", [
    "aws:CalledVia — not populated (AssumeRole, not FAS)",
    "aws:ViaAWSService — already false for these calls",
    "aws:PrincipalIsAWSService — false, principal is the role",
    "Adding the role to the IP list — a role is not an IP",
], color=RED, fill="#FDF0EE", title_color=RED, size=13, align="start")
d.note(660, 960, 1320, 140, "Two ways AWS services act on your behalf", [
    "Credential forwarding (CloudFormation, API Gateway, DynamoDB Streams): you call A, A calls B with YOUR credentials",
    "→ aws:ViaAWSService = true, aws:CalledVia set; network conditions of the original caller apply",
    "Role assumption (ARC Region Switch, CodePipeline, ECS task role): the service assumes a role you configured",
    "→ own session, aws:ViaAWSService = false, aws:CalledVia empty; the call comes FROM the role",
    "A network-perimeter SCP must exempt service-assumed roles, or it blocks every such service.",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", size=13, align="start")

d.legend([
    (GREEN, 1, "Engineer from a known network → SCP allows → failover"),
    (RED, 2, "Region Switch assumes the role → call outside perimeter → SCP deny"),
    (TEAL, 3, "Pre-validation passes — SimulatePrincipalPolicy ignores SCPs"),
    (MUTED, None, "Routing control → health check → DNS failover", "dash"),
], cols=2)

d.save(HERE / "architecture")
