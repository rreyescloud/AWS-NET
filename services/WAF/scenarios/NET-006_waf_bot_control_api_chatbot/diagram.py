"""NET-006 diagrams -> architecture.(svg|png) and concepts.(svg|png)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, MUTED  # noqa: E402


# ======================================================================
# Figure 1 — architecture and the Challenge problem
# ======================================================================
def architecture():
    d = Diagram(2000, 1070)

    d.group(20, 160, 290, 560, "internet", "Internet (EU)")
    d.group(340, 20, 1640, 720, "cloud", "AWS Cloud")
    d.group(370, 80, 530, 640, "generic", "Global · CloudFront edge (EU locations)")
    d.group(930, 80, 1020, 380, "region", "Region eu-west-1")

    # clients
    d.node("Users", 160, 300, ["EU citizens · no login", "browser / mobile app"], pos="above")
    d.node("AWS-WAF_Bad-Bot", 160, 600, ["Bots · scrapers", "AI crawlers"], pos="above")

    # edge
    d.node("AWS-WAF", 520, 300, ["Global WAF", "CLOUDFRONT scope"], pos="above", weight=600)
    d.node("Amazon-CloudFront", 780, 300, ["CloudFront", "distribution"], pos="above")
    d.box(400, 470, 470, 210, "Web ACL (customer, priority order)", [
        "1  IP reputation list",
        "2  Common rule set",
        "3  Known bad inputs",
        "4  Rate limit 200 / IP",
        "5  Bot Control (Targeted + ML)  ← PROBLEM",
    ], align="start", size=14, color="#DD344C")

    # region
    d.node("AWS-WAF", 1030, 300, ["Regional WAF", "only CloudFront in"], pos="above")
    d.node("Amazon-API-Gateway", 1260, 300, ["API Gateway", "REST · POST /chat"], pos="above")
    d.node("AWS-Lambda", 1500, 300, "Lambda", pos="above")
    d.node("Amazon-Bedrock", 1780, 300, ["Amazon Bedrock", "Knowledge Bases + Nova Lite"], pos="above")
    d.text(1440, 420, "every request = inference cost", 14, 400, MUTED, italic=True)

    d.note(930, 490, 1020, 200, "Recommended Web ACL (CloudFront scope)", [
        "Geo EU only · IP reputation · Common rule set · Known bad inputs",
        "Rate 200 / IP global + 50 / IP on /api/chat",
        "Block: no User-Agent · AI scraper UAs · body > 4 KB",
        "Bot Control → COUNT (labels only) + custom rule blocking bad-bot labels",
        "With the JS SDK: token required for /api/chat",
    ], color=GREEN, fill="#EEF7EE", title_color="#1F6B1F")

    # flow 1: chat request (orange)
    d.arrow([(192, 288), (486, 288)], ORANGE)
    d.arrow([(554, 288), (748, 288)], ORANGE)
    d.arrow([(812, 288), (998, 288)], ORANGE)
    d.arrow([(1062, 288), (1228, 288)], ORANGE)
    d.arrow([(1292, 288), (1468, 288)], ORANGE)
    d.arrow([(1532, 288), (1748, 288)], ORANGE)
    d.badge(250, 288, 1, ORANGE)
    d.text(872, 276, "origin request", 13, 600, ORANGE, bg=True)
    # flow 2: challenge returned (red)
    d.arrow([(486, 316), (194, 316)], RED)
    d.badge(330, 316, 2, RED)
    d.text(330, 352, "202 + JS challenge (HTML)", 14, 700, RED, bg=True)
    d.text(330, 372, "fetch() expects JSON → error", 13, 400, RED, bg=True)
    # flow 3: SDK token (blue)
    d.arrow([(160, 332), (160, 420), (520, 420), (520, 334)], BLUE)
    d.badge(250, 420, 3, BLUE)
    d.text(400, 410, "aws-waf-token", 14, 700, BLUE, bg=True)
    # flow 4: bots blocked by the Web ACL (purple)
    d.arrow([(192, 600), (394, 600)], PURPLE)
    d.badge(290, 600, 4, PURPLE)
    d.text(290, 640, "403 · or labelled", 13, 600, PURPLE, bg=True)
    # WAF -> rule list
    d.line([(520, 436), (520, 464)], MUTED, dash=True)

    d.note(20, 770, 640, 140, "Why the Challenge breaks an API", [
        "Challenge needs a browser to run JavaScript.",
        "A REST call gets 202 + HTML instead of JSON,",
        "so the chatbot frontend shows an error.",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.note(680, 770, 640, 140, "Lab: Targeted + ML + Block", [
        "requests 1–2 → 200 (tolerance window)",
        "request 3+ → 202 CHALLENGE",
        "rule TGT_VolumetricIpTokenAbsent · label token_absent",
    ], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")
    d.note(1340, 770, 640, 140, "Three ways out", [
        "1  JS SDK: token acquired silently, sent on fetch",
        "2  Bot Control in COUNT + label-based block rules",
        "3  No Bot Control: rate, geo, UA, body-size rules",
    ], color=GREEN, fill="#EEF7EE", title_color="#1F6B1F")

    d.legend([
        (ORANGE, 1, "Chat request: browser → CloudFront → API Gateway → Lambda → Bedrock"),
        (RED, 2, "Bot Control emits 202 Challenge to an API call"),
        (BLUE, 3, "Fix: WAF JS SDK sends aws-waf-token with each call"),
        (PURPLE, 4, "Automated traffic blocked or labelled by the Web ACL"),
    ], cols=2)
    d.save(HERE / "architecture")


# ======================================================================
# Figure 2 — concepts: API, HTTP, REST, API Gateway, in the lab
# ======================================================================
def concepts():
    d = Diagram(2000, 960)

    d.group(280, 60, 1700, 560, "cloud", "AWS Cloud")
    d.group(310, 190, 500, 400, "generic", "Global · edge")
    d.group(840, 190, 1110, 400, "region", "Region us-east-1 (lab)")

    d.node("Client", 140, 290, ["Browser", "chatbot page"])
    d.node("AWS-WAF", 440, 290, ["AWS WAF", "Web ACL"], weight=600)
    d.node("Amazon-CloudFront", 680, 290, "CloudFront")
    d.node("Amazon-Simple-Storage-Service", 960, 470, ["S3", "index.html"])
    d.node("Amazon-API-Gateway", 1100, 290, ["API Gateway", "REST · POST /chat"])
    d.node("AWS-Lambda", 1400, 290, ["Lambda", "proxy integration"])
    d.node("Amazon-Bedrock", 1740, 290, ["Bedrock", "converse(nova-lite)"])

    # flow 1: GET / (orange)
    d.arrow([(172, 300), (408, 300)], ORANGE)
    d.arrow([(472, 300), (648, 300)], ORANGE)
    d.arrow([(712, 305), (760, 305), (760, 470), (928, 470)], ORANGE)
    d.badge(230, 300, 1, ORANGE)
    d.text(840, 462, "GET /", 13, 700, ORANGE, bg=True)
    # flow 2: POST /chat (blue)
    d.arrow([(172, 275), (408, 275)], BLUE)
    d.arrow([(472, 275), (648, 275)], BLUE)
    d.arrow([(712, 275), (1068, 275)], BLUE)
    d.arrow([(1132, 275), (1368, 275)], BLUE)
    d.arrow([(1432, 275), (1708, 275)], BLUE)
    d.badge(890, 275, 2, BLUE)
    d.text(1250, 262, '{"message": "Hello"}', 13, 600, BLUE, bg=True)
    # flow 3: response (green)
    d.arrow([(1400, 258), (1400, 160), (140, 160), (140, 256)], GREEN)
    d.badge(760, 160, 3, GREEN)
    d.text(1050, 150, '200 OK  {"response": "Hello! ..."}', 14, 700, GREEN, bg=True)

    d.note(330, 380, 400, 170, "WAF evaluates every request first", [
        "bad IP / SQLi / XSS → 403 Block",
        "rate limit → 403 (or custom 429)",
        "bot → label · count · block · challenge",
        "otherwise → ALLOW to the origin",
    ], color="#DD344C", fill="#FDF0EE", title_color="#B0102A", size=13, align="start")
    d.note(1100, 390, 820, 130, "API Gateway = the smart doorman", [
        "receive → validate (auth, format) → route to Lambda",
        "→ throttle → meter → return the response",
        "types: REST API (lab) · HTTP API · WebSocket",
    ], color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9", size=13, align="start")

    cols = [
        ("API = a contract", [
            "“send me THIS, I return THAT”",
            "like a waiter: you order, the",
            "kitchen cooks, you never",
            "walk into the kitchen",
        ]),
        ("HTTP request", [
            "METHOD · URL · HEADERS · BODY",
            "POST /chat · Content-Type: json",
            "GET read · POST send",
            "PUT / PATCH update · DELETE",
        ]),
        ("Status codes", [
            "200 OK · 201 Created · 400 Bad Request",
            "401 not authenticated · 403 WAF block",
            "404 not found · 429 too many · 500 error",
            "202 + HTML = WAF challenge",
        ]),
        ("REST style", [
            "URLs are resources, methods are actions",
            "JSON responses · stateless requests",
            "GET /users/123 · POST /chat",
            "DELETE /users/123",
        ]),
    ]
    for i, (t, lines) in enumerate(cols):
        d.box(20 + i * 495, 650, 470, 160, t, lines, size=14, color="#7D8998", fill="#F7F9FC")

    d.legend([
        (ORANGE, 1, "GET / → CloudFront serves index.html from S3"),
        (BLUE, 2, "POST /chat → API Gateway → Lambda → Bedrock"),
        (GREEN, 3, "200 + JSON back; the page shows the reply"),
    ], cols=3)
    d.save(HERE / "concepts")


architecture()
concepts()
