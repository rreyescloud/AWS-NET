"""Regenerate every scenario diagram: runs each services/**/diagram.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
failed = []
for script in sorted(ROOT.glob("services/**/diagram.py")):
    r = subprocess.run([sys.executable, str(script)], cwd=script.parent)
    if r.returncode:
        failed.append(script.relative_to(ROOT))
if failed:
    sys.exit("failed: " + ", ".join(map(str, failed)))
