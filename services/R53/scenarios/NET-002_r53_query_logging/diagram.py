"""NET-002 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, MUTED  # noqa: E402

d = Diagram(1900, 1290)

# --- Groups: Resolver query logging + delivery path
d.group(20, 20, 1860, 840, "cloud", "AWS Cloud")
d.group(50, 80, 1800, 760, "region", "Region (config and destination in the same Region)")
d.group(80, 140, 1000, 680, "account", "Source account")
d.group(110, 205, 700, 590, "vpc", "VPC")
d.group(140, 265, 300, 250, "private_subnet", "Private subnet")
d.group(140, 545, 300, 220, "private_subnet", "Private subnet")
d.group(1110, 140, 720, 680, "account", "Logging account")

# --- VPC
d.node("Amazon-EC2_Instance", 290, 360, ["EC2 · pods · Lambda"])
d.node("Amazon-Route-53_Resolver", 640, 360, ["VPC Resolver", ".2 address"], weight=600)
d.node("Server", 290, 640, ["Custom DNS server (AD)", "set via DHCP options"])
d.mark(250, 612, False)
d.note(470, 590, 320, 145, "Fix the bypass", [
    "conditional forwarding from",
    "the custom server back to .2,",
    "or Resolver endpoints for",
    "the zones you care about",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")

# --- Query log config, SLR (source account)
d.node("Amazon-Route-53_Resolver-Query-Logging", 945, 360, ["Query log config", "+ VPC association"])
d.node("AWS-Identity-Access-Management_Role", 945, 630, ["AWSServiceRoleForLogDelivery", "delivery.logs.amazonaws.com"],
       weight=600)
d.text(945, 750, ["writes the logs, not", "your IAM role or user"], 14, 700, RED)

# --- Destinations (logging account)
d.note(1150, 210, 640, 125, "Hard limits", [
    "destination in the same Region · no native cross-Region",
    "cross-account: CLI / API only, not the console",
    "nothing is retroactive",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E")
d.band(1150, 380, 640, 200, "Destination (one of)")
d.node("Amazon-Simple-Storage-Service_Bucket", 1270, 470, ["S3 bucket"])
d.node("Amazon-CloudWatch_Logs", 1470, 470, ["CloudWatch", "log group"])
d.node("Amazon-Data-Firehose", 1670, 470, ["Firehose", "stream"])
d.note(1150, 670, 640, 105, "Needs a resource policy", [
    "Principal: delivery.logs.amazonaws.com",
    "Condition: aws:SourceAccount + aws:SourceArn",
], color=RED, fill="#FDF0EE", title_color=RED)

# --- Flows
d.arrow([(318, 360), (608, 360)], ORANGE)
d.badge(460, 360, 1, ORANGE)
d.arrow([(318, 382), (410, 382), (410, 640), (324, 640)], RED, dash=True)
d.badge(410, 530, 2, RED)
d.text(438, 536, "NOT logged", 14, 700, RED, anchor="start")
d.arrow([(672, 360), (913, 360)], MUTED, label="every query via .2", label_at=(790, 340))
d.arrow([(945, 440), (945, 596)], GREEN, label="vended logs v2", label_at=(1010, 530))
d.badge(945, 480, 4, GREEN)
d.arrow([(978, 630), (1470, 630), (1470, 586)], GREEN)
d.text(1220, 618, "writes", 14, 600, GREEN, bg=True)

# --- Public hosted zone query logging (separate plane)
d.group(20, 890, 330, 220, "internet", "Internet")
d.node("Globe", 185, 990, ["Resolvers on", "the internet"])
d.group(390, 890, 1040, 220, "cloud", "AWS Cloud · Route 53 (global)")
d.node("Amazon-Route-53-Hosted-Zone", 560, 990, ["Public hosted zone", "example.com"])
d.node("Amazon-CloudWatch_Logs", 880, 990, ["CloudWatch Logs", "(only destination)"])
d.note(1050, 935, 350, 125, "Zone query logging", [
    "only names in this zone",
    "no private DNS · not what",
    "your instances look up",
], color="#7D8998", fill="#F7F9FC", title_color="#232F3E")
d.arrow([(213, 975), (528, 975)], BLUE)
d.badge(370, 975, 3, BLUE)
d.arrow([(592, 975), (848, 975)], MUTED)

d.note(1460, 905, 420, 175, "Failure modes", [
    "ACCESS_DENIED → dest. resource policy",
    "CreateLogDelivery missing → no SLR",
    "association FAILED → read Error field",
    "empty log group → no queries or bypass",
    "Terraform flaky → pre-create SLR",
], color="#E7157B", fill="#FDF1F7", title_color="#B0105C", align="start")

d.legend([
    (ORANGE, 1, "Instance query → VPC Resolver (Resolver query logging)"),
    (RED, 2, "Query to a custom DNS server: bypasses .2, not logged"),
    (BLUE, 3, "Internet resolver → public hosted zone (zone query logging)"),
    (GREEN, 4, "Delivery: the service-linked role writes to the destination"),
], cols=2)

d.save(HERE / "architecture")
