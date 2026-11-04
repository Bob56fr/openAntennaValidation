from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from open_antenna_validation import installation as inst
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.campaign_manager import CampaignError
from open_antenna_validation.config import (
    CONFIG_FILENAME,
    SimulatedConfig,
    load_config,
    save_config,
)
from open_antenna_validation.detect import classify, detect, same_signal, select, survey
from open_antenna_validation.hardware.simulated import SimulatedDriver
from open_antenna_validation.service import Station

CENTER = 7.1e6


@pytest.fixture(scope="module")
def surveyed() -> dict[str, object]:
    driver = SimulatedDriver(SimulatedConfig(realtime=False))
    driver.set_sample_rate(250_000)
    driver.open()
    raw = survey(driver, 20.0, 4096)
    return detect(raw["grams"], raw["rate"], 4096, raw["sdr_hz"], low_hz=7.0e6, high_hz=7.2e6,
                  usable_hz=200e3)


def test_detection_finds_and_classifies_the_simulated_air(surveyed: dict[str, object]) -> None:
    found = {(round(c["freq_hz"] / 1e3), c["class"]) for c in surveyed["candidates"]}  # type: ignore[attr-defined]
    # Carrier (steady, narrow), FT8 (narrow, every other slot), two voices (about 2 kHz).
    assert found == {(7038, "carrier"), (7074, "digital"), (7091, "ssb"), (7146, "ssb")}
    first = surveyed["candidates"][0]  # type: ignore[index]
    assert first["class"] == "carrier"  # constant envelope first
    digital = next(c for c in surveyed["candidates"] if c["class"] == "digital")  # type: ignore[attr-defined]
    assert 0.2 < digital["duty"] < 0.9


def test_quiet_point_avoids_signals_and_dc(surveyed: dict[str, object]) -> None:
    quiet = surveyed["quiet"]
    assert quiet is not None and quiet["class"] == "noise"  # type: ignore[index]
    f = quiet["freq_hz"]  # type: ignore[index]
    assert abs(f - CENTER) > 3_000
    for c in surveyed["candidates"]:  # type: ignore[attr-defined]
        assert abs(f - c["freq_hz"]) > (c["width_hz"] + 2_700) / 2


@pytest.mark.parametrize(
    ("width", "duty", "spread", "expected"),
    [(150, 1.0, 1.0, "carrier"), (150, 0.5, 1.0, "digital"), (150, 1.0, 6.0, "digital"),
     (900, 1.0, 1.0, "digital"), (2_400, 0.7, 5.0, "ssb"), (8_000, 1.0, 2.0, "am_fm"),
     (20_000, 1.0, 1.0, None)],
)
def test_classify(width: float, duty: float, spread: float, expected: str | None) -> None:
    assert classify(width, duty, spread) == expected


def test_select_spacing_and_same_signal() -> None:
    cands = [{"freq_hz": 7.050e6, "class": "carrier", "width_hz": 200.0},
             {"freq_hz": 7.0503e6, "class": "carrier", "width_hz": 200.0},
             {"freq_hz": 7.060e6, "class": "ssb", "width_hz": 2700.0}]
    assert [c["freq_hz"] for c in select(cands, 5)] == [7.050e6, 7.060e6]
    assert select(cands, 5, already=[cands[2]]) == [cands[0]]
    assert same_signal(cands[0], {"freq_hz": 7.05005e6, "class": "carrier", "width_hz": 200.0})
    assert not same_signal(cands[0], {"freq_hz": 7.05005e6, "class": "ssb", "width_hz": 200.0})


def _station(home: Path, **campaign: float) -> Station:
    path = home / CONFIG_FILENAME
    config = load_config(path)
    for key, value in campaign.items():
        setattr(config.campaign, key, value)
    save_config(config, path)
    s = Station.from_home(home, inline=True)
    s.startup()
    assert wait_for(lambda: s.receiver.get("state") == "running", 5)
    return s


def test_automatic_campaign_surveys_measures_and_reevaluates(station_home: Path) -> None:
    s = _station(station_home, survey_s=10.0, reevaluate_s=30.0)
    try:
        a = inst.save_antenna(s.db, {"type": "dipole", "name": "A"})
        b = inst.save_antenna(s.db, {"type": "vertical_quarter", "name": "B"})
        with pytest.raises(CampaignError, match="bande utile"):
            s.start_campaign({"mode": "auto", "low_hz": 7.0e6, "high_hz": 7.3e6,
                              "channels": [{"antenna_id": a}, {"antenna_id": b}],
                              "duration_s": 600})
        uuid = s.start_campaign({"mode": "auto", "low_hz": 7.0e6, "high_hz": 7.2e6 - 10e3,
                                 "channels": [{"antenna_id": a}, {"antenna_id": b}],
                                 "duration_s": 90})
        assert wait_for(lambda: s.campaign is None, 120)
        cid, mode, state = s.db.query(
            "SELECT id, measure_mode, state FROM campaigns WHERE uuid = ?", (uuid,))[0]
        assert (mode, state) == ("auto", "done")
        signals = s.db.query("SELECT class, source FROM signals WHERE campaign_id = ?", (cid,))
        classes = sorted(r["class"] for r in signals)
        assert classes.count("noise") >= 1 and "carrier" in classes and "ssb" in classes
        assert {r["source"] for r in signals} == {"auto"}
        surveys = s.db.query("SELECT COUNT(*) FROM events WHERE type = 'survey'")[0][0]
        assert surveys >= 2  # initial survey and at least one re-evaluation
        # The noise point measures the floor of both antennas all along.
        floors = s.db.query(
            "SELECT COUNT(*) FROM measurements m JOIN signals g ON g.id = m.signal_id"
            " WHERE m.campaign_id = ? AND g.class = 'noise' AND m.floor_dbfs IS NOT NULL",
            (cid,))[0][0]
        assert floors > 0
    finally:
        s.shutdown()
        s.db.close()


def test_calm_band_slows_the_measurements(station_home: Path) -> None:
    path = station_home / CONFIG_FILENAME
    config = load_config(path)
    config.hardware.simulated.signals = []  # nothing on the air: the band is calm
    save_config(config, path)
    s = _station(station_home, calm_after_s=10.0, calm_period_s=30.0)
    try:
        a = inst.save_antenna(s.db, {"type": "dipole", "name": "A"})
        b = inst.save_antenna(s.db, {"type": "dipole", "name": "B"})
        uuid = s.start_campaign({"frequencies": [{"freq_hz": 7.05e6}],
                                 "channels": [{"antenna_id": a}, {"antenna_id": b}],
                                 "duration_s": 240})
        assert wait_for(lambda: s.campaign is None, 120)
        cid = s.db.query("SELECT id FROM campaigns WHERE uuid = ?", (uuid,))[0][0]
        states = [r[0] for r in s.db.query(
            "SELECT state FROM activity WHERE campaign_id = ? ORDER BY id", (cid,))]
        assert "calm" in states
        ts = [r[0] for r in s.db.query(
            "SELECT DISTINCT ts_ms FROM measurements WHERE campaign_id = ? ORDER BY ts_ms",
            (cid,))]
        gaps = [b - a for a, b in itertools.pairwise(ts)]
        assert min(gaps) < 15_000 and max(gaps) >= 29_000  # normal, then the calm period
    finally:
        s.shutdown()
        s.db.close()
