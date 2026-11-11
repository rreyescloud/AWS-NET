# Diagrams

Scenario diagrams are hand-placed Python scripts rendered to SVG + PNG — no draw.io.

- `awsdiag.py` — the drawing library: AWS group containers, icons, numbered flows, notes, legend bar. Its docstring lists the visual conventions.
- `icons/` — the AWS Architecture Icons used so far, vendored so any diagram regenerates offline.
- `build_all.py` — regenerates every `services/**/diagram.py`.

## Requirements

- Python 3.9+
- `rsvg-convert` (`brew install librsvg`) for SVG → PNG

## Adding a diagram

1. Copy an existing `diagram.py` (for example `services/R53/scenarios/NET-013_*/diagram.py`) into the scenario folder.
2. Find icon names: `python3 tools/diagrams/awsdiag.py search route 53`.
   New icons come from the [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) package: extract it and point `AWS_ICON_PKG` at the folder; the library copies each icon it uses into `icons/`.
3. `python3 diagram.py`, open the PNG, adjust coordinates, repeat.

AWS Architecture Icons are provided by AWS for use in architecture diagrams under the terms on the page linked above.
