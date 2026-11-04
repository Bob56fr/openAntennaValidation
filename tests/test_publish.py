"""Publication on the WordPress site, against the fake plugin (tests/fake_wordpress.py)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.campaigns import campaign_meta
from open_antenna_validation.publish import (
    FACTORS,
    Publisher,
    PublishError,
    StorageFull,
    choose_window,
)
from open_antenna_validation.service import Station
from tests.fake_wordpress import FakeWordPress
from tests.test_campaign_engine import antennas, body, client  # noqa: F401  (fixture)

TOKEN = "jeton-de-test-0123456789"


@pytest.fixture
def fake() -> Iterator[FakeWordPress]:
    wp = FakeWordPress(TOKEN).start()
    yield wp
    wp.stop()


def publisher(station: Station, fake: FakeWordPress) -> Publisher:
    """A publisher of its own (no thread): the station's one stays disabled."""
    cfg = station.config.model_copy(deep=True)
    cfg.wordpress.enabled = True
    cfg.wordpress.url = fake.url
    cfg.wordpress.token = TOKEN
    cfg.station.locator = "JN18eu"
    return Publisher(station.db, cfg, lambda: {"state": "running", "sdr": "Simulateur",
                                               "campaign": None})


def run(c: TestClient, **extra: Any) -> str:
    station: Station = c.station  # type: ignore[attr-defined]
    r = c.post("/api/campaign", json=body(antennas(c), **extra))
    assert r.status_code == 200, r.text
    assert wait_for(lambda: station.campaign is None, 60)
    return str(r.json()["uuid"])


def aggregates(station: Station, uuid: str) -> int:
    return int(station.db.query(
        "SELECT COUNT(*) FROM aggregates a JOIN campaigns c ON c.id = a.campaign_id"
        " WHERE c.uuid = ?", (uuid,))[0][0])


def test_choose_window() -> None:
    limits = {"package_max_bytes": 1_000_000, "row_bytes": 40}
    # 1 day, 60 s windows, 8 signals x 2 channels = 23 040 rows = 0.92 MB: fits at 80 %? no.
    assert choose_window(60_000, 86_400_000, 16, 5_000, limits, 0.8) == 120_000
    assert choose_window(60_000, 86_400_000, 16, 5_000, None, 0.8) == 60_000
    assert choose_window(60_000, 86_400_000, 16, 5_000, {"package_max_bytes": 0}, 0.8) == 60_000
    # Ten antennas are never refused: the window widens instead.
    huge = choose_window(900_000, 30 * 86_400_000, 20 * 10, 5_000, limits, 0.8)
    assert huge >= 900_000 * 8 and huge <= 900_000 * FACTORS[-1]


def test_campaign_published_once_complete(client: TestClient, fake: FakeWordPress  # noqa: F811
                                          ) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    station.config.station.locator = "JN18eu"
    station.config.station.callsign = "F4ABC"
    private = run(client)
    uuid = run(client, visibility="public")
    pub = publisher(station, fake)
    pub.publish_pending()
    assert fake.errors == []
    assert private not in fake.campaigns  # private campaigns never leave the station
    sent = fake.campaigns[uuid]
    assert sent["complete"] and sent["visibility"] == "public"
    assert len(sent["windows"]) == aggregates(station, uuid)  # same window, no limit
    meta = sent["meta"]
    assert meta["campaign"]["locator"] == "JN18" and meta["campaign"]["operator"] == "F4ABC"
    assert meta["campaign"]["timezone"] == "Europe/Paris"
    assert {c["type_code"] for c in meta["channels"]} == {"dipole", "vertical_quarter"}
    assert fake.station is not None and fake.station["locator"] == "JN18"
    assert fake.station["status"]["state"] == "running"
    # Nothing new: only the station state goes, no campaign.
    count = len(fake.payloads)
    pub.publish_pending()
    assert len(fake.payloads) == count + 1 and fake.payloads[-1]["campaigns"] == []
    states = pub.campaign_states()
    assert states[uuid]["state"] == "complete" and private not in states
    # Visibility changed on the station: the metadata goes again, the windows do not.
    r = client.post(f"/api/campaigns/{uuid}/visibility", json={"visibility": "unlisted"})
    assert r.status_code == 200
    pub.publish_pending()
    last = fake.payloads[-1]["campaigns"][0]
    assert last["visibility"] == "unlisted" and last["rows"] == [] and "meta" in last


