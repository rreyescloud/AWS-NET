"""NET-023 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, TEAL  # noqa: E402

d = Diagram(1920, 1110)

# --- Groups
d.group(20, 160, 280, 420, "internet", "Clients")
d.group(340, 20, 1220, 920, "cloud", "AWS Cloud")
d.group(370, 80, 1160, 840, "region", "Region (one commercial Region)")
d.group(400, 140, 1100, 760, "account", "<ACCOUNT_APP>")
d.group(1600, 160, 300, 420, "generic", "Outside AWS")

# --- Client
d.node("Client", 160, 335, ["Client app", "POST /token", "grant_type=password"], size=64)

# --- Front door + web ACL
d.node("Amazon-API-Gateway", 560, 335, ["API Gateway (REST) / ALB", "TLS terminates here"],
       size=64, pos="above", weight=600)
d.band(430, 520, 420, 200, "Web ACL · REGIONAL")
d.node("AWS-WAF_Rule", 540, 610, ["Rate-based per IP", "grant_type=password → 429"])
d.node("AWS-WAF_Managed-Rule", 740, 610, ["ATP request inspection", "FORM_ENCODED"])

# --- Compute + state
d.node("AWS-Lambda_Lambda-Function", 940, 335, ["Lambda · OAuth token endpoint", "per-account failure counter"],
       size=64, pos="above", weight=600)
d.node("Amazon-DynamoDB_Table", 940, 610, ["DynamoDB table", "sha256(user)#minute"])
d.box(1060, 540, 410, 150, "Counter (per account, 1-min window)", [
    "key = sha256(lower(trim(username)))#minute",
    "fails ≥ 5 → 429 + Retry-After, IdP not called",
    "IdP failure → ADD fails :1 · TTL +120 s",
], color="#8C4FFF", fill="#F7F3FF", size=14, align="start")

# --- External IdP
d.node("Authenticated-User", 1750, 335, ["External IdP", "only place where", "\"login failed\" is known"], size=64)

# --- Flows
d.arrow([(194, 320), (526, 320)], ORANGE)
d.badge(360, 320, 1, ORANGE)
d.arrow([(560, 370), (560, 516)], BLUE, both=True)
d.badge(560, 445, 2, BLUE)
d.text(580, 450, "verdict? (request only)", 13, 600, BLUE, "start", bg=True)
d.arrow([(594, 320), (906, 320)], GREEN)
d.badge(750, 320, 3, GREEN)
d.text(750, 296, "forward if ALLOW", 13, 600, GREEN, bg=True)
d.arrow([(940, 370), (940, 578)], PURPLE, both=True)
d.badge(940, 470, 4, PURPLE)
d.text(960, 475, "check / increment", 13, 600, PURPLE, "start", bg=True)
d.arrow([(974, 330), (1716, 330)], TEAL, both=True)
d.badge(1250, 330, 5, TEAL)
d.text(1250, 300, "validate credentials (skipped when blocked)", 13, 600, TEAL, bg=True)
d.arrow([(906, 352), (596, 352)], RED, dash=True)
d.arrow([(524, 352), (196, 352)], RED, dash=True)
d.badge(360, 352, 6, RED)
d.text(750, 382, "200 · 400 invalid_grant · 429", 13, 600, RED, bg=True)

# --- Notes
d.note(430, 760, 520, 110, "What a REGIONAL web ACL sees", [
    "method, URI, headers, IP, first 8 KB of body",
    "never the response · no rate key on a body field",
], color="#00838F", fill="#E6F6F7", title_color="#00838F")
d.note(980, 760, 490, 110, "Lab run (2026-11-02)", [
    "30 IPs × 1 account: WAF ALLOW all, Lambda 429 from #6",
    "real user locked out too · rate block only at #153 (88 s)",
], color="#E07B00", fill="#FFF4E5", title_color="#B35F00")
d.note(1600, 620, 300, 200, "Managed options fall short", [
    "ATP response inspection:",
    "CloudFront web ACLs only",
    "ATP aggregates on the real",
    "source IP, not X-Forwarded-For",
    "→ the Lambda counter is the",
    "only exact fit",
], color="#C7186E")

d.legend([
    (ORANGE, 1, "POST /token (username in form body)"),
    (BLUE, 2, "API Gateway / ALB asks WAF for a verdict"),
    (GREEN, 3, "Forward to the Lambda if ALLOW"),
    (PURPLE, 4, "Check / increment the per-account counter"),
    (TEAL, 5, "Validate credentials with the IdP"),
    (RED, 6, "Response to the client · never back to WAF"),
], cols=3)

d.save(HERE / "architecture")
