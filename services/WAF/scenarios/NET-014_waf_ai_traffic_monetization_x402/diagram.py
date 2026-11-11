"""NET-014 architecture diagram -> architecture.svg / architecture.png."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, MUTED  # noqa: E402

d = Diagram(1960, 1110)

# --- Groups
d.group(20, 120, 320, 620, "internet", "Internet clients")
d.group(380, 20, 1080, 930, "cloud", "AWS Cloud")
d.group(410, 80, 1020, 850, "region", "Global edge · CLOUDFRONT scope (us-east-1)")
d.group(440, 140, 960, 770, "account", "Lab account")
d.group(1500, 120, 440, 700, "generic", "Off-AWS · Base Sepolia testnet")

# --- Clients
d.node("AWS-WAF_Bot", 170, 300, ["AI bot / agent", "UA GPTBot · payer wallet"], size=96)
d.node("Client", 170, 580, ["Browser", "UA Mozilla/5.0 Chrome"], size=64)

# --- Edge + Web ACL
d.node("Amazon-CloudFront", 600, 300, ["CloudFront", "distribution"], size=96, weight=600, pos="above")
d.band(700, 200, 660, 200, "Web ACL waf-monetization-lab")
d.node("AWS-WAF_Bot-Control", 860, 300, ["Rule 0 · AWS-BotControl", "Targeted · ML on"])
d.node("AWS-WAF_Rule", 1200, 300, ["Rule 1 · Monetize-AI-Bots-402", "label bot:category:ai → Monetize"])
d.arrow([(650, 300), (828, 300)], MUTED)
d.arrow([(888, 300), (1168, 300)], MUTED)
d.text(1030, 288, "labels", 13, 600, MUTED, bg=True)

# --- Origin, KMS, logs
d.node("Amazon-Simple-Storage-Service_Bucket", 800, 600, ["S3 origin (KMS-encrypted)", "/documents/premium.txt"])
d.node("AWS-Key-Management-Service", 1000, 600, ["AWS KMS key", "+ kms:Decrypt for CF"])
d.node("Amazon-CloudWatch_Logs", 1230, 600, ["CloudWatch Logs", "aws-waf-logs-monetization-lab"], label_size=14)
d.arrow([(830, 600), (968, 600)], MUTED, dash=True)
d.arrow([(1230, 400), (1230, 568)], MUTED)
d.text(1300, 490, "action=MONETIZE", 13, 600, MUTED, bg=True)

# --- Off-AWS payment rail
d.node("Server", 1720, 300, ["x402 facilitator", "verifies EIP-3009 signature"])
d.node("Amazon-Managed-Blockchain_Blockchain", 1720, 540, ["Base Sepolia · USDC", "payer → payTo wallet"], size=64)
d.box(1540, 660, 360, 130, "402 payment spec",
      ["0.001 USDC · scheme exact", "network eip155:84532", "gasless for the payer"],
      color="#8C4FFF", fill="#F7F3FF", size=14)

# --- Request / response bundle bot <-> CloudFront
d.arrow([(220, 262), (550, 262)], ORANGE)
d.badge(300, 262, 1, ORANGE)
d.arrow([(550, 288), (222, 288)], RED, dash=True)
d.badge(360, 288, 2, RED)
d.arrow([(220, 314), (550, 314)], BLUE)
d.badge(420, 314, 3, BLUE)
d.arrow([(550, 340), (222, 340)], GREEN)
d.badge(480, 340, 5, GREEN)

# --- Payment verification and settlement
d.arrow([(1230, 300), (1688, 300)], PURPLE)
d.badge(1450, 300, 4, PURPLE)
d.arrow([(1720, 385), (1720, 500)], PURPLE)
d.badge(1720, 442, 4, PURPLE)
d.text(1748, 448, "settle", 14, 600, PURPLE, "start")

# --- Origin fetch (only after a valid payment)
d.arrow([(625, 350), (625, 600), (768, 600)], GREEN)
d.badge(625, 480, 5, GREEN)

# --- Browser contrast path
d.arrow([(205, 580), (575, 580), (575, 352)], MUTED, dash=True)
d.text(390, 568, "no AI label → 200", 13, 600, MUTED, bg=True)

# --- Findings
d.note(470, 720, 900, 160, "What the lab showed", [
    "Monetize action + MonetizationConfig are console-only: get-web-acl shows Action {}",
    "403 after a valid payment was the origin: KMS key policy lacked kms:Decrypt for CloudFront",
    "WAF settles the payment only when the origin returns 200 — no USDC moved while S3 said 403",
    "Payer and payTo wallets must differ, or the facilitator returns self_send_not_allowed",
], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")

d.legend([
    (ORANGE, 1, "GET /premium.txt as an AI bot"),
    (RED, 2, "HTTP 402 + x402 challenge (price, payTo)"),
    (BLUE, 3, "GET again + PAYMENT-SIGNATURE"),
    (PURPLE, 4, "Facilitator verifies, settles USDC on-chain"),
    (GREEN, 5, "Origin 200 → content + PAYMENT-RESPONSE"),
    (MUTED, None, "Browser, logs, KMS (supporting)", "dash"),
], cols=3)

d.save(HERE / "architecture")
