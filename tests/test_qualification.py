from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation import qualification as q
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.bench import analyze_switching, measure_levels, measure_switching
from open_antenna_validation.config import (
    CONFIG_FILENAME,
    QualificationConfig,
    SimulatedConfig,
    load_config,
    save_config,
)
from open_antenna_validation.hardware.simulated import SimulatedDriver
from open_antenna_validation.service import Station
from open_antenna_validation.web import app as web_app
from open_antenna_validation.web import create_app
from tests.conftest import PASSWORD


def bench_driver(**overrides: object) -> SimulatedDriver:
    settings: dict[str, object] = {"realtime": False, "fading_db": 0.0, "signals": [],
                                    "noise_dbfs": -50.0, **overrides}
    config = SimulatedConfig(**settings)  # type: ignore[arg-type]
    driver = SimulatedDriver(config)
    driver.set_sample_rate(250_000)
    driver.open()
    return driver


def test_switching_with_attenuator_finds_simulated_latency_and_settling() -> None:
    driver = bench_driver(antenna_b_signal_db=-10.0, antenna_b_noise_db=-10.0,
                          switch_latency_ms=1.4, switch_settle_ms=1.6, switch_glitch_db=6.0)
    raw = measure_switching(driver, 400, 0.010)
    r = analyze_switching(raw["power"], raw["rate"], raw["dwell"], raw["commands"], raw["inputs"])
    assert r["detected"] is True
    assert r["step_db"] == pytest.approx(-10.0, abs=0.3)
    assert r["latency_ms"]["p50"] == pytest.approx(1.4, abs=0.1)
    # Glitch of 6 dB decaying with tau = settle / 4: back within 0.2 dB after about 1.4 ms.
    assert 0.9 < r["settle_ms"] < 2.5
    assert 3.0 < r["glitch_db"] < 6.5
    assert len(r["envelope"]["t_ms"]) <= 400


def test_switching_without_step_is_aligned_on_command() -> None:
    driver = bench_driver(antenna_b_signal_db=0.0, antenna_b_noise_db=0.0)
    raw = measure_switching(driver, 200, 0.010)
    r = analyze_switching(raw["power"], raw["rate"], raw["dwell"], raw["commands"], raw["inputs"])
    assert r["detected"] is False and r["aligned_on"] == "command"
    assert r["latency_ms"] is None
    assert r["settle_ms"] > 1.4  # includes the latency: an upper bound


def test_levels_measure_input_difference() -> None:
    driver = bench_driver(antenna_b_signal_db=0.0, antenna_b_noise_db=-0.8)
    levels = measure_levels(driver, 4.0, 4096, 16)
    assert len(levels["difference_db"]) == 16
    assert sum(levels["difference_db"]) / 16 == pytest.approx(0.8, abs=0.15)
    assert len(levels["freqs_hz"]) == 16


def test_profile_from_steps() -> None:
    cfg = QualificationConfig()
    lv = lambda d: {"inputs": ["A", "B"], "freqs_hz": [7.0e6, 7.1e6],  # noqa: E731
                    "difference_db": d, "floor_dbfs": {"A": -120.0, "B": -119.0},
                    "total_db": {"A": -60.0, "B": -60.0 - sum(d) / 2}, "level_db": {}}
    results = {"attenuator_db": 10.0, "steps": {
        "floor": {"levels": lv([0.0, 0.0])},
        "symmetric": {"levels": lv([0.5, 0.3])},
        "swapped": {"levels": lv([0.1, -0.1])},
        "attenuator": {"levels": lv([10.6, 10.2]), "switching": {
            "detected": True, "latency_ms": {"p50": 1.4, "p99": 1.6, "max": 1.7, "jitter": 0.1},
            "settle_ms": 1.5, "glitch_db": 5.0}},
    }}
    p = q.compute_profile(results, cfg)
    assert p["complete"] is True
    assert p["imbalance"]["port_db"] == [0.3, 0.1]
    assert p["imbalance"]["splitter_db"] == pytest.approx(0.2)
    assert p["blanking_ms"] == pytest.approx(1.6 + 1.5 + 0.2)
    assert p["attenuator_check_db"] == pytest.approx(0.0)
    states = {v["class"]: v["state"] for v in p["verdicts"]}
    # 3.3 ms blanked: fine at 2 Hz, 2 x 20 Hz leaves 25 ms - 3.3 ms = 87 % for SSB.
    assert states == {"carrier": "ok", "digital": "ok", "ssb": "ok"}
    assert q.verdicts(15.0, cfg)[2]["state"] == "warn"  # 25 ms per input at 20 Hz: 40 % left
    assert q.verdicts(20.0, cfg)[2]["state"] == "bad"  # 20 % left
    assert q.useful_fraction(3.3, 25) == pytest.approx(1 - 3.3 / 20)


