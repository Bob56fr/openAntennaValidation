"""Diagrams of the presentation page: present, valid, up to date, accepted by the plugin."""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "wordpress"))

from build import IMAGE_PATH, presentation_images  # noqa: E402

SCHEMAS = ROOT / "docs" / "schemas"


def test_presentation_images_exist_and_are_accepted_by_the_plugin() -> None:
    images = presentation_images()
    assert images, "docs/presentation.md ne contient aucune image"
    for image in images:
        assert IMAGE_PATH.match(image), image
        path = ROOT / "docs" / image
        assert path.is_file(), image
        if path.suffix == ".svg":
            ET.parse(path)
            # The plugin forces the theme by rewriting exactly this media query.
            assert "@media (prefers-color-scheme: dark)" in path.read_text(encoding="utf-8")


def test_diagrams_are_up_to_date(tmp_path: Path) -> None:
    for name in ("generate.py", "template.svg"):
        shutil.copy2(SCHEMAS / name, tmp_path / name)
    subprocess.run([sys.executable, str(tmp_path / "generate.py")], check=True,
                   capture_output=True)
    produced = sorted(p.name for p in tmp_path.glob("*.svg") if p.name != "template.svg")
    assert produced == sorted(p.name for p in SCHEMAS.glob("*.svg") if p.name != "template.svg")
    for name in produced:
        assert filecmp.cmp(tmp_path / name, SCHEMAS / name, shallow=False), \
            f"{name} : relancer python docs/schemas/generate.py"
