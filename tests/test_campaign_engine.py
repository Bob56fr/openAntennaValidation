from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.config import (
    CONFIG_FILENAME,
    SimulatedConfig,
    SimulatedSignal,
    load_config,
    save_config,
)
from open_antenna_validation.engine import CampaignRunner
from open_antenna_validation.hardware.simulated import SimulatedDriver
from open_antenna_validation.service import Station
from open_antenna_validation.timeutil import now_ms
from open_antenna_validation.web import app as web_app
from open_antenna_validation.web import create_app
from tests.conftest import PASSWORD

CENTER = 7.1e6
# The default simulated air: carrier, FT8, two voices, around 7.1 MHz.
FREQUENCIES = [
    {"freq_hz": CENTER - 62_000, "class": "carrier"},
    {"freq_hz": CENTER - 26_000, "class": "digital"},
    {"freq_hz": CENTER - 10_000, "class": "ssb"},
]


def test_runner_measures_each_input_after_blanking() -> None:
    carrier = SimulatedSignal(offset_hz=-62_000.0, kind="carrier", dbfs=-62.0)
    driver = SimulatedDriver(SimulatedConfig(realtime=False, signals=[carrier], fading_db=0.0,
                                             antenna_b_signal_db=-4.0, antenna_b_noise_db=1.5))
    driver.set_sample_rate(250_000)
    driver.open()
    spec = {"campaign": "x", "inputs": ["A", "B"], "rate_hz": 25.0, "blanking_ms": 2.5,
            "fft_size": 4096, "period_s": 2.0, "clip_threshold": 0.99, "clip_alert_ratio": 0.001,
            "center_hz": CENTER, "display_points": 1024,
            "signals": [{"id": 7, "offset_hz": -62_000.0, "width_hz": 200.0, "class": "carrier"}]}
    runner = CampaignRunner(driver, spec, start_ms=1_000)
    assert runner.dwell == 5000 and runner.blank == 625 and runner.fft == 4096
    events: list[dict[str, object]] = []
    while not events:
        events = runner.cycle()
    m = events[0]
    assert m["type"] == "measurement" and m["ts_ms"] == 1_000 and m["period_ms"] == 2000
    values = {(v[0], v[1]): v for v in m["values"]}  # type: ignore[attr-defined]
    a, b = values[(7, 0)], values[(7, 1)]
    assert b[2] - a[2] == pytest.approx(-4.0, abs=0.3)  # RSSI
    assert b[3] - a[3] == pytest.approx(1.5, abs=0.4)  # local noise floor
    assert {e["antenna"] for e in events[1:]} == {"A", "B"}  # one spectrum per input


