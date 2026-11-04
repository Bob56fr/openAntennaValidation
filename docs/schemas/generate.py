"""Generate the measurement-principle diagrams of docs/schemas/ from template.svg.

The layout is drawn by hand in template.svg; this script computes the curves (spectrum,
envelopes, histogram, confidence intervals), then writes the whole diagram
(principes-mesure.svg) and one file per section marked ``<!-- @section name y height -->``
in the template (shown one by one in docs/presentation.md and on the WordPress site).

Usage: python docs/schemas/generate.py
"""
import math
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
random.seed(7)


def path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)


# --- Section 1B: power envelopes (fading), two receivers, time-shifted -------------
def env(t: float) -> float:
    return (0.5 + 0.22 * math.sin(2 * math.pi * t / 190 + 0.4)
            + 0.14 * math.sin(2 * math.pi * t / 83 + 1.0))


X0, X1, SHIFT = 780, 1320, 14
env1 = [(x, 556 - 72 * env(x - X0)) for x in range(X0, X1 + 1, 3)]
env2 = [(x, 562 - 72 * env(x - X0 - SHIFT)) for x in range(X0 + SHIFT, X1 + 1, 3)]
# peak of env1 between 960 and 1120 to anchor the Δt marker
peak_x = min(range(960, 1120), key=lambda x: 556 - 72 * env(x - X0))
peak_y1 = 556 - 72 * env(peak_x - X0)
peak_y2 = 562 - 72 * env(peak_x - X0)

# --- Section 2: spectrum ----------------------------------------------------------
FLOOR = 912


def spectrum_y(x: float) -> float:
    s = 0.0
    s += 140 * math.exp(-((x - 250) / 3.2) ** 2)                        # CW
    if 410 <= x <= 430:                                                  # FT8
        s += 66 + 2 * math.sin(x)
    if 680 <= x <= 762:                                                  # SSB
        u = (x - 680) / 82
        s += (112 - 52 * u) * (1 - math.exp(-(x - 678) / 3)) * (1 - math.exp(-(764 - x) / 4)) \
            + 9 * math.sin(x / 2.3)
    s += 150 * math.exp(-((x - 1120) / 2.6) ** 2)                       # AM carrier
    d = abs(x - 1120)
    if 6 <= d <= 80:
        s += 58 * (1 - (d / 80) ** 1.6) + 5 * math.sin(x / 1.7)
    noise = random.gauss(0, 2.6)
    return FLOOR - s + noise


spec = [(x, spectrum_y(x)) for x in range(104, 1298, 2)]
spec_fill = path(spec) + f" L1296,950 L104,950 Z"

# --- Section 3B: histogram of B−A window medians -----------------------------------
HX0, HX1, HY = 770, 1050, 1440         # x range, baseline
dB_to_x = lambda d: 910 + d * 140      # 0 dB at 910, ±1 dB span
bias, sigma = 0.12, 0.18
bars = []
for i in range(-9, 10):
    c = i * 0.1
    h = 64 * math.exp(-0.5 * ((c - bias) / sigma) ** 2)
    if h < 1.5:
        continue
    x = dB_to_x(c) - 6.0
    bars.append(f'<rect x="{x:.1f}" y="{HY - h:.1f}" width="12" height="{h:.1f}" rx="1.5" class="hist"/>')
margin = abs(bias) + 2 * sigma

# --- Section 4: forest plot ------------------------------------------------------
FX = lambda d: 760 + d * 110
rows = [
    (1812, "Dipôle − verticale · nuit", "porteuses, 40 m", 1.6, 1.1, 2.1, "ok",
     "Écart significatif : +1,6 dB"),
    (1852, "Dipôle − verticale · jour", "porteuses, 40 m", 0.3, -0.5, 1.1, "no",
     "Pas d'écart : l'intervalle contient zéro"),
    (1892, "Dipôle − verticale · jour", "SSB, 40 m", 0.45, 0.25, 0.65, "no",
     "Pas d'écart : sous la marge"),
]
forest = []
for y, label, sub, med, lo, hi, kind, verdict in rows:
    forest.append(f'<text x="72" y="{y + 1}" class="t13 b">{label}</text>')
    forest.append(f'<text x="72" y="{y + 17}" class="t12 m">{sub}</text>')
    forest.append(f'<line x1="{FX(lo):.1f}" y1="{y - 4}" x2="{FX(hi):.1f}" y2="{y - 4}" class="ci"/>')
    for e in (lo, hi):
        forest.append(f'<line x1="{FX(e):.1f}" y1="{y - 10}" x2="{FX(e):.1f}" y2="{y + 2}" class="ci"/>')
    forest.append(f'<circle cx="{FX(med):.1f}" cy="{y - 4}" r="6" class="pt"/>')
    cls = "good b" if kind == "ok" else "m"
    forest.append(f'<text x="1110" y="{y + 1}" class="t13 {cls}">{verdict}</text>')
