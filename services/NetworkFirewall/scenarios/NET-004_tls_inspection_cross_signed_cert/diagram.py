"""NET-004 diagrams -> architecture, handshakes (5 figures) and lab/architecture_lab (2 figures)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, GREEN, RED, PURPLE, TEAL, MUTED, INK  # noqa: E402

LAB = HERE / "lab"


# ---------------------------------------------------------------- helpers
def lifeline(d, icon, x, label, top=110, bottom=900, size=56):
    d.node(icon, x, top, label, size=size, weight=700)
    d.line([(x, top + 75), (x, bottom)], MUTED, dash=True)


def msg(d, x1, x2, y, color, n, title, detail=None, dash=False, both=False):
    """Horizontal sequence message with a numbered badge at its source end."""
    s = 1 if x2 > x1 else -1
    d.arrow([(x1 + 6 * s, y), (x2 - 6 * s, y)], color, dash=dash, both=both)
    if n is not None:
        d.badge(x1 + 40 * s, y, n, color)
    mx = (x1 + x2) / 2
    d.text(mx, y - 14, title, 15, 700, color, bg=True)
    if detail:
        d.text(mx, y + 26, detail, 13, 400, INK)


def zone(d, x, y, w, h, label, color, label_color=None):
    d.band(x, y, w, h, label, color=color, label_color=label_color or color, fill_opacity=0.05)


# ================================================================ architecture
def architecture():
    d = Diagram(2000, 1290)
    d.group(20, 20, 1700, 1110, "cloud", "AWS Cloud")
    d.group(50, 80, 1640, 1030, "region", "Region eu-west-1")
    d.group(80, 140, 440, 940, "account", "Account A · central certificates")
    d.group(560, 140, 1100, 940, "account", "Account B · workload (nonproduction)")
    d.group(590, 200, 1040, 560, "vpc", "Workload VPC")
    d.group(620, 260, 560, 460, "private_subnet", "Application subnets")
    d.text(900, 330, "10.100.36.0/24 · 10.100.37.0/24 · 10.100.38.0/24", 14, 400, MUTED)
    d.group(1220, 260, 340, 460, "public_subnet", "Firewall subnet")
    d.group(1760, 330, 220, 320, "internet", "Internet")

    # --- Account A
    d.note(110, 220, 380, 170, "Certificate type?", [
        "AMAZON_ISSUED (ACM public):",
        "private key not exportable",
        "→ no cross-account use",
        "IMPORTED (e.g. Let's Encrypt):",
        "cross-signed root → NF rejects",
    ], color=PURPLE, fill="#F7F3FF", title_color="#6A35D9")
    d.note(110, 450, 380, 200, "How NF validates the chain", [
        "Walks the chain to a self-signed",
        "root (Issuer == Subject).",
        "CA:TRUE with Issuer ≠ Subject",
        "= cross-signed root → rejected.",
        "No public root trust store;",
        "only exception: AMAZON_ISSUED.",
    ])
    d.node("AWS-Certificate-Manager", 300, 900, ["ACM (central account)", "certificate store"])

    # --- Account B, VPC
    d.node("Amazon-Elastic-Container-Service", 740, 480, ["ECS service", "post-trade APIs"])
    d.node("Elastic-Load-Balancing_Application-Load-Balancer", 1040, 480, ["ALB", "HTTPS :443"])
    d.node("AWS-Network-Firewall", 1390, 480, ["Network Firewall", "TLS inspection (inbound)"],
           pos="above", weight=600)
    d.mark(1424, 512, False)
    d.text(1410, 650, ["scope: private subnets", "→ 0.0.0.0/0 :443"], 13, 400, MUTED, "start")
    d.node("Amazon-VPC_Internet-Gateway", 1630, 480, "IGW", size=48, pos="above")
    d.node("Users", 1870, 480, ["External clients", "brokers · custodians"])

    # --- Account B, bottom
    d.node("AWS-Certificate-Manager", 700, 900, ["ACM (Account B)", "imported cert + key"])
    d.note(800, 830, 410, 170, "CreateTLSInspectionConfiguration", [
        "Attempt 1: chain is invalid and",
        "doesn't support the certificate body",
        "Attempt 2: root certificate NF",
        "can't validate (cross-signed root)",
        "ACM import itself succeeds",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.note(1240, 830, 400, 170, "Fix: no cross-signed imported root", [
        "A. ACM public cert in Account B",
        "B. Private CA + RAM, issue locally",
        "C. Private CA export (with key)",
        "D. External CA with a direct root",
    ], color=GREEN, fill="#EEF7EE", title_color=GREEN)

    # --- Flows
    d.arrow([(1838, 480), (1662, 480)], ORANGE)
    d.arrow([(1598, 480), (1422, 480)], ORANGE)
    d.badge(1510, 480, 1, ORANGE)
    d.arrow([(1358, 480), (1072, 480)], BLUE)
    d.arrow([(1008, 480), (772, 480)], BLUE)
    d.badge(1200, 480, 2, BLUE)
    d.arrow([(332, 900), (668, 900)], RED, dash=True)
    d.badge(390, 900, 3, RED)
    d.mark(540, 900, False)
    d.text(470, 870, "export cert + key?", 13, 700, RED, bg=True)
    d.arrow([(700, 868), (700, 790), (1390, 790), (1390, 514)], PURPLE)
    d.badge(1050, 790, 4, PURPLE)

    d.legend([
        (ORANGE, 1, "Inbound HTTPS :443 → IGW → Network Firewall"),
        (BLUE, 2, "Decrypted, inspected, re-encrypted → ALB → ECS"),
        (RED, 3, "Export ACM public cert + key to Account B (not possible)"),
        (PURPLE, 4, "Imported cert for TLS termination → rejected by NF"),
    ], cols=2)
    d.save(HERE / "architecture")


# ================================================================ handshakes
def tcp():
    d = Diagram(1600, 820)
    lifeline(d, "Client", 260, ["Client", "browser / app"], bottom=560)
    lifeline(d, "Server", 1000, ["Server", "port 443"], bottom=560)
    msg(d, 260, 1000, 250, BLUE, 1, "SYN (seq=100)", "“Can I connect?”")
    msg(d, 1000, 260, 350, GREEN, 2, "SYN-ACK (seq=300, ack=101)", "“Yes, go ahead”")
    msg(d, 260, 1000, 450, BLUE, 3, "ACK (seq=101, ack=301)", "“Confirmed, we are connected”")
    d.box(260, 590, 740, 60, "TCP connection established — ready for the TLS handshake",
          color=GREEN, fill="#EEF7EE", title_color=GREEN)
    d.note(1100, 200, 470, 160, "Layer 4 — transport", [
        "Everything in plain text (no encryption)",
        "Defines source/destination IPs and ports",
        "Network Firewall sees: IPs + ports",
        "Stateless / stateful rules can filter here",
    ], color=ORANGE, fill="#FFF5EB", title_color=ORANGE)
    d.legend([(BLUE, None, "Client → server"), (GREEN, None, "Server → client"),
              (ORANGE, None, "What Network Firewall can see / do", "dash")], cols=3)
    d.save(HERE / "handshakes")


def tls12():
    d = Diagram(1640, 1220)
    zone(d, 40, 190, 1080, 560, "In plain text", ORANGE)
    zone(d, 40, 770, 1080, 270, "Encrypted from here", PURPLE)
    lifeline(d, "Client", 260, ["Client"], bottom=1040)
    lifeline(d, "Server", 1000, ["Server"], bottom=1040)
    msg(d, 260, 1000, 260, BLUE, 1, "ClientHello",
        "SNI api.example.com · cipher suites [AES-GCM, ChaCha20…] · client random · TLS 1.2")
    msg(d, 1000, 260, 370, GREEN, 2, "ServerHello",
        "cipher TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 · server random")
    msg(d, 1000, 260, 480, RED, 3, "Certificate",
        "leaf + intermediate CA + root CA (or cross-signed root ← NET-004)")
    msg(d, 1000, 260, 590, GREEN, 4, "ServerHelloDone")
    msg(d, 260, 1000, 680, BLUE, 5, "ClientKeyExchange",
        "pre-master secret, encrypted with the certificate public key")
    msg(d, 260, 1000, 850, BLUE, 6, "ChangeCipherSpec + Finished")
    msg(d, 1000, 260, 930, GREEN, 7, "ChangeCipherSpec + Finished")
    d.box(280, 975, 700, 50, "Encrypted application traffic (HTTP requests / responses)",
          color=PURPLE, fill="#F7F3FF", title_color="#6A35D9")
    d.note(1150, 210, 460, 90, "SNI is visible unencrypted", [
        "Network Firewall can filter by domain here"],
        color=ORANGE, fill="#FFF5EB", title_color=ORANGE)
    d.note(1150, 400, 460, 150, "Chain-of-trust check", [
        "The client validates the chain here.",
        "NF does the same when creating the",
        "TLSInspectionConfiguration:",
        "cross-signed root → rejected",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.note(1150, 640, 460, 125, "Key derivation", [
        "master = PRF(pre-master,",
        "client_random, server_random)",
        "→ symmetric session keys",
    ], color=PURPLE, fill="#F7F3FF", title_color="#6A35D9")
    d.legend([(BLUE, None, "Client → server"), (GREEN, None, "Server → client"),
              (RED, None, "Certificate chain (NET-004 failure point)")], cols=3)
    d.save(HERE / "handshakes_tls12")


def tls13():
    d = Diagram(1640, 1020)
    zone(d, 40, 190, 1080, 300, "In plain text", ORANGE)
    zone(d, 40, 510, 1080, 330, "Encrypted from here", PURPLE)
    lifeline(d, "Client", 260, ["Client"], bottom=840)
    lifeline(d, "Server", 1000, ["Server"], bottom=840)
    msg(d, 260, 1000, 260, BLUE, 1, "ClientHello",
        ["SNI api.example.com (plain) · versions [1.3, 1.2] · cipher suites",
         "key share: x25519 public key — negotiation starts early"])
    msg(d, 1000, 260, 400, GREEN, 2, "ServerHello + key share", "server x25519 public key")
    msg(d, 1000, 260, 580, RED, 3, "Certificate (encrypted)", "leaf + intermediate + root")
    msg(d, 1000, 260, 680, GREEN, 4, "Finished")
    msg(d, 260, 1000, 750, BLUE, 5, "Finished")
    d.box(280, 780, 700, 50, "Encrypted application traffic", color=PURPLE, fill="#F7F3FF",
          title_color="#6A35D9")
    d.note(1150, 210, 460, 150, "vs TLS 1.2", [
        "1 round trip (vs 2)",
        "Certificate encrypted (vs plain text)",
        "Forward secrecy mandatory",
        "Legacy cipher suites removed",
    ], color=PURPLE, fill="#F7F3FF", title_color="#6A35D9")
    d.note(1150, 530, 460, 100, "Certificate not visible on the wire", [
        "A passive observer can't see which",
        "certificate the server uses",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.legend([(BLUE, None, "Client → server"), (GREEN, None, "Server → client"),
              (RED, None, "Certificate chain")], cols=3)
    d.save(HERE / "handshakes_tls13")


def mitm():
    d = Diagram(1860, 1360)
    C, N, S = 300, 800, 1320
    zone(d, 20, 190, 900, 510, "Session #1 · client ↔ NF", BLUE)
    zone(d, 540, 860, 900, 240, "Session #2 · NF ↔ server", PURPLE)
    lifeline(d, "Users", C, ["Client", "broker / custodian"], bottom=1200)
    lifeline(d, "AWS-Network-Firewall", N, ["Network Firewall", "TLS inspection engine"], bottom=1200)
    lifeline(d, "Elastic-Load-Balancing_Application-Load-Balancer", S, ["Server", "ALB → ECS"],
             bottom=1200)
    msg(d, C, N, 260, BLUE, 1, "TCP handshake", "SYN / SYN-ACK / ACK")
    msg(d, C, N, 360, BLUE, 2, "ClientHello", "SNI api.trading.example.com")
    msg(d, N, C, 460, GREEN, 3, "ServerHello + certificate", "NF acts AS the server (configured private key)")
    msg(d, C, N, 560, BLUE, 4, "Key exchange → session keys", both=True)
    msg(d, C, N, 640, BLUE, 5, "HTTP request (encrypted)", "GET /api/trades HTTP/1.1")
    d.box(840, 715, 360, 130, "NF decrypts and inspects",
          ["Suricata rules", "malware · injection · C2", "if OK → re-encrypt, forward"],
          color=ORANGE, fill="#FFF5EB", title_color=ORANGE, size=13)
    msg(d, N, S, 920, PURPLE, 6, "TCP handshake")
    msg(d, N, S, 990, PURPLE, 7, "TLS handshake (NF as client → real server)")
    msg(d, N, S, 1060, PURPLE, 8, "HTTP request (re-encrypted) · GET /api/trades")
    msg(d, S, C, 1160, GREEN, 9, "HTTP response (encrypted) · 200 OK · via NF")
    d.note(1440, 200, 400, 190, "Why NF needs the private key", [
        "In session #1 NF acts as the real",
        "server. The client's key exchange",
        "uses the certificate public key.",
        "No private key → no session keys",
        "→ no decryption.",
    ], color=PURPLE, fill="#F7F3FF", title_color="#6A35D9")
    d.note(1440, 420, 400, 160, "NET-004", [
        "Imported cert has a cross-signed",
        "root → rejected at",
        "CreateTLSInspectionConfiguration;",
        "NF never gets to use it.",
    ], color=RED, fill="#FDF0EE", title_color=RED)
    d.legend([(BLUE, None, "Client → NF (session #1)"), (PURPLE, None, "NF → server (session #2)"),
              (GREEN, None, "Responses towards the client"), (ORANGE, None, "NF inspection", "dash")],
             cols=4)
    d.save(HERE / "handshakes_mitm")


def https_flow():
    d = Diagram(1640, 1340)
    C, S = 380, 1020
    zone(d, 20, 190, 1100, 300, "Phase 1 · TCP (L4) · connection", BLUE)
    zone(d, 20, 510, 1100, 420, "Phase 2 · TLS (L6) · encryption", PURPLE)
    zone(d, 20, 950, 1100, 220, "Phase 3 · HTTP (L7) · encrypted data", GREEN)
    lifeline(d, "Client", C, ["Client"], bottom=1170)
    lifeline(d, "Server", S, ["Server"], bottom=1170)
    msg(d, C, S, 270, BLUE, 1, "SYN")
    msg(d, S, C, 350, GREEN, 2, "SYN-ACK")
    msg(d, C, S, 430, BLUE, 3, "ACK")
    msg(d, C, S, 590, BLUE, 4, "ClientHello", "SNI + cipher suites")
    msg(d, S, C, 690, GREEN, 5, "ServerHello + certificate")
    msg(d, C, S, 790, BLUE, 6, "Key exchange")
    msg(d, C, S, 870, PURPLE, 7, "Finished", both=True)
    d.text(700, 915, "Result: both sides hold the session keys", 13, 600, "#6A35D9")
    msg(d, C, S, 1040, BLUE, 8, "Request", "GET /api/trades · headers + body")
    msg(d, S, C, 1120, GREEN, 9, "Response", None)
    d.text(700, 1160, "200 OK {trades: […]}", 13, 400, INK)
    kw = dict(color=ORANGE, fill="#FFF5EB", title_color=ORANGE)
    d.note(1150, 230, 460, 90, "NF sees", ["IPs + ports"], **kw)
    d.note(1150, 560, 460, 160, "NF sees: SNI (domain)", [
        "Without TLS inspection:",
        "domain filtering only",
        "With TLS inspection:",
        "NF terminates the handshake",
    ], **kw)
    d.note(1150, 980, 460, 130, "NF sees", [
        "Without TLS inspection: nothing",
        "With TLS inspection: everything —",
        "URLs, headers, body, payload",
    ], **kw)
    d.text(700, 1215, "Typical latency: TCP ~1 RTT · TLS 1.2 ~2 RTT · TLS 1.3 ~1 RTT · HTTP variable",
           14, 400, MUTED)
    d.legend([(BLUE, None, "Client → server"), (GREEN, None, "Server → client"),
              (ORANGE, None, "What Network Firewall can see / do", "dash")], cols=3)
    d.save(HERE / "handshakes_https_flow")


# ================================================================ lab
def lab_architecture():
    d = Diagram(1960, 1290)
    d.group(20, 380, 220, 300, "internet", "Internet")
    d.group(280, 20, 1660, 1110, "cloud", "AWS Cloud")
    d.group(310, 80, 1600, 1030, "region", "Region eu-west-1")
    d.group(340, 140, 1540, 940, "account", "Lab account")
    d.group(370, 200, 1480, 600, "vpc", "net004-lab-vpc · 10.0.0.0/16")
    d.group(460, 260, 360, 500, "public_subnet", "Firewall subnet · 10.0.1.0/28")
    d.group(880, 260, 400, 500, "public_subnet", "Public subnet · 10.0.2.0/24")
    d.group(1340, 260, 480, 500, "private_subnet", "Private subnet · 10.0.3.0/24")

    d.node("Client", 130, 530, ["External client", "HTTPS"])
    d.node("Amazon-VPC_Internet-Gateway", 370, 530, "IGW", size=48, pos="above")
    d.node("AWS-Network-Firewall", 640, 530, ["net004-lab-firewall", "TLS inspection · inbound :443"],
           pos="above", weight=600)
    d.node("Elastic-Load-Balancing_Application-Load-Balancer", 1080, 530, ["net004-lab-alb", "HTTPS :443"])
    d.node("Amazon-EC2_Instance", 1580, 530, ["net004-web-server", "nginx · HTTP :80"])

    rt = dict(color="#7D8998", fill="#FFFFFF", size=13)
    d.box(476, 650, 150, 80, "Subnet RT", ["0.0.0.0/0 → IGW"], **rt)
    d.box(950, 650, 260, 80, "Public subnet RT", ["0.0.0.0/0 → IGW"], **rt)
    d.box(1440, 650, 280, 80, "Private subnet RT", ["0.0.0.0/0 → NAT (optional)"], **rt)
    d.box(380, 840, 150, 110, "IGW edge RT", ["10.0.2.0/24", "→ NF endpoint"], color=BLUE,
          fill="#EEF4FB", title_color=BLUE, size=13)

    d.node("AWS-Certificate-Manager", 640, 900, ["ACM certificate (imported)", "cert + chain + private key"])
    d.note(800, 840, 520, 160, "Tests", [
        "1. Imported cert, cross-signed root",
        "   → CreateTLSInspectionConfiguration fails",
        "2. Imported cert, direct self-signed root → works",
        "3. ACM AMAZON_ISSUED (same account) → works",
    ], align="start")
    d.note(1350, 840, 500, 160, "Routing key", [
        "The IGW edge route table forces",
        "inbound traffic through NF before",
        "the ALB. Without it traffic goes",
        "IGW → ALB directly.",
    ], color=BLUE, fill="#EEF4FB", title_color=BLUE)

    # flows
    d.arrow([(162, 530), (342, 530)], ORANGE)
    d.badge(255, 530, 1, ORANGE)
    d.arrow([(398, 530), (606, 530)], BLUE)
    d.badge(500, 530, 2, BLUE)
    d.badge(692, 570, 3, PURPLE)
    d.arrow([(674, 530), (1046, 530)], GREEN)
    d.badge(850, 530, 4, GREEN)
    d.arrow([(1114, 530), (1546, 530)], TEAL)
    d.badge(1310, 530, 5, TEAL)
    d.arrow([(640, 866), (640, 564)], MUTED, dash=True)
    d.text(640, 830, "cert for TLS termination", 13, 600, MUTED, bg=True)

    d.legend([
        (ORANGE, 1, "Client → IGW (HTTPS)"),
        (BLUE, 2, "IGW edge RT → NF endpoint"),
        (PURPLE, 3, "NF decrypts, inspects, re-encrypts"),
        (GREEN, 4, "NF → ALB (public subnet)"),
        (TEAL, 5, "ALB → EC2 HTTP :80 (private subnet)"),
    ], cols=3)
    d.save(LAB / "architecture_lab")


def lab_traffic():
    d = Diagram(1960, 880)
    xs = [170, 560, 960, 1360, 1760]
    y = 200
    d.node("Client", xs[0], y, "Client", weight=700)
    d.node("Amazon-VPC_Internet-Gateway", xs[1], y, "Internet gateway", weight=700)
    d.node("AWS-Network-Firewall_Endpoints", xs[2], y, "NF endpoint", weight=700)
    d.node("Elastic-Load-Balancing_Application-Load-Balancer", xs[3], y, "ALB", weight=700)
    d.node("Amazon-EC2_Instance", xs[4], y, "EC2", weight=700)
    cols = [ORANGE, BLUE, PURPLE, GREEN, TEAL]
    steps = [
        ("HTTPS request", ["dst: EIP / ALB DNS"]),
        ("Edge RT redirect", ["IGW edge route table", "→ NF endpoint"]),
        ("TLS inspection", ["TLS termination", "Suricata inspection", "re-encrypt"]),
        ("TLS termination", ["(again)", "→ target group"]),
        ("HTTP :80", ["nginx response"]),
    ]
    for i, (x, (t, ls)) in enumerate(zip(xs, steps)):
        d.box(x - 150, 300, 300, 110, t, ls, color=cols[i], fill="#FFFFFF", title_color=cols[i], size=13)
        d.badge(x - 150, 300, i + 1, cols[i])
    # encryption state of each leg
    d.arrow([(xs[0] + 34, y), (xs[1] - 30, y)], ORANGE)
    d.arrow([(xs[1] + 30, y), (xs[2] - 34, y)], ORANGE)
    d.arrow([(xs[2] + 34, y), (xs[3] - 34, y)], PURPLE)
    d.arrow([(xs[3] + 34, y), (xs[4] - 34, y)], MUTED)
    d.text((xs[0] + xs[2]) / 2, y - 50, "HTTPS · encrypted with the original cert", 14, 700, ORANGE, bg=True)
    d.text((xs[2] + xs[3]) / 2, y - 50, "re-encrypted · new TLS session", 14, 700, PURPLE, bg=True)
    d.text((xs[3] + xs[4]) / 2, y - 50, "HTTP :80", 14, 700, MUTED, bg=True)
    d.box(xs[2] - 150, 440, 300, 60, "Plain text inside NF", ["NF inspects"], color=GREEN,
          fill="#EEF7EE", title_color=GREEN, size=13)

    d.box(40, 550, 900, 190, "Required route tables", [
        "IGW edge RT:  10.0.2.0/24 → NF endpoint",
        "Firewall subnet RT:  0.0.0.0/0 → IGW",
        "Public subnet RT:  0.0.0.0/0 → IGW",
        "Private subnet RT:  0.0.0.0/0 → NAT (if egress is needed)",
    ], color=BLUE, fill="#EEF4FB", title_color=BLUE, align="start", size=15)
    d.note(980, 550, 940, 190, "Routing key", [
        "The IGW edge route table is what forces traffic",
        "through NF before it reaches the ALB.",
        "Without it, traffic goes IGW → ALB directly.",
    ], color=BLUE, fill="#EEF4FB", title_color=BLUE, size=15)
    d.legend([
        (ORANGE, None, "HTTPS (encrypted with original cert)"),
        (GREEN, None, "Plain text — NF inspects", "dash"),
        (PURPLE, None, "Re-encrypted (new TLS session)"),
        (MUTED, None, "HTTP :80 to the target"),
    ], cols=4)
    d.save(LAB / "architecture_lab_traffic_flow")


if __name__ == "__main__":
    architecture()
    tcp()
    tls12()
    tls13()
    mitm()
    https_flow()
    lab_architecture()
    lab_traffic()
