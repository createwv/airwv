"""Split the Empower WV clouds banner SVG into parallax layers.

The banner is one sky gradient rect + three cloud paths (gold/blue/white). This
writes each as its own SVG so the dashboard can keep the sky still and pan the
cloud layers at different speeds. Re-run if the source banner changes.

    python scripts/split_banner.py
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).parent.parent / "src/airwv/web/static"
svg = (STATIC / "banner.svg").read_text()

view_box = re.search(r'viewBox="([^"]+)"', svg).group(1)
gradient = re.search(r"(<linearGradient.*?</linearGradient>)", svg, re.S).group(1)
rect = re.search(r'<rect class="cls-1"[^>]*/>', svg).group(0)
rw = re.search(r'width="([\d.]+)"', rect).group(1)
rh = re.search(r'height="([\d.]+)"', rect).group(1)
paths = dict(re.findall(r'<path class="(cls-[234])" d="([^"]*)"', svg))

FILLS = {"cls-3": ("gold", "#f2da9e"), "cls-4": ("blue", "#d7edf4"), "cls-2": ("white", "#ffffff")}


def wrap(inner: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
            f'preserveAspectRatio="xMidYMax meet">{inner}</svg>')


(STATIC / "banner-sky.svg").write_text(
    wrap(f'<defs>{gradient}</defs><rect width="{rw}" height="{rh}" fill="url(#linear-gradient)"/>')
)
for cls, (name, fill) in FILLS.items():
    (STATIC / f"banner-clouds-{name}.svg").write_text(wrap(f'<path d="{paths[cls]}" fill="{fill}"/>'))

print(f"wrote banner-sky.svg + {len(FILLS)} cloud layers (viewBox {view_box})")