ticks = []
for d in range(-3, 4):
    ticks.append(f'<line x1="{FX(d):.0f}" y1="1912" x2="{FX(d):.0f}" y2="1917" class="axis"/>')
    lab = f"{d:+d}".replace("+0", "0").replace("-", "−")
    ticks.append(f'<text x="{FX(d):.0f}" y="1933" class="t12 m mid mono">{lab}</text>')

# --- Section 1A: switching timeline --------------------------------------------------
seg = []
for i in range(8):
    x = 80 + 70 * i
    who = "A" if i % 2 == 0 else "B"
    seg.append(f'<rect x="{x}" y="466" width="70" height="30" class="{"fa" if who == "A" else "fb"}"/>')
    seg.append(f'<rect x="{x}" y="466" width="17.5" height="30" fill="url(#hatch)"/>')
    seg.append(f'<text x="{x + 44}" y="486" class="t13 b mid onfill">{who}</text>')
for i in range(9):
    x = 80 + 70 * i
    seg.append(f'<line x1="{x}" y1="496" x2="{x}" y2="502" class="axis"/>')
    seg.append(f'<text x="{x}" y="516" class="t11 m mid mono">{20 * i}</text>')
for i in range(0, 8, 2):
    a, b = 80 + 70 * i + 44, 80 + 70 * (i + 1) + 44
    seg.append(f'<path d="M{a},526 v7 H{b} v-7" class="bracket"/>')

tpl = (HERE / "template.svg").read_text(encoding="utf-8")
svg = (tpl
       .replace("{{ENV1}}", path(env1))
       .replace("{{ENV2}}", path(env2))
       .replace("{{PEAKX}}", f"{peak_x}")
       .replace("{{PEAKX2}}", f"{peak_x + SHIFT}")
       .replace("{{PEAKY1}}", f"{peak_y1:.1f}")
       .replace("{{PEAKY2}}", f"{peak_y2:.1f}")
       .replace("{{SPEC}}", path(spec))
       .replace("{{SPECFILL}}", spec_fill)
       .replace("{{HIST}}", "\n    ".join(bars))
       .replace("{{BIASX}}", f"{dB_to_x(bias):.1f}")
       .replace("{{MARGL}}", f"{dB_to_x(bias - margin):.1f}")
       .replace("{{MARGR}}", f"{dB_to_x(bias + margin):.1f}")
       .replace("{{FOREST}}", "\n    ".join(forest))
       .replace("{{FTICKS}}", "\n    ".join(ticks))
       .replace("{{FZ0}}", f"{FX(0):.0f}")
       .replace("{{FML}}", f"{FX(-0.8):.0f}")
       .replace("{{FMW}}", f"{FX(0.8) - FX(-0.8):.0f}")
       .replace("{{SWITCH}}", "\n    ".join(seg)))


def write(name: str, text: str) -> None:
    (HERE / name).write_text(text, encoding="utf-8", newline="\n")


write("principes-mesure.svg", svg)

# One file per section: same style and definitions, the section only, cropped around it.
head = svg[:svg.index('  <rect class="bg"')]
SECTION = re.compile(r"  <!-- @section (\w+) (\d+) (\d+) -->\n(.*?)  <!-- @end -->", re.DOTALL)
for name, y, h, body in SECTION.findall(svg):
    # The section numbers belong to the whole diagram: dropped, title moved to the margin.
    body = re.sub(r'  <circle cx="62"[^\n]*class="numt">\d</text>\n', "", body)
    body = re.sub(r'<text x="86" (y="\d+" class="h2")', r'<text x="48" \1', body)
    part = (head.replace('viewBox="0 0 1400 2030" width="1400" height="2030"',
                         f'viewBox="24 {y} 1352 {h}" width="1352" height="{h}"')
            + f'  <rect class="bg" x="24" y="{y}" width="1352" height="{h}" rx="12"/>\n'
            + body + "</svg>\n")
    write(f"{name}.svg", part)
print("ok")
