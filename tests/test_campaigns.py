from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.campaigns import CampaignNotFound, campaign_results, list_campaigns
from open_antenna_validation.demo import N_WINDOWS, ensure_demo_campaigns
from open_antenna_validation.service import Station
from open_antenna_validation.storage import Database
from open_antenna_validation.web import app as web_app
from open_antenna_validation.web import create_app
from tests.conftest import PASSWORD


def test_demo_campaigns_created_once(db: Database) -> None:
    assert ensure_demo_campaigns(db) == 3
    assert ensure_demo_campaigns(db) == 0
    items = list_campaigns(db)
    assert {c["uuid"] for c in items} == {"demo-40m-2", "demo-40m-6", "demo-40m-ft8"}
    assert all(c["demo"] for c in items)
    six = next(c for c in items if c["uuid"] == "demo-40m-6")
    assert six["n_channels"] == 6 and "Mini-whip" in six["antenna_types"]


def test_demo_hidden_once_a_real_campaign_exists(db: Database) -> None:
    ensure_demo_campaigns(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO campaigns (uuid, reception_mode, measure_mode, low_hz, high_hz, state,"
            " created_ms, n_channels) VALUES ('vraie', 'switched', 'fixed', 7e6, 7.2e6, 'running',"
            " 1, 2)"
        )
    assert [c["uuid"] for c in list_campaigns(db)] == ["vraie"]
    assert list_campaigns(db, show_demo=False) == [c for c in list_campaigns(db) if not c["demo"]]


def test_results_shape_and_installation_view(db: Database) -> None:
    ensure_demo_campaigns(db)
    r = campaign_results(db, "demo-40m-2")
    assert r["campaign"]["demo"] is True
    assert r["campaign"]["locator"] == "JN18eu"
    assert r["windows"]["count"] == N_WINDOWS
    assert [c["letter"] for c in r["channels"]] == ["A", "B"]
    assert len(r["signals"]) == 8
    assert {s["class"] for s in r["signals"]} == {"carrier", "digital", "ssb"}
    signal = r["signals"][0]
    assert len(signal["rssi"]) == 2 and len(signal["rssi"][0]) == N_WINDOWS
    # Coax of B replaced in segment 2: loss 2.3 dB, then 0.8 dB.
    assert [s["chains"][1]["loss_db"] for s in r["segments"]] == [2.3, 0.8]
    assert any(a["state"] == "calm" for a in r["activity"])
    # Calm windows have no RSSI but keep the noise floor.
    calm = next(a for a in r["activity"] if a["state"] == "calm")
    i = (calm["start_ms"] - r["windows"]["start_ms"]) // r["windows"]["step_ms"]
    assert signal["rssi"][0][i] is None and signal["floor"][0][i] is not None


def test_results_unknown_campaign(db: Database) -> None:
    with pytest.raises(CampaignNotFound):
        campaign_results(db, "absente")


@pytest.fixture
def client(station: Station, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(web_app, "FAILED_LOGIN_DELAY_S", 0.0)
    station.startup()  # creates the demo campaigns
    assert wait_for(lambda: station.receiver.get("state") == "running", 5)
    with TestClient(create_app(station, run_lifecycle=False)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield c


def test_campaign_api_and_pages(client: TestClient) -> None:
    items = client.get("/api/campaigns").json()
    assert len(items) == 3
    ft8 = client.get("/api/campaigns/demo-40m-ft8/results").json()["digital"]
    assert ft8["mode"] == "ft8" and len(ft8["stations"]) > 30
    # The vertical (B) loses at high angles, wins on long distances.
    assert ft8["groups"]["class"]["nvis"]["1"]["median"] < -5
    assert ft8["groups"]["class"]["dxn"]["1"]["median"] > 2
    r = client.get("/api/campaigns/demo-40m-6/results", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and len(r.json()["channels"]) == 6
    assert client.get("/api/campaigns/absente/results").status_code == 404
    assert client.get("/campaigns").status_code == 200
    assert client.get("/campaign?id=demo-40m-2").status_code == 200
    assert client.get("/static/results.js").status_code == 200
