"""NET-007 diagrams -> architecture.png (case: sandbox + production) and architecture_lab.png (lab)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "tools/diagrams").is_dir()) / "tools/diagrams"))
from awsdiag import Diagram, ORANGE, BLUE, RED, MUTED  # noqa: E402

AURORA = "Amazon-Aurora-MySQL-Instance"
DMS = "AWS-Database-Migration-Service"
MSK = "Amazon-Managed-Streaming-for-Apache-Kafka"
PURPLE_NOTE = dict(color="#8C4FFF", fill="#F7F3FF", title_color="#6A35D9")
RED_NOTE = dict(color=RED, fill="#FDF0EE", title_color=RED)


def case():
    d = Diagram(2000, 1322)

    d.group(20, 20, 1960, 1000, "cloud", "AWS Cloud")
    d.group(50, 80, 1900, 920, "region", "Region eu-central-1")
    d.group(80, 140, 1300, 440, "account", "Sandbox account")
    d.group(110, 200, 1240, 360, "vpc", "Sandbox VPC")
    d.group(1420, 140, 500, 440, "account", "Remote account (Kafka)")
    d.group(1450, 200, 440, 360, "vpc", "Remote VPC")
    d.group(80, 620, 1300, 360, "account", "Production account (separate case)")
    d.group(110, 680, 1240, 280, "vpc", "Production VPC")

    # --- Sandbox
    d.node(AURORA, 250, 400, ["Aurora MySQL", "Serverless v2 · ACU 24", "13 ledger tables"])
    d.node(DMS, 650, 400, ["DMS replication instance", "dms.c5.4xlarge · 13 tasks (1 per table)",
                           "ParallelApply buffer 1000 · threads 8 · queues 16"])
    d.node(MSK, 1150, 280, ["MSK · local Kafka (same VPC)", "peaks 14 min"], color=RED)
    d.node(MSK, 1670, 400, ["MSK · remote Kafka", "cross-VPC / cross-account", "peaks 27 min"], color=RED)

    d.arrow([(278, 400), (622, 400)], ORANGE)
    d.badge(450, 400, 1, ORANGE)
    d.text(450, 376, "binlog CDC", 13, 600, ORANGE, bg=True)
    d.arrow([(650, 372), (650, 280), (1122, 280)], BLUE)
    d.badge(880, 280, 2, BLUE)
    d.text(880, 256, "TARGET_APPLY · same VPC", 13, 600, BLUE, bg=True)
    d.arrow([(678, 410), (1642, 410)], RED)
    d.badge(1100, 410, 3, RED)
    d.text(1100, 444, "TARGET_APPLY · cross-VPC", 13, 600, RED, bg=True)

    # --- Production
    d.node(AURORA, 250, 820, ["Aurora MySQL", "db.r6x.8xlarge (provisioned)", "2 tables for testing"])
    d.node(DMS, 650, 820, ["DMS replication instance", "dms.r7i.4xlarge", "only 2 tables"])
    d.node(MSK, 1150, 820, ["MSK express.m7g.4xlarge", "peaks 14 min"], color=RED)
    d.arrow([(278, 820), (622, 820)], ORANGE)
    d.badge(450, 820, 1, ORANGE)
    d.arrow([(678, 820), (1122, 820)], BLUE)
    d.badge(900, 820, 2, BLUE)

    d.note(1420, 640, 500, 300, "Key insight", [
        "Local and remote Kafka show the",
        "SAME latency pattern.",
        "A network fault would hit only the",
        "cross-VPC path → network is cleared.",
        "Bottleneck sits before the network hop:",
        "DMS SOURCE_CAPTURE, internal",
        "batching or the Kafka producer.",
    ], **PURPLE_NOTE)

    # --- Notes below
    d.note(20, 1050, 620, 160, "Ruled out", [
        "Storage BW: 12,540 exceeded → 0 after ACU 24",
        "No packet loss or retransmissions on the VPC path",
        "ParallelApplyBufferSize 100 → 500 → 1000: no change",
        "Same in local and remote Kafka (not network)",
        "Same in production with 2 tables (not table count)",
    ], size=13, align="start", **RED_NOTE)
    d.note(660, 1050, 640, 160, "Remaining suspects (DMS side)", [
        "Large transactions (5M+ rows) wait for COMMIT",
        "LOB handling: JSON columns, LobMaxSize 32 KB",
        "TARGET_APPLY batching (buffer 1000 may be too high)",
        "Kafka producer: batch.size / linger.ms / acks",
        "13 tasks share one replication instance",
    ], size=13, align="start", **PURPLE_NOTE)
    d.note(1320, 1050, 660, 160, "Timeline (relative days)", [
        "Day 1 · storage bandwidth saturation found",
        "Day 4 · ACU raised to 24, gain only temporary",
        "Day 6 · remote spikes to 140 s",
        "Day 7 · ParallelApplyBufferSize 500, no gain",
        "Day 8 · network cleared; 14 min local, 27 min remote",
        "Day 9 · same in production with 2 tables",
    ], color=MUTED, fill="#F7F9FC", title_color="#232F3E", size=13, align="start")

    d.legend([
        (ORANGE, 1, "SOURCE_CAPTURE · DMS reads the Aurora binlog"),
        (BLUE, 2, "TARGET_APPLY to Kafka in the same VPC"),
        (RED, 3, "TARGET_APPLY to Kafka in another VPC / account"),
    ], cols=3)
    d.save(HERE / "architecture")


def lab():
    d = Diagram(1900, 1185)

    d.group(20, 20, 1860, 900, "cloud", "AWS Cloud")
    d.group(50, 80, 1800, 820, "region", "Region eu-central-1")
    d.group(80, 140, 1740, 740, "account", "Lab account")
    d.group(110, 200, 1190, 660, "vpc", "Lab VPC · 10.0.0.0/16 · private only")
    d.group(140, 260, 560, 580, "private_subnet", "Subnet · 10.0.1.0/24 · eu-central-1a")
    d.group(720, 260, 560, 580, "private_subnet", "Subnet · 10.0.2.0/24 · eu-central-1b")

    d.node(DMS, 330, 450, ["DMS replication instance", "dms.t3.medium", "ENI 10.0.1.17"], pos="above")
    d.node(AURORA, 1000, 450, ["Aurora MySQL Serverless v2", "ACU 2–16 · 8.0 (3.08.2)",
                               "DB ledger · ENI 10.0.2.165"])

    d.band(160, 620, 1100, 190, "MSK Serverless · IAM auth · topic ledger-cdc", color="#8C4FFF")
    d.node(MSK, 250, 720, None, size=56)
    d.node("Amazon-VPC_Elastic-Network-Interface", 560, 715, "endpoint 10.0.1.204", size=44, label_size=14)
    d.node("Amazon-VPC_Elastic-Network-Interface", 1000, 715, "endpoint 10.0.2.208", size=44, label_size=14)

    d.arrow([(972, 450), (358, 450)], ORANGE)
    d.badge(660, 450, 1, ORANGE)
    d.text(660, 425, "binlog CDC · TCP 3306 · cross-AZ", 13, 600, ORANGE, bg=True)
    d.arrow([(330, 478), (330, 560), (560, 560), (560, 690)], BLUE)
    d.badge(380, 560, 2, BLUE)
    d.text(560, 546, "TARGET_APPLY · TCP 9098 · same AZ", 13, 600, BLUE, bg=True)

    d.box(1340, 230, 450, 130, "Security group net007-lab-sg",
          ["inbound: all from 10.0.0.0/16 · outbound: all", "covers DMS ↔ Aurora 3306, DMS ↔ Kafka 9098"],
          color="#DD344C", size=13)
    d.box(1340, 390, 450, 110, "Route table", ["10.0.0.0/16 → local", "no IGW · no NAT"], size=13)
    d.note(1340, 540, 450, 300, "Lab findings", [
        "MSK Serverless uses IAM auth; DMS needs",
        "SASL/SCRAM → use MSK provisioned",
        "DMS → S3 target needs an S3 gateway",
        "endpoint in a private VPC",
        "S3 target ignores ParallelApply settings",
        "Aurora → DMS cross-AZ adds ~1 ms:",
        "not the cause of the spikes",
    ], size=13, align="start", **RED_NOTE)

    d.note(20, 950, 1860, 120, "Lab test plan", [
        "1. Create ledger_entries in Aurora · 2. DMS source endpoint (Aurora) · 3. DMS target endpoint (Kafka) · 4. DMS task in CDC mode",
        "5. Insert 10K rows → measure CDCLatencyTarget · 6. Insert 1M rows in one transaction → reproduce the latency spike",
        "7. Tune ParallelApplyBufferSize and compare · 8. Check NetworkTransmitThroughput in CloudWatch",
    ], size=13, align="start", **PURPLE_NOTE)

    d.legend([
        (ORANGE, 1, "SOURCE_CAPTURE · Aurora 10.0.2.165:3306 → DMS 10.0.1.17"),
        (BLUE, 2, "TARGET_APPLY · DMS → MSK endpoint 10.0.1.204:9098"),
    ], cols=2)
    d.save(HERE / "architecture_lab")


case()
lab()
