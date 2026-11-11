"""awsdiag — hand-placed AWS architecture diagrams rendered to SVG + PNG.

Every scenario diagram in this repo is a small Python script that imports this
module, places groups / icons / arrows on an explicit pixel grid and calls
``Diagram.save()``. Output is ``<name>.svg`` + ``<name>.png`` (PNG via
``rsvg-convert``).

Visual conventions (match every diagram to these):
  * Containers follow the AWS Architecture Icons group style: AWS Cloud ->
    Region -> Account -> VPC -> Subnet, each with its group icon top-left.
  * Each numbered flow gets one colour, a numbered badge on the arrow, and an
    entry in the legend bar at the bottom.
  * Grey (#5A6B7D) arrows are supporting flows (logs, config, replication)
    and normally carry no badge.
  * No title inside the image; the scenario README provides it.

Icons are resolved by short name, e.g. ``Amazon-Route-53_Resolver``,
``AWS-WAF``, ``Amazon-VPC_NAT-Gateway``, ``Users``. Lookup order:
  1. ``tools/diagrams/icons/<name>.svg`` (vendored, committed)
  2. the AWS Architecture Icons package at $AWS_ICON_PKG (the extracted zip
     from https://aws.amazon.com/architecture/icons/). A hit is copied into
     ``icons/`` so the repo stays self-contained.

Find a name:  python tools/diagrams/awsdiag.py search "route 53"
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ICON_DIR = HERE / "icons"
ICON_PKG = Path(os.environ.get("AWS_ICON_PKG", "/tmp/awsicons_dl/x"))
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"

# ---------------------------------------------------------------- palette
INK = "#232F3E"        # default text
MUTED = "#5A6B7D"      # secondary text, supporting arrows
ORANGE = "#E07B00"
BLUE = "#1F6FB8"
GREEN = "#2E8B2E"
RED = "#D13212"
PURPLE = "#7B3FE4"
TEAL = "#00838F"
PINK = "#C7186E"
GREY = MUTED
FLOW_COLORS = [ORANGE, BLUE, GREEN, PURPLE, RED, TEAL, PINK]

# group kind -> (stroke, label colour, fill, dash, group icon)
GROUPS = {
    "cloud":          ("#232F3E", "#232F3E", "none",    None,  "AWS-Cloud_32"),
    "region":         ("#00A4A6", "#00A4A6", "none",    "8,6", "Region_32"),
    "account":        ("#E7157B", "#E7157B", "none",    None,  "AWS-Account_32"),
    "vpc":            ("#8C4FFF", "#8C4FFF", "none",    None,  "Virtual-private-cloud-VPC_32"),
    "private_subnet": ("#00A4A6", "#00A4A6", "#E6F6F7", None,  "Private-subnet_32"),
    "public_subnet":  ("#7AA116", "#5E7F0F", "#F2F6E8", None,  "Public-subnet_32"),
    "dc":             ("#7D8998", "#7D8998", "none",    None,  "Corporate-data-center_32"),
    "server":         ("#7D8998", "#7D8998", "none",    None,  "Server-contents_32"),
    "ec2":            ("#ED7100", "#ED7100", "none",    None,  "EC2-instance-contents_32"),
    "asg":            ("#ED7100", "#ED7100", "none",    "8,6", "Auto-Scaling-group_32"),
    "az":             ("#00A4A6", "#00A4A6", "none",    "8,6", None),
    "sg":             ("#DD344C", "#DD344C", "none",    None,  None),
    "generic":        ("#7D8998", "#5A6B7D", "none",    "8,6", None),
    "internet":       ("#7D8998", "#5A6B7D", "#F7F9FC", None,  None),
}


# ---------------------------------------------------------------- icons
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _stem(p: Path) -> str:
    s = p.stem
    s = re.sub(r"^(Arch|Res)_", "", s)
    s = re.sub(r"_(16|32|48|64)(_(Light|Dark))?$", "", s)
    s = re.sub(r"_(Light|Dark)$", "", s)
    return s


def _rank(p: Path) -> int:
    """Prefer 64px service icons, 48px light resource icons, 32px groups."""
    n = p.name
    r = 0
    if "_Dark" in n:
        r += 50
    if "@" in n:
        r += 100
    for size, cost in (("_64", 0), ("_48", 1), ("_32", 2), ("_16", 9)):
        if size in n:
            r += cost
    return r


_INDEX: dict[str, Path] | None = None


def _index() -> dict[str, Path]:
    global _INDEX
    if _INDEX is None:
        _INDEX = {}
        if ICON_PKG.exists():
            for p in sorted(ICON_PKG.rglob("*.svg"), key=_rank):
                if "__MACOSX" in p.parts:
                    continue
                for key in {_norm(_stem(p)), _norm(p.stem)}:
                    _INDEX.setdefault(key, p)
    return _INDEX


def icon_file(name: str) -> Path:
    local = ICON_DIR / f"{name}.svg"
    if local.exists():
        return local
    hit = _index().get(_norm(name))
    if not hit:
        raise SystemExit(f"icon '{name}' not found. Try: python {__file__} search <words>")
    ICON_DIR.mkdir(exist_ok=True)
    shutil.copy(hit, local)
    return local


def search(words: str) -> list[str]:
    toks = [_norm(w) for w in words.split()]
    names = sorted({_stem(p) for p in _index().values()})
    return [n for n in names if all(t in _norm(n) for t in toks)]


# ---------------------------------------------------------------- diagram
class Diagram:
    def __init__(self, width: int, height: int):
        self.W, self.H = width, height
        self.body: list[str] = []
        self.colors: set[str] = {MUTED}
        self._uid = 0

    # -- primitives -------------------------------------------------------
    def raw(self, svg: str) -> None:
        self.body.append(svg)

    def text(self, x, y, lines, size=15, weight=400, color=INK, anchor="middle",
             italic=False, bg=False):
        """Multi-line text; ``y`` is the baseline of the first line."""
        lines = lines if isinstance(lines, (list, tuple)) else [lines]
        lh = int(size * 1.3)
        if bg:
            w = max(len(str(l)) for l in lines) * size * 0.56 + 10
            h = lh * len(lines) + 4
            x0 = {"middle": x - w / 2, "start": x - 5, "end": x - w + 5}[anchor]
            self.raw(f'<rect x="{x0:.0f}" y="{y - size - 2:.0f}" width="{w:.0f}" height="{h:.0f}" '
                     f'rx="4" fill="#FFFFFF" fill-opacity="0.92"/>')
        st = ' font-style="italic"' if italic else ""
        for i, l in enumerate(lines):
            self.raw(f'<text x="{x}" y="{y + i * lh}" text-anchor="{anchor}" font-size="{size}" '
                     f'font-weight="{weight}"{st} fill="{color}">{escape(str(l))}</text>')

    def icon(self, name, x, y, size=56):
        src = icon_file(name).read_text(encoding="utf-8")
        src = re.sub(r"<\?xml[^>]*\?>", "", src)
        vb = re.search(r'viewBox="([^"]+)"', src)
        vb = vb.group(1) if vb else "0 0 64 64"
        inner = re.sub(r"^.*?<svg[^>]*>", "", src, count=1, flags=re.S).rsplit("</svg>", 1)[0]
        self._uid += 1
        tag = f"i{self._uid}"
        inner = re.sub(r'id="([^"]+)"', lambda m: f'id="{tag}-{m.group(1)}"', inner)
        inner = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{tag}-{m.group(1)})", inner)
        inner = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{tag}-{m.group(1)}"', inner)
        self.raw(f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="{vb}">{inner}</svg>')

    # -- building blocks --------------------------------------------------
    def group(self, x, y, w, h, kind, label, icon=True):
        stroke, lcolor, fill, dash, gicon = GROUPS[kind]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="2"{d}/>')
        if gicon and icon:
            self.icon(gicon, x, y, 40)
            self.text(x + 50, y + 26, label, 18, 600, lcolor, "start")
        else:
            self.text(x + 12, y + 26, label, 18, 600, lcolor, "start")

    def node(self, icon, cx, cy, label=None, size=56, pos="below", weight=400,
             color=INK, label_size=15):
        """Icon centred on (cx, cy) with a label below / above / left / right."""
        self.icon(icon, cx - size / 2, cy - size / 2, size)
        if not label:
            return
        lines = label if isinstance(label, (list, tuple)) else [label]
        lh = int(label_size * 1.3)
        if pos == "below":
            self.text(cx, cy + size / 2 + 22, lines, label_size, weight, color)
        elif pos == "above":
            self.text(cx, cy - size / 2 - 12 - lh * (len(lines) - 1), lines, label_size, weight, color)
        elif pos == "right":
            self.text(cx + size / 2 + 12, cy + 5 - lh * (len(lines) - 1) / 2, lines, label_size, weight, color, "start")
        elif pos == "left":
            self.text(cx - size / 2 - 12, cy + 5 - lh * (len(lines) - 1) / 2, lines, label_size, weight, color, "end")

    def band(self, x, y, w, h, label, color="#8C4FFF", label_color="#6A35D9", fill_opacity=0.07):
        """Dashed rounded band (e.g. Resolver endpoint, rule group, firewall endpoint set)."""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{color}" '
                 f'fill-opacity="{fill_opacity}" stroke="{color}" stroke-width="2" stroke-dasharray="6,4"/>')
        if label:
            self.text(x + 14, y + 22, label, 16, 700, label_color, "start")

    def note(self, x, y, w, h, title, lines=(), color="#E7157B", fill="#FDF1F7",
             title_color="#B0105C", size=14, align="middle"):
        """Dashed callout box: a title plus short lines (findings, what an account receives...)."""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
                 f'stroke="{color}" stroke-width="1.5" stroke-dasharray="5,4"/>')
        tx = x + w / 2 if align == "middle" else x + 16
        self.text(tx, y + 30, title, size + 1, 700, title_color, align)
        if lines:
            self.text(tx, y + 58, list(lines), size, 400, INK, align)

    def box(self, x, y, w, h, title, lines=(), color="#7D8998", fill="#FFFFFF",
            title_color=INK, size=14, rx=8, dash=None, align="middle"):
        """Solid rounded box for non-AWS or logical items (rule, request, response...)."""
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                 f'stroke="{color}" stroke-width="2"{d}/>')
        n = 1 + len(lines)
        lh = int(size * 1.3)
        ty = y + h / 2 - (n - 1) * lh / 2 + size * 0.35
        tx = x + w / 2 if align == "middle" else x + 14
        self.text(tx, ty, title, size + 1, 700, title_color, align)
        if lines:
            self.text(tx, ty + lh + 1, list(lines), size, 400, INK, align)

    def pill(self, x, y, w, h, icon=None, label=None, vertical=True):
        """Connector pill, e.g. VPN / Direct Connect / Internet between groups."""
        r = min(w, h) / 2
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="#F2F3F3" '
                 f'stroke="#7D8998" stroke-width="2" stroke-dasharray="6,4"/>')
        if icon:
            self.icon(icon, x + w / 2 - 20, y + h / 2 - 20, 40)
        if label:
            lines = label if isinstance(label, (list, tuple)) else [label]
            if vertical:
                self.text(x + w / 2, y + h + 30, lines, 15, 600, MUTED)
            else:
                self.text(x + w / 2, y - 12 - 19 * (len(lines) - 1), lines, 15, 600, MUTED)

    def arrow(self, pts, color=MUTED, dash=False, both=False, width=3.5, label=None,
              label_at=None, label_color=None):
        """Orthogonal/polyline arrow through ``pts``; ``label`` sits at ``label_at`` (x, y)."""
        self.colors.add(color)
        mid = self._mid(color)
        d = " ".join(("M" if i == 0 else "L") + f"{x},{y}" for i, (x, y) in enumerate(pts))
        da = ' stroke-dasharray="9,6"' if dash is True else (f' stroke-dasharray="{dash}"' if dash else "")
        ms = f' marker-start="url(#s{mid})"' if both else ""
        self.raw(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
                 f'stroke-linejoin="round"{da}{ms} marker-end="url(#e{mid})"/>')
        if label:
            lx, ly = label_at or ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2 - 8)
            self.text(lx, ly, label, 14, 600, label_color or color, bg=True)

    def line(self, pts, color=MUTED, dash=False, width=2):
        d = " ".join(("M" if i == 0 else "L") + f"{x},{y}" for i, (x, y) in enumerate(pts))
        da = ' stroke-dasharray="6,4"' if dash else ""
        self.raw(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{da}/>')

    def badge(self, x, y, n, color):
        self.raw(f'<circle cx="{x}" cy="{y}" r="16" fill="{color}" stroke="#FFFFFF" stroke-width="2.5"/>')
        self.text(x, y + 6, str(n), 17, 700, "#FFFFFF")

    def mark(self, x, y, ok: bool):
        """Small green check / red cross status marker."""
        c = GREEN if ok else RED
        self.raw(f'<circle cx="{x}" cy="{y}" r="13" fill="{c}" stroke="#FFFFFF" stroke-width="2"/>')
        if ok:
            self.raw(f'<path d="M{x-6},{y} L{x-2},{y+5} L{x+6},{y-5}" fill="none" stroke="#FFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
        else:
            self.raw(f'<path d="M{x-5},{y-5} L{x+5},{y+5} M{x+5},{y-5} L{x-5},{y+5}" fill="none" stroke="#FFF" stroke-width="3" stroke-linecap="round"/>')

    def legend(self, items, y=None, x=20, w=None, cols=None, row_h=40):
        """Legend bar. Each item: (color, badge_number_or_None, text[, 'dash'])."""
        w = w or self.W - 40
        cols = cols or min(len(items), 3)
        rows = (len(items) + cols - 1) // cols
        h = rows * row_h + 32
        y = y if y is not None else self.H - h - 20
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#F7F9FC" stroke="#D5DBE3"/>')
        cw = (w - 40) / cols
        for i, it in enumerate(items):
            color, n, label = it[0], it[1], it[2]
            dash = len(it) > 3 and it[3] == "dash"
            cx = x + 40 + (i % cols) * cw
            cy = y + 16 + row_h / 2 + (i // cols) * row_h
            if n is not None:
                self.badge(cx, cy, n, color)
                self.text(cx + 28, cy + 6, label, 16, 400, INK, "start")
            else:
                self.colors.add(color)
                da = ' stroke-dasharray="9,6"' if dash else ""
                self.raw(f'<path d="M{cx-18},{cy} L{cx+22},{cy}" stroke="{color}" stroke-width="3.5"{da} '
                         f'marker-end="url(#e{self._mid(color)})"/>')
                self.text(cx + 36, cy + 6, label, 16, 400, INK, "start")
        return y

    # -- output ------------------------------------------------------------
    @staticmethod
    def _mid(color: str) -> str:
        return color.lstrip("#")

    def svg(self) -> str:
        markers = "".join(
            f'<marker id="e{self._mid(c)}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="4.2" '
            f'markerHeight="4.2" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
            f'<marker id="s{self._mid(c)}" viewBox="0 0 10 10" refX="1.5" refY="5" markerWidth="4.2" '
            f'markerHeight="4.2" orient="auto"><path d="M10,0 L0,5 L10,10 z" fill="{c}"/></marker>'
            for c in sorted(self.colors))
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                f'width="{self.W}" height="{self.H}" viewBox="0 0 {self.W} {self.H}" font-family="{FONT}">'
                f'<defs>{markers}</defs><rect width="{self.W}" height="{self.H}" fill="#FFFFFF"/>')
        return head + "\n" + "\n".join(self.body) + "\n</svg>\n"

    def save(self, base) -> Path:
        """Write ``<base>.svg`` and ``<base>.png``. ``base`` is a path without extension."""
        base = Path(base)
        svg = base.with_suffix(".svg")
        png = base.with_suffix(".png")
        svg.write_text(self.svg(), encoding="utf-8")
        subprocess.run(["rsvg-convert", "-f", "png", "-o", str(png), str(svg)], check=True)
        print(f"wrote {png}")
        return png


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "search":
        for n in search(" ".join(sys.argv[2:])):
            print(n)
    else:
        print(__doc__)
