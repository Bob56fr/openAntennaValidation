from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation import installation as inst
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.service import Station
from open_antenna_validation.storage import Database
from open_antenna_validation.web import app as web_app
from open_antenna_validation.web import create_app
from tests.conftest import PASSWORD


def test_cable_loss_goes_through_the_datasheet_points() -> None:
    for cable, points in inst.CABLES.items():
        for f, per_100m in points["db_100m"].items():
            assert inst.cable_loss_db(cable, 100, f) == pytest.approx(per_100m, abs=1e-6), cable
    # Between the points the loss grows with frequency, and with length.
    assert inst.cable_loss_db("RG58", 40, 7.1e6) < inst.cable_loss_db("RG58", 40, 14e6)
    double = inst.cable_loss_db("RG58", 80, 7.1e6)
    assert double == pytest.approx(2 * inst.cable_loss_db("RG58", 40, 7.1e6))
    with pytest.raises(inst.InstallationError):
        inst.cable_loss_db("XYZ", 10, 7e6)


def test_antenna_and_chain_versions(db: Database) -> None:
    aid = inst.save_antenna(db, {"type": "dipole", "name": "Dipôle 40 m", "height_m": 8.0})
    first = inst.save_chain(db, aid, [{"kind": "coax", "cable_type": "RG58", "length_m": 40},
                                      {"kind": "relay", "db": 0.2}])
    assert inst.chain_loss_db(db, first, 7.1e6) == pytest.approx(
        inst.cable_loss_db("RG58", 40, 7.1e6) + 0.2)
    second = inst.save_chain(db, aid, [{"kind": "coax", "cable_type": "RG213", "length_m": 40}])
    assert second != first  # a new version, the old one stays for past campaigns
    current = inst.current_chain(db, aid)
    assert current is not None and current["id"] == second
    assert current["elements"][0]["label"] == "RG213/U, 40 m"
    assert [a["name"] for a in inst.list_antennas(db)] == ["Dipôle 40 m"]
    inst.archive_antenna(db, aid)
    assert inst.list_antennas(db) == []


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"type": "dipole", "name": " "}, "nom"),
        ({"type": "parabole", "name": "x"}, "Type"),
        ({"type": "yagi", "name": "x", "azimuth_deg": 400}, "azimut"),
    ],
)
def test_invalid_antennas(db: Database, data: dict[str, object], message: str) -> None:
    with pytest.raises(inst.InstallationError, match=message):
        inst.save_antenna(db, data)


def test_invalid_chain_elements(db: Database) -> None:
    aid = inst.save_antenna(db, {"type": "dipole", "name": "D"})
    with pytest.raises(inst.InstallationError):
        inst.save_chain(db, aid, [{"kind": "coax"}])  # no cable and no loss
    with pytest.raises(inst.InstallationError):
        inst.save_chain(db, aid, [{"kind": "balun"}])  # loss required
    inst.save_chain(db, aid, [{"kind": "preamp", "db": -10.0}])  # a gain is a negative loss


@pytest.fixture
def client(station: Station, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(web_app, "FAILED_LOGIN_DELAY_S", 0.0)
    station.startup()
    assert wait_for(lambda: station.receiver.get("state") == "running", 5)
    with TestClient(create_app(station, run_lifecycle=False)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield c


def test_installation_api(client: TestClient) -> None:
    data = client.get("/api/installation").json()
    assert data["antennas"] == []  # demo antennas are not part of the catalogue
    assert "RG213" in data["cables"]
    r = client.post("/api/installation/antennas",
                    json={"type": "vertical_quarter", "name": "Verticale"})
    aid = r.json()["created_id"]
    r = client.post(f"/api/installation/antennas/{aid}/chain",
                    json={"elements": [{"kind": "coax", "cable_type": "H155", "length_m": 30}]})
    assert r.json()["antennas"][0]["chain"]["elements"][0]["cable_type"] == "H155"
    loss = client.post("/api/installation/chain-loss", json={
        "elements": [{"kind": "coax", "cable_type": "RG58", "length_m": 100}],
        "freq_hz": 10e6}).json()
    assert loss["total_db"] == pytest.approx(4.9)
    bad = client.post("/api/installation/antennas", json={"type": "?", "name": "x"})
    assert bad.status_code == 400
    assert client.get("/installation").status_code == 200
