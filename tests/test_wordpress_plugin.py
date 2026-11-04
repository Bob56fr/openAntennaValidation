"""The real plugin, on the test WordPress (wordpress/dev/dev-up.sh prints the tokens).

    OAV_WP_URL=http://<debian>:8082 OAV_WP_TOKEN1=oav_... OAV_WP_TOKEN2=oav_... \\
        pytest -m wordpress

Two stations of two operators publish a campaign each (criterion of phase P7); the public
routes then serve them, in the format of the station's own page.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid as uuidlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.campaigns import campaign_results
from open_antenna_validation.config import CONFIG_FILENAME, Config, save_config
from open_antenna_validation.installation import save_antenna
from open_antenna_validation.publish import Publisher, PublishError
from open_antenna_validation.service import Station

pytestmark = pytest.mark.wordpress

URL = os.environ.get("OAV_WP_URL", "").rstrip("/")
TOKENS = [os.environ.get("OAV_WP_TOKEN1", ""), os.environ.get("OAV_WP_TOKEN2", "")]
NS = "open-antenna-validation/v1"


def public(route: str) -> Any:
    for url in (f"{URL}/wp-json/{NS}/public/{route}", f"{URL}/?rest_route=/{NS}/public/{route}"):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or "rest_route" in url:
                raise
        except json.JSONDecodeError:
            continue
    raise AssertionError("routes publiques introuvables")


def make_station(home: Path, n: int, token: str) -> Station:
    config = Config()
    # A fresh identity per run: a token is bound to the first station that uses it, and
    # dev-up.sh creates new tokens at each start.
    config.station.id = f"essai-{n}-{uuidlib.uuid4().hex[:8]}"
    config.station.name = f"Station d'essai {n}"
    config.station.callsign = f"F4TS{n}"
    config.station.locator = "JN18eu" if n == 1 else "IN88gm"
    config.hardware.driver = "simulated"
    config.hardware.simulated.realtime = False
    config.wordpress.url = URL
    config.wordpress.token = token
    save_config(config, home / CONFIG_FILENAME)
    s = Station.from_home(home, inline=True)
    s.startup()
    assert wait_for(lambda: s.receiver.get("state") == "running", 10)
    return s


def run_campaign(s: Station, kinds: tuple[str, str]) -> str:
    ids = [save_antenna(s.db, {"type": k, "name": f"{k} {s.config.station.callsign}"})
           for k in kinds]
    request = {"frequencies": [{"freq_hz": 7.038e6, "class": "carrier"},
                               {"freq_hz": 7.074e6, "class": "digital"}],
               "channels": [{"antenna_id": i} for i in ids], "duration_s": 60,
               "visibility": "public"}
    uuid = s.start_campaign(request)
    assert wait_for(lambda: s.campaign is None, 60)
    return uuid


@pytest.fixture(scope="module")
def stations(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[Station]]:
    if not URL or not all(TOKENS):
        pytest.skip("OAV_WP_URL, OAV_WP_TOKEN1 et OAV_WP_TOKEN2 non définis")
    made = [make_station(tmp_path_factory.mktemp(f"st{n}"), n, TOKENS[n - 1]) for n in (1, 2)]
    yield made
    for s in made:
        s.shutdown()
        s.db.close()


def publisher(s: Station) -> Publisher:
    return Publisher(s.db, s.config, s._station_status)


def test_two_operators_publish(stations: list[Station]) -> None:
    s1, s2 = stations
    u1 = run_campaign(s1, ("dipole", "vertical_quarter"))
    u2 = run_campaign(s2, ("vertical_quarter", "efhw"))
    for s in (s1, s2):
        pub = publisher(s)
        limits = pub.fetch_limits()
        assert limits["station"].startswith("Station de test")
        pub.publish_pending()
        assert pub.queue_rows() == 0

    listing = public("campaigns")
    by_uuid = {c["uuid"]: c for c in listing["campaigns"]}
    assert {u1, u2} <= set(by_uuid)
    assert not any(c["demo"] for c in listing["campaigns"])  # demonstrations gone
    c1, c2 = by_uuid[u1], by_uuid[u2]
    assert c1["operator"] == "F4TS1" and c2["operator"] == "F4TS2"
    assert c1["operator_name"] != c2["operator_name"]  # two WordPress operators
    # Unordered pair of antenna types: both campaigns contain a quarter-wave vertical.
    assert sorted(c1["types"]) == ["dipole", "vertical_quarter"]
    assert sorted(c2["types"]) == ["efhw", "vertical_quarter"]
    assert listing["types"]["vertical_quarter"]

    # The page of the site gets what the local page gets (same window here: no limit hit).
    local = campaign_results(s1.db, u1)
    site = public(f"campaigns/{u1}")
    assert site["campaign"]["locator"] == "JN18" and site["campaign"]["timezone"]
    assert site["windows"]["count"] == local["windows"]["count"]
    for a, b in zip(local["signals"], site["signals"], strict=True):
        assert a["freq_hz"] == b["freq_hz"]
        for ch in range(2):
            for x, y in zip(a["rssi"][ch], b["rssi"][ch], strict=True):
                assert (x is None) == (y is None)
                assert x is None or abs(x - y) < 0.011

    states = {s["name"]: s for s in public("stations")["stations"]}
    assert states["Station de test 1"]["state"] == "running"
    assert states["Station de test 1"]["locator"] == "JN18"


def test_idempotent_and_owner_checks(stations: list[Station]) -> None:
    s1, s2 = stations
    uuid = run_campaign(s1, ("dipole", "long_wire"))
    pub = publisher(s1)
    pub.publish_pending()
    rows = len(public(f"campaigns/{uuid}")["signals"])
    # Sent again from scratch: the site replaces, never duplicates.
    with s1.db.transaction() as conn:
        conn.execute("DELETE FROM publication")
    pub.publish_pending()
    again = public(f"campaigns/{uuid}")
    assert len(again["signals"]) == rows
    # Station 2 cannot use the token of station 1 (bound to station 1).
    other = Publisher(s2.db, s2.config.model_copy(deep=True), s2._station_status)
    other._config.wordpress.token = TOKENS[0]
    with pytest.raises(PublishError, match="autre station"):
        other.publish_pending()
    # A wrong token.
    other._config.wordpress.token = "oav_" + "0" * 40
    with pytest.raises(PublishError, match="Jeton refusé"):
        other.publish_pending()


def test_unlisted_and_unknown(stations: list[Station]) -> None:
    s1, _ = stations
    uuid = run_campaign(s1, ("dipole", "delta_loop"))
    with s1.db.transaction() as conn:
        conn.execute("UPDATE campaigns SET visibility = 'unlisted' WHERE uuid = ?", (uuid,))
    publisher(s1).publish_pending()
    assert uuid not in {c["uuid"] for c in public("campaigns")["campaigns"]}
    assert public(f"campaigns/{uuid}")["campaign"]["uuid"] == uuid  # link only
    with pytest.raises(urllib.error.HTTPError) as exc:
        public("campaigns/inconnue-0000")
    assert exc.value.code == 404
    assert cm  # module used by the station side of these tests
