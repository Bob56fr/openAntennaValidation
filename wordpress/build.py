"""Build the WordPress plugin from the station's own files, then its zip.

One source for both result pages: the plugin takes results.js, stats.js, the campaign view of
campaign.html, the design system (style.css, results.css, scoped to .oav-root so that it
cannot leak into the site's theme) and the vendored ECharts and fonts. The demonstration
campaigns are produced by the station code (demo.py) in the exchange format.

Usage: python wordpress/build.py [--zip]   (zip in wordpress/dist/)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB = ROOT / "web"
PLUGIN = HERE / "open-antenna-validation"
SCOPE = ".oav-root"


# --- CSS scoping ------------------------------------------------------------------------

def _split_top(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside parentheses and brackets."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def _scope_selector(sel: str) -> str:
    sel = sel.strip()
    if sel in ("html", "body"):
        return SCOPE
    if sel.startswith(":root"):
        return SCOPE + sel[len(":root"):]
    return f"{SCOPE} {sel}"


def scope_css(css: str) -> str:
    """Prefix every rule with .oav-root (recursing into @media); @font-face kept."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out = []
    i = 0
    while i < len(css):
        brace = css.find("{", i)
        if brace < 0:
            break
        head = css[i:brace].strip()
        # Find the matching closing brace.
        depth, j = 1, brace + 1
        while depth and j < len(css):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[brace + 1:j - 1]
        if head.startswith("@media") or head.startswith("@supports"):
            out.append(f"{head} {{\n{scope_css(body)}}}\n")
        elif head.startswith("@"):
            out.append(f"{head} {{{body}}}\n")
        else:
            selectors = ", ".join(_scope_selector(s) for s in _split_top(head, ","))
            out.append(f"{selectors} {{{body}}}\n")
        i = j
    return "".join(out)


# --- pieces -------------------------------------------------------------------------------

def campaign_view() -> str:
    html = (WEB / "campaign.html").read_text(encoding="utf-8")
    m = re.search(r"<!-- oav:view:start[^>]*-->(.*)<!-- oav:view:end -->", html, re.DOTALL)
    if not m:
        raise SystemExit("campaign.html : repères oav:view introuvables")
    return m.group(1).strip() + "\n"


def demos(out: Path) -> list[dict[str, object]]:
    sys.path.insert(0, str(ROOT / "src"))
    from open_antenna_validation.campaigns import campaign_results, list_campaigns
    from open_antenna_validation.demo import ensure_demo_campaigns
    from open_antenna_validation.storage import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "demo.sqlite3")
        db.migrate()
        ensure_demo_campaigns(db)
        items = list_campaigns(db)
        for item in items:
            results = campaign_results(db, str(item["uuid"]))
            (out / f"{item['uuid']}.json").write_text(
                json.dumps(results, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        db.close()
    return items


IMAGE = re.compile(r"^!\[[^\]]*\]\(([^)\s]+)\)$", re.MULTILINE)
IMAGE_PATH = re.compile(r"^[a-z0-9_-]+(?:/[a-z0-9_-]+)*\.(?:svg|png)$")  # as in the plugin


def presentation_images() -> list[str]:
    """Images of docs/presentation.md (lines ``![text](path)``), paths relative to docs/."""
    text = (ROOT / "docs" / "presentation.md").read_text(encoding="utf-8")
    return IMAGE.findall(text)


def build() -> Path:
    assets = PLUGIN / "assets"
    shared, vendor, demo = assets / "shared", assets / "vendor", assets / "demo"
    for d in (shared, vendor, demo):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    for name in ("results.js", "stats.js"):
        shutil.copy2(WEB / name, shared / name)
    styles = (WEB / "style.css").read_text(encoding="utf-8") + "\n" \
        + (WEB / "results.css").read_text(encoding="utf-8")
    styles = styles.replace('url("vendor/fonts/', 'url("../vendor/fonts/')
    (shared / "oav-design.css").write_text(
        "/* Généré par wordpress/build.py depuis web/style.css et web/results.css. */\n"
        + scope_css(styles), encoding="utf-8")
    shutil.copy2(WEB / "vendor" / "echarts.min.js", vendor / "echarts.min.js")
    shutil.copytree(WEB / "vendor" / "fonts", vendor / "fonts")
    templates = PLUGIN / "templates"
    templates.mkdir(exist_ok=True)
    (templates / "campaign-view.html").write_text(campaign_view(), encoding="utf-8")
    # Copy of the presentation shipped with the plugin, for when GitHub does not answer.
    shutil.copy2(ROOT / "docs" / "presentation.md", assets / "presentation.md")
    docs = assets / "docs"
    if docs.exists():
        shutil.rmtree(docs)
    for image in presentation_images():
        (docs / image).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "docs" / image, docs / image)
    items = demos(demo)
    from open_antenna_validation.installation import ANTENNA_TYPES
    (assets / "antenna-types.json").write_text(json.dumps(ANTENNA_TYPES, ensure_ascii=False),
                                               encoding="utf-8")
    (demo / "index.json").write_text(json.dumps(items, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    return PLUGIN


def make_zip() -> Path:
    header = (PLUGIN / "open-antenna-validation.php").read_text(encoding="utf-8")
    version = re.search(r"^\s*\*\s*Version:\s*(\S+)", header, re.MULTILINE)
    out = HERE / "dist" / f"open-antenna-validation-{version.group(1) if version else 'dev'}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN.rglob("*")):
            if path.is_file():
                archive.write(path, Path("open-antenna-validation") / path.relative_to(PLUGIN))
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", action="store_true")
    args = parser.parse_args()
    print(build())
    if args.zip:
        print(make_zip())