def test_package_limit_widens_the_published_window(client: TestClient,  # noqa: F811
                                                   fake: FakeWordPress) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    uuid = run(client, visibility="public")
    local = aggregates(station, uuid)
    meta = len(json.dumps(campaign_meta(station.db, uuid, locator_chars=4),
                          ensure_ascii=False, separators=(",", ":")).encode())
    # Room for about a third of the local windows: the published window must widen.
    fake.package_max_bytes = int((meta + local * 40 / 3) / 0.8)
    pub = publisher(station, fake)
    pub.publish_pending()
    sent = fake.campaigns[uuid]
    assert sent["complete"] and sent["step_ms"] >= 20_000
    assert len(sent["windows"]) < local
    assert fake.package_bytes(uuid) <= fake.package_max_bytes


def test_too_large_refused_then_retried(client: TestClient, fake: FakeWordPress  # noqa: F811
                                        ) -> None:
    uuid = run(client, visibility="public")
    fake.package_max_bytes = 1_000  # not even the metadata fits
    pub = publisher(client.station, fake)  # type: ignore[attr-defined]
    with pytest.raises(PublishError, match="trop gros"):
        pub.publish_pending()
    assert pub.campaign_states()[uuid]["state"] == "too_large"
    pub.publish_pending()  # the other campaigns (none) go on; this one waits
    assert uuid not in fake.campaigns or not fake.campaigns[uuid]["windows"]
    fake.package_max_bytes = 0
    client.post(f"/api/campaigns/{uuid}/visibility", json={"visibility": "public"})
    pub.publish_pending()
    assert fake.campaigns[uuid]["complete"]


def test_outage_quota_and_plain_permalinks(client: TestClient, fake: FakeWordPress  # noqa: F811
                                           ) -> None:
    station: Station = client.station  # type: ignore[attr-defined]
    uuid = run(client, visibility="public")
    pub = publisher(station, fake)
    fake.fail_next = 1
    with pytest.raises(PublishError, match="503"):
        pub.publish_pending()
    assert uuid not in fake.campaigns
    fake.storage_full = True
    with pytest.raises(StorageFull):
        pub.publish_pending()
    fake.storage_full = False
    fake.plain_permalinks = True
    pub.publish_pending()  # /wp-json/ answers an HTML page: ?rest_route= is used
    assert fake.campaigns[uuid]["complete"]
    assert pub.queue_rows() == 0


def test_bad_token(client: TestClient, fake: FakeWordPress) -> None:  # noqa: F811
    pub = publisher(client.station, fake)  # type: ignore[attr-defined]
    pub._config.wordpress.token = "autre"
    with pytest.raises(PublishError, match="Jeton refusé"):
        pub.publish_pending()


def test_settings_api(client: TestClient, fake: FakeWordPress) -> None:  # noqa: F811
    station: Station = client.station  # type: ignore[attr-defined]
    assert client.get("/api/wordpress").json()["enabled"] is False
    r = client.put("/api/wordpress", json={"enabled": True, "url": "ftp://x", "token": "t"})
    assert r.status_code == 400
    r = client.put("/api/wordpress", json={"enabled": True, "url": fake.url + "/",
                                           "token": TOKEN})
    assert r.status_code == 200 and r.json()["token_set"] and "token" not in r.json()
    assert station.config.wordpress.url == fake.url
    test = client.post("/api/wordpress/test").json()
    assert test["limits"]["operator"] == "Opérateur d'essai"
    est = client.get("/api/wordpress/estimate", params={
        "duration_s": 86_400, "n_signals": 8, "n_channels": 2, "window_s": 60}).json()
    assert est["window_ms"] == 60_000 and est["limit_bytes"] == 0
    # Keep the token when the form leaves it empty.
    client.put("/api/wordpress", json={"enabled": False, "url": fake.url, "token": ""})
    assert station.config.wordpress.token == TOKEN
