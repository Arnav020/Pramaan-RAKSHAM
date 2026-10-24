"""Engraved guilloché patterns, generated in code (the security-print look of banknotes).

Hypotrochoid rosettes and wave bands as SVG paths; no images, no AI art.
"""

from __future__ import annotations

import math


def rosette(cx: float, cy: float, R: float, r: float, d: float, turns: int = 1, steps: int = 2400,
            stroke: str = "#B5763A", width: float = 0.6, opacity: float = 0.35) -> str:
    pts = []
    k = (R - r) / r
    for i in range(steps + 1):
        t = 2 * math.pi * turns * i / steps
        x = cx + (R - r) * math.cos(t) + d * math.cos(k * t)
        y = cy + (R - r) * math.sin(t) - d * math.sin(k * t)
        pts.append(f"{x:.1f},{y:.1f}")
    return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{stroke}" stroke-width="{width}" opacity="{opacity}"/>'


def rosette_stack(cx: float, cy: float, size: float, stroke: str = "#B5763A", opacity: float = 0.28) -> str:
    """Several nested rosettes, like the seal on a currency note."""
    parts = []
    for j, (R, r, d) in enumerate(((size, size * 0.27, size * 0.55), (size * 0.82, size * 0.19, size * 0.47), (size * 0.62, size * 0.143, size * 0.33))):
        parts.append(rosette(cx, cy, R, r, d, turns=7 if j == 0 else 4, stroke=stroke, opacity=opacity - j * 0.05))
    return "".join(parts)


def wave_band(x0: float, y0: float, width: float, height: float, lines: int = 9, periods: float = 14,
              stroke: str = "#33504A", opacity: float = 0.22) -> str:
    out = []
    for n in range(lines):
        phase = n * math.pi / lines
        amp = height / 2 * (0.55 + 0.45 * math.sin(n / lines * math.pi))
        pts = []
        for i in range(0, 401):
            x = x0 + width * i / 400
            y = y0 + height / 2 + amp * math.sin(2 * math.pi * periods * i / 400 + phase)
            pts.append(f"{x:.1f},{y:.1f}")
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{stroke}" stroke-width="0.7" opacity="{opacity}"/>')
    return "".join(out)


def card_background_svg(w: int, h: int, accent: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        + rosette_stack(w * 0.80, h * 0.30, w * 0.30, stroke="#B5763A", opacity=0.22)
        + rosette_stack(w * 0.16, h * 0.86, w * 0.20, stroke="#33504A", opacity=0.16)
        + wave_band(0, 18, w, 40, stroke=accent, opacity=0.30)
        + wave_band(0, h - 58, w, 40, stroke=accent, opacity=0.30)
        + "</svg>"
    )
