from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.service import Station
from open_antenna_validation.web import app as web_app
from open_antenna_validation.web import create_app
from tests.conftest import PASSWORD


@pytest.fixture
def client(station: Station, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(web_app, "FAILED_LOGIN_DELAY_S", 0.0)
    station.startup()
    assert wait_for(lambda: station.receiver.get("state") == "running", 5)
    with TestClient(create_app(station, run_lifecycle=False)) as c:
        yield c


def login(client: TestClient) -> None:
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_api_requires_login(client: TestClient) -> None:
    assert client.get("/api/hardware").status_code == 401
    assert client.get("/api/status").status_code == 401
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"


def test_wrong_password_rejected(client: TestClient) -> None:
    assert client.post("/api/login", json={"password": "faux"}).status_code == 401
    assert client.get("/api/session").json() == {"authenticated": False, "password_set": True}


def test_login_logout(client: TestClient) -> None:
    login(client)
    assert client.get("/api/session").json()["authenticated"] is True
    assert client.get("/").status_code == 200
    client.post("/api/logout")
    assert client.get("/api/hardware").status_code == 401


def test_hardware_report(client: TestClient) -> None:
    login(client)
    hw = client.get("/api/hardware").json()
    assert hw["state"] == "running"
    assert hw["device"]["serial"] == "SIM0001"
    assert hw["antennas"] == ["A", "B"]
    assert hw["antenna"] == "A"
    assert len(hw["gain_steps"]) == 40
    assert hw["identity"] == "simulated:Simulateur:SIM0001:antA:gain"


def test_switch_antenna_and_frequency(client: TestClient, station: Station) -> None:
    login(client)
    assert client.post("/api/hardware/antenna", json={"antenna": "B"}).status_code == 200
    assert wait_for(lambda: station.receiver.get("antenna") == "B", 5)
    assert client.post("/api/hardware/antenna", json={"antenna": "C"}).status_code == 400
    assert client.post("/api/receiver/frequency", json={"center_hz": 145e6}).status_code == 400
    r = client.post("/api/receiver/frequency", json={"center_hz": 3.45e6})
    assert r.status_code == 200 and r.json()["center_hz"] == 3.45e6
    assert client.post("/api/hardware/gain", json={"index": 99}).status_code == 400


def test_bands_and_status(client: TestClient) -> None:
    login(client)
    names = [b["name"] for b in client.get("/api/bands").json()]
    assert "45 m" in names and "40 m" in names
    status = client.get("/api/status").json()
    assert status["station"]["id"] == "test-station"


def test_spectrum_websocket_requires_login_then_streams(client: TestClient) -> None:
    with pytest.raises(Exception), client.websocket_connect("/ws/spectrum") as ws:  # noqa: B017
        ws.receive_json()
    login(client)
    with client.websocket_connect("/ws/spectrum") as ws:
        message = ws.receive_json()
        assert message["type"] == "spectrum"
        assert len(message["bins_dbfs"]) == 1024


def test_static_assets_served(client: TestClient) -> None:
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/login").status_code == 200