@pytest.fixture
def client(station_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    path = station_home / CONFIG_FILENAME
    config = load_config(path)
    config.hardware.simulated.antenna_b_signal_db = -3.0
    config.campaign.calm_after_s = 30.0
    save_config(config, path)
    monkeypatch.setattr(web_app, "FAILED_LOGIN_DELAY_S", 0.0)
    station = Station.from_home(station_home, inline=True)
    station.startup()
    assert wait_for(lambda: station.receiver.get("state") == "running", 5)
    with TestClient(create_app(station, run_lifecycle=False)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        c.station = station  # type: ignore[attr-defined]
        yield c
    station.shutdown()
    station.db.close()


def antennas(c: TestClient) -> list[int]:
    ids = []
    for name, kind in (("Dipôle", "dipole"), ("Verticale", "vertical_quarter")):
        made = c.post("/api/installation/antennas", json={"type": kind, "name": name})
        aid = made.json()["created_id"]
        c.post(f"/api/installation/antennas/{aid}/chain",
               json={"elements": [{"kind": "coax", "cable_type": "RG213", "length_m": 20}]})
        ids.append(aid)
    return ids


def body(ids: list[int], **extra: object) -> dict[str, object]:
    return {"frequencies": FREQUENCIES, "channels": [{"antenna_id": i} for i in ids],
            "duration_s": 60, **extra}


def test_campaign_runs_to_the_end_and_feeds_the_result_page(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    ids = antennas(client)
    setup = client.get("/api/campaign/setup").json()
    assert len(setup["antennas"]) == 2 and setup["campaign"] is None
    r = client.post("/api/campaign", json=body(ids))
    assert r.status_code == 200, r.text
    uuid = r.json()["uuid"]
    # Manual receiver commands are refused while the campaign holds the receiver.
    assert client.post("/api/hardware/antenna", json={"antenna": "B"}).status_code == 400
    assert client.post("/api/campaign", json=body(ids)).status_code == 400  # one at a time
    assert wait_for(lambda: station.campaign is None, 60)
    row = station.db.query("SELECT state, params_json FROM campaigns WHERE uuid = ?", (uuid,))[0]
    assert row["state"] == "done"
    assert json.loads(row["params_json"])["window_ms"] == 10_000
    # The demonstrations are hidden now that a real campaign exists.
    assert [c["uuid"] for c in client.get("/api/campaigns").json()] == [uuid]
    res = client.get(f"/api/campaigns/{uuid}/results").json()
    assert res["campaign"]["reception_mode"] == "switched"
    assert res["windows"]["count"] >= 5
    carrier = next(s for s in res["signals"] if s["class"] == "carrier")
    deltas = [b - a for a, b in zip(carrier["rssi"][0], carrier["rssi"][1], strict=True)
              if a is not None and b is not None]
    assert deltas and sum(deltas) / len(deltas) == pytest.approx(-3.0, abs=0.5)
    assert res["segments"][0]["chains"][0]["loss_db"] > 0  # RG213, 20 m at 7.1 MHz
    pairs = station.db.query("SELECT COUNT(*) FROM pair_aggregates")[0][0]
    assert pairs > 0


def test_stop_and_validation(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    ids = antennas(client)
    far = {"frequencies": [{"freq_hz": 7.0e6}, {"freq_hz": 7.3e6}],
           "channels": [{"antenna_id": i} for i in ids], "duration_s": 600}
    assert "bande utile" in client.post("/api/campaign", json=far).json()["detail"]
    same = body([ids[0], ids[0]])
    assert client.post("/api/campaign", json=same).status_code == 400  # A/A not ticked
    r = client.post("/api/campaign", json=body(ids, duration_s=3600))
    uuid = r.json()["uuid"]
    assert client.post("/api/campaign/stop").status_code == 200
    assert station.campaign is None
    state = station.db.query("SELECT state FROM campaigns WHERE uuid = ?", (uuid,))[0][0]
    assert state == "abandoned"
    assert client.post("/api/campaign/stop").status_code == 400


def test_aa_campaign_writes_margins_into_the_profile(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    ids = antennas(client)
    identity = station.receiver["identity"]
    ts = now_ms()
    with station.db.transaction() as conn:
        conn.execute(
            "INSERT INTO qualification_runs (uuid, kind, reception_mode, band, center_hz,"
            " sample_rate, started_ms, ended_ms, status, accepted_ms) VALUES"
            " ('q', 'bench', 'switched', '40 m', 7.1e6, 250000, ?, ?, 'done', ?)", (ts, ts, ts))
        conn.execute(
            "INSERT INTO hardware_profiles (qualification_id, identities_json, band,"
            " blanking_ms, useful_fraction, correction_json, margins_json, created_ms, active)"
            " VALUES (1, ?, '40 m', 2.5, 0.87, ?, ?, ?, 1)",
            (json.dumps([identity]),
             json.dumps({"freqs_hz": [7.0e6, 7.2e6], "port_db": [3.0, 3.0]}),
             json.dumps({"verdicts": [{"class": "carrier", "state": "ok"}]}), ts))
    refused = client.post("/api/campaign", json=body([ids[0], ids[0]], aa=True))
    assert "acceptez" in refused.json()["detail"]
    r = client.post("/api/campaign", json=body([ids[0], ids[0]], aa=True, accept_limits=True))
    assert r.status_code == 200, r.text
    assert wait_for(lambda: station.campaign is None, 60)
    stored = json.loads(station.db.query("SELECT margins_json FROM hardware_profiles")[0][0])
    # Simulated B is 3 dB low and the profile corrects B by +3 dB: the A/A bias is small.
    assert stored["margins"]["carrier"] < 1.5
    assert stored["aa_campaign"] == r.json()["uuid"]
    assert stored["verdicts"][0]["margin_db"] == stored["margins"]["carrier"]


def test_campaign_resumes_after_a_restart(station_home: Path) -> None:
    s = Station.from_home(station_home, inline=True)
    s.startup()
    assert wait_for(lambda: s.receiver.get("state") == "running", 5)
    from open_antenna_validation import installation as inst

    a = inst.save_antenna(s.db, {"type": "dipole", "name": "A"})
    b = inst.save_antenna(s.db, {"type": "dipole", "name": "B"})
    uuid = s.start_campaign({"frequencies": FREQUENCIES, "channels": [{"antenna_id": a},
                             {"antenna_id": b}], "duration_s": 3600})
    assert wait_for(lambda: bool(s.db.query("SELECT 1 FROM measurements")), 20)
    s.shutdown()  # the service stops, the campaign is still in progress in the database
    s.db.close()
    s2 = Station.from_home(station_home, inline=True)
    s2.startup()
    try:
        assert s2.campaign is not None and s2.campaign.uuid == uuid
        assert wait_for(lambda: s2.campaign is not None and s2.campaign.engine_started, 10)
        states = [r[0] for r in s2.db.query(
            "SELECT state FROM activity WHERE campaign_id = ? ORDER BY id",
            (s2.campaign.id,))]
        assert states[-2:] == ["interrupted", "active"]
        assert s2.db.query("SELECT state FROM campaigns WHERE uuid = ?", (uuid,))[0][0] == "running"
        assert cm.load_running(s2.db) is not None
    finally:
        s2.shutdown()
        s2.db.close()


def test_campaign_pages_are_served(client: TestClient) -> None:
    assert client.get("/campaigns/new").status_code == 200
    for asset in ("new-campaign.js", "installation.js", "results.js"):
        assert client.get(f"/static/{asset}").status_code == 200
    setup = client.get("/api/campaign/setup").json()
    assert setup["rate_hz"] == 25.0 and setup["disk_free_bytes"] > 0
    assert set(setup["class_width_hz"]) == {"carrier", "digital", "ssb", "am_fm"}