def test_profile_reports_missing_steps() -> None:
    p = q.compute_profile({"steps": {}}, QualificationConfig())
    assert p["complete"] is False and len(p["missing"]) == 4
    assert all(v["state"] == "unknown" for v in p["verdicts"])


@pytest.fixture
def bench_home(station_home: Path) -> Path:
    path = station_home / CONFIG_FILENAME
    config = load_config(path)
    sim = config.hardware.simulated
    sim.signals = []
    sim.noise_dbfs = -50.0
    sim.fading_db = 0.0
    sim.antenna_b_signal_db = -10.0
    sim.antenna_b_noise_db = -10.0
    config.qualification.switches = 200
    config.qualification.levels_seconds = 2.0
    save_config(config, path)
    return station_home


@pytest.fixture
def client(bench_home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(web_app, "FAILED_LOGIN_DELAY_S", 0.0)
    station = Station.from_home(bench_home, inline=True)
    station.startup()
    assert wait_for(lambda: station.receiver.get("state") == "running", 5)
    with TestClient(create_app(station, run_lifecycle=False)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        c.station = station  # type: ignore[attr-defined]
        yield c
    station.shutdown()
    station.db.close()


def test_full_bench_flow_through_the_api(client: TestClient) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    overview = client.get("/api/qualification").json()
    keys = [s["key"] for s in overview["steps"]]
    assert keys[:4] == ["floor", "symmetric", "swapped", "attenuator"]
    assert overview["run"] is None
    run = client.post("/api/qualification/runs", json={"attenuator_db": 10}).json()
    uuid = run["uuid"]
    assert run["band"] == "40 m" and run["status"] == "running"
    assert client.post(f"/api/qualification/runs/{uuid}/finish").status_code == 400  # incomplete
    for step in ("floor", "symmetric", "swapped", "attenuator"):
        r = client.post(f"/api/qualification/runs/{uuid}/steps/{step}")
        assert r.status_code == 202, r.text
        assert wait_for(lambda: not station.bench.get("busy"), 30)
        assert "error" not in station.bench
    assert client.post(f"/api/qualification/runs/{uuid}/steps/inconnue").status_code == 400
    done = client.post(f"/api/qualification/runs/{uuid}/finish").json()
    assert done["status"] == "done"
    prof = done["profile"]
    assert prof["switching"]["source"] == "attenuator"
    assert prof["switching"]["latency_ms"]["p50"] == pytest.approx(1.4, abs=0.15)
    assert prof["blanking_ms"] < 5.0
    assert client.post(f"/api/qualification/runs/{uuid}/steps/floor").status_code == 400
    accepted = client.post(f"/api/qualification/runs/{uuid}/accept").json()
    assert accepted["accepted_ms"] is not None
    rows = station.db.query("SELECT active, blanking_ms FROM hardware_profiles")
    assert len(rows) == 1 and rows[0]["active"] == 1
    assert client.get("/qualification").status_code == 200


def test_varying_source_is_flagged_as_noisy() -> None:
    from open_antenna_validation.config import SimulatedSignal

    voice = SimulatedSignal(offset_hz=10_000.0, kind="ssb", dbfs=-30.0)
    driver = bench_driver(antenna_b_signal_db=-10.0, antenna_b_noise_db=-10.0, signals=[voice],
                          noise_dbfs=-70.0)
    raw = measure_switching(driver, 200, 0.010)
    r = analyze_switching(raw["power"], raw["rate"], raw["dwell"], raw["commands"], raw["inputs"])
    assert r["noisy"] is True
    results = {"steps": {"attenuator": {"switching": {**r}}}}
    warnings = q.compute_profile(results, QualificationConfig())["warnings"]
    assert any("bruitée" in w for w in warnings)
