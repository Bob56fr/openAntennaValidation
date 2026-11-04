"""Statistics of the result pages (web/stats.js), run with Node when it is installed."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parent / "js" / "stats_check.js"


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js absent")
    done = subprocess.run([node, str(SCRIPT)], capture_output=True, text=True, check=True,
                          timeout=60)
    result: dict[str, Any] = json.loads(done.stdout)
    return result


def test_independent_windows(out: dict[str, Any]) -> None:
    iid = out["iid"]
    assert iid["n"] == 400 and iid["lo"] < 0 < iid["hi"]
    assert iid["p"] > 0.05
    # Without correlation, blocks stay short and the interval close to the classical one
    # (somewhat wider: minimal block length n^(1/3), discrete median; prudent, as wanted).
    assert iid["block"] <= 10
    assert 0.9 * out["iidNaive"] < iid["ci"] < 1.6 * out["iidNaive"]


def test_correlated_windows_widen_the_interval(out: dict[str, Any]) -> None:
    ar = out["ar"]
    assert ar["block"] >= 10  # follows the autocorrelation
    assert ar["ci"] > 2 * out["arNaive"]  # a naive interval would be far too narrow
    assert out["again"] == ar  # reproducible


def test_clear_offset_and_edge_cases(out: dict[str, Any]) -> None:
    clear = out["clear"]
    assert clear["lo"] > 2.5 and clear["p"] == 0
    assert out["withNulls"]["n"] == 3 and out["withNulls"]["med"] == 2
    assert out["empty"] is None


def test_holm(out: dict[str, Any]) -> None:
    assert out["holm"] == [True, False, False, True]
    assert out["holmNone"] == [False, False]
