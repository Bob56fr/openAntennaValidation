"""Simultaneous mode: one SDR per antenna, load test, calibration by an A/A campaign."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation import service
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.config import (
    CONFIG_FILENAME,
    Config,
    SdrSlot,
    load_config,
    save_config,
)
from open_antenna_validation.engine import Q_MISALIGNED
from open_antenna_validation.service import Station, judge_load
from open_antenna_validation.web import app as web_app
from open_antenna_validation.web import create_app
from tests.conftest import PASSWORD

CENTER = 7.1e6
FREQUENCIES = [
    {"freq_hz": CENTER - 62_000, "class": "carrier"},
    {"freq_hz": CENTER - 26_000, "class": "digital"},
    {"freq_hz": CENTER - 10_000, "class": "ssb"},
]
GAIN_ERROR_DB = 2.0


@pytest.fixture
def client(station_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    path = station_home / CONFIG_FILENAME
    config = load_config(path)
    # A second simulated SDR that reads 2 dB high: what the calibration must remove.
    config.hardware.others = [SdrSlot(serial="SIM0002", antenna="A",
                                      sim_gain_error_db=GAIN_ERROR_DB)]
    config.hardware.antenna = "A"
    config.campaign.load_test_s = 2.0
    config.campaign.calm_after_s = 30.0
    save_config(config, path)
    monkeypatch.setattr(web_app, "FAILED_LOGIN_DELAY_S", 0.0)
    # The simulator runs faster than real time in one process: its load says nothing about
    # the computer (and depends on what else pytest is doing). Verdict forced; judge_load
    # itself is tested below.
    real_judge = service.judge_load
    monkeypatch.setattr(service, "judge_load", lambda *a, **k: {
        **real_judge(*a, **k), "verdict": "ok", "message": "Charge forcée pour les tests."})
    station = Station.from_home(station_home, inline=True)
    station.startup()
    assert wait_for(lambda: all(d["state"] == "running" for d in station.devices_report()), 5)
    with TestClient(create_app(station, run_lifecycle=False)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        c.station = station  # type: ignore[attr-defined]
        yield c
    station.shutdown()
    station.db.close()


def antennas(c: TestClient) -> list[int]:
    ids = []
    for name, kind in (("Dipôle", "dipole"), ("Verticale", "vertical_quarter")):
        aid = c.post("/api/installation/antennas", json={"type": kind, "name": name}
                     ).json()["created_id"]
        ids.append(aid)
    return ids


def body(ids: list[int], **extra: object) -> dict[str, object]:
    return {"reception": "simultaneous", "frequencies": FREQUENCIES, "duration_s": 60,
            "channels": [{"antenna_id": a, "serial": s}
                         for a, s in zip(ids, ("SIM0001", "SIM0002"), strict=True)],
            "accept_load": True, **extra}


def load_test(c: TestClient) -> dict[str, object]:
    station: Station = c.station  # type: ignore[attr-defined]
    r = c.post("/api/campaign/loadtest", json={})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "running"
    assert c.post("/api/campaign/loadtest", json={}).status_code == 400  # one at a time
    assert wait_for(lambda: (station.load_report() or {}).get("state") == "done", 10)
    report = station.load_report() or {}
    assert set(report["loads"]) == {"SIM0001", "SIM0002"}  # type: ignore[arg-type]
    return report


def deltas(c: TestClient, uuid: str) -> list[float]:
    res = c.get(f"/api/campaigns/{uuid}/results").json()
    carrier = next(s for s in res["signals"] if s["class"] == "carrier")
    return [b - a for a, b in zip(carrier["rssi"][0], carrier["rssi"][1], strict=True)
            if a is not None and b is not None]


def test_station_runs_one_worker_per_sdr(client: TestClient) -> None:
    report = client.get("/api/hardware").json()
    assert [(d["serial"], d["state"], d["primary"]) for d in report["devices"]] == [
        ("SIM0001", "running", True), ("SIM0002", "running", False)]


def test_simultaneous_campaign_needs_a_load_test(client: TestClient) -> None:
    ids = antennas(client)
    r = client.post("/api/campaign", json=body(ids))
    assert r.status_code == 400 and "test de charge" in r.json()["detail"]
    load_test(client)
    twice = body(ids)
    twice["channels"] = [{"antenna_id": ids[0], "serial": "SIM0001"},
                         {"antenna_id": ids[1], "serial": "SIM0001"}]
    assert "qu'une fois" in client.post("/api/campaign", json=twice).json()["detail"]
    unknown = body(ids)
    unknown["channels"] = [{"antenna_id": ids[0], "serial": "SIM0001"},
                           {"antenna_id": ids[1], "serial": "SIM9999"}]
    # Not in the load test, then unknown to the station.
    assert "test de charge" in client.post("/api/campaign", json=unknown).json()["detail"]
    station: Station = client.station  # type: ignore[attr-defined]
    with pytest.raises(cm.CampaignError, match="inconnu"):
        cm.create_campaign(station.db, station.config, station.receiver, unknown,
                           devices=station.devices_report())


def test_aa_calibration_then_corrected_campaign(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    ids = antennas(client)
    load_test(client)
    # 1. A/A: the same antenna on both SDRs, through a splitter.
    r = client.post("/api/campaign", json=body([ids[0], ids[0]], aa=True))
    assert r.status_code == 200, r.text
    aa = r.json()["uuid"]
    assert wait_for(lambda: station.campaign is None, 60)
    row = station.db.query("SELECT state, reception_mode, n_channels FROM campaigns"
                           " WHERE uuid = ?", (aa,))[0]
    assert tuple(row) == ("done", "simultaneous", 2)
    raw = deltas(client, aa)
    assert raw and sum(raw) / len(raw) == pytest.approx(GAIN_ERROR_DB, abs=0.5)
    profile = station.db.query(
        "SELECT p.identities_json, p.correction_json, p.margins_json, q.kind,"
        " q.reception_mode FROM hardware_profiles p JOIN qualification_runs q"
        " ON q.id = p.qualification_id WHERE p.active = 1")[0]
    assert (profile["kind"], profile["reception_mode"]) == ("aa", "simultaneous")
    identities = json.loads(profile["identities_json"])
    assert [i.split(":")[2] for i in identities] == ["SIM0001", "SIM0002"]
    correction = json.loads(profile["correction_json"])
    assert correction["channels_db"][0] == [0.0, 0.0, 0.0]
    carrier, digital, voice = correction["channels_db"][1]
    assert carrier == pytest.approx(-GAIN_ERROR_DB, abs=0.5)
    assert digital == pytest.approx(-GAIN_ERROR_DB, abs=0.5)
    assert voice == pytest.approx(-GAIN_ERROR_DB, abs=1.5)  # one minute of voice: noisier
    assert json.loads(profile["margins_json"])["margins"]["carrier"] < 1.0
    profiles = client.get("/api/campaign/profiles", params={"serials": "SIM0001,SIM0002"}).json()
    assert profiles["40 m"] is not None
    assert client.get("/api/campaign/profiles",
                      params={"serials": "SIM0002,SIM0001"}).json()["40 m"] is None

    # 2. Two antennas: the gain error of the second SDR is corrected.
    load_test(client)
    r = client.post("/api/campaign", json=body(ids))
    assert "limites" in r.json()["detail"]  # the operator sees the calibration margins
    r = client.post("/api/campaign", json=body(ids, accept_limits=True))
    assert r.status_code == 200, r.text
    uuid = r.json()["uuid"]
    assert wait_for(lambda: station.campaign is None, 60)
    params = json.loads(station.db.query("SELECT params_json FROM campaigns WHERE uuid = ?",
                                         (uuid,))[0][0])
    assert params["devices"] == ["SIM0001", "SIM0002"] and params["qualification"]["kind"] == "aa"
    corrected = deltas(client, uuid)
    assert corrected and sum(corrected) / len(corrected) == pytest.approx(0.0, abs=0.5)


def test_misaligned_voices_are_excluded(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    ids = antennas(client)
    load_test(client)
    assert client.post("/api/campaign", json=body(ids, duration_s=3600)).status_code == 200
    run = station.campaign
    assert run is not None and run.simultaneous
    assert client.post("/api/campaign/stop").status_code == 200
    # Replay two periods by hand: the SDRs 2 ms apart, then 12 ms apart.
    voice = next(s["id"] for s in run.signals if s["class"] == "ssb")
    carrier = next(s["id"] for s in run.signals if s["class"] == "carrier")
    for ts, spread in ((10_000_000, 2.0), (10_001_000, 12.0)):
        for ch, wall in ((0, 0.0), (1, spread)):
            values = [[sid, ch, -60.0, -100.0, 0, 1] for sid in (voice, carrier)]
            cm.store_measurements(station.db, station.config, run, {
                "ts_ms": ts, "period_ms": 1000, "values": values, "channel_base": ch,
                "wall_ms": 1e12 + ts + wall})
    rows = station.db.query("SELECT ts_ms, signal_id, quality FROM measurements"
                            " WHERE campaign_id = ? AND ts_ms >= 10000000", (run.id,))
    bad = {(r["ts_ms"], r["signal_id"]) for r in rows if r["quality"] & Q_MISALIGNED}
    assert bad == {(10_001_000, voice)}  # both SDRs' rows, the carrier is kept
    assert len([r for r in rows if r["quality"] & Q_MISALIGNED]) == 2
    assert run.align_ms == [2.0, 12.0]


def test_judge_load() -> None:
    cfg = Config()
    ok = judge_load({"a": [(0.1, 1.0, 0), (0.1, 1.0, 0)]}, cfg, 4)
    assert ok["verdict"] == "ok" and ok["loads"]["a"] == pytest.approx(0.1)
    limit = judge_load({"a": [(0.75, 1.0, 0)]}, cfg, 4)
    assert limit["verdict"] == "limit"
    lost = judge_load({"a": [(0.1, 1.0, 0), (0.1, 1.0, 500)]}, cfg, 4)
    assert lost["verdict"] == "limit" and "perdus" in lost["message"]
    many = judge_load({str(k): [(0.6, 1.0, 0)] for k in range(8)}, cfg, 4)
    assert many["verdict"] == "refuse"  # 8 x 0.6 cores on 4 cores
    assert judge_load({"a": []}, cfg, 4)["verdict"] == "refuse"


def test_disk_check_refuses_a_campaign_too_big(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    ids = antennas(client)
    request = {"frequencies": FREQUENCIES, "duration_s": 600,
               "channels": [{"antenna_id": i} for i in ids]}
    with pytest.raises(cm.CampaignError, match="Espace disque insuffisant"):
        cm.create_campaign(station.db, station.config, station.receiver, request,
                           free_bytes=10_000)
