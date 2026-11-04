"""Data quality in the result set, CSV export."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.service import Station
from tests.test_campaign_engine import antennas, body, client  # noqa: F401  (fixture)


def run_campaign(c: TestClient) -> str:
    station: Station = c.station  # type: ignore[attr-defined]
    r = c.post("/api/campaign", json=body(antennas(c)))
    assert r.status_code == 200, r.text
    assert wait_for(lambda: station.campaign is None, 60)
    return str(r.json()["uuid"])


def rows(text: str, delimiter: str) -> list[list[str]]:
    assert text.startswith("﻿")  # spreadsheets read the accents
    return list(csv.reader(io.StringIO(text[1:]), delimiter=delimiter))


def test_quality_and_exports(client: TestClient) -> None:  # noqa: F811
    station: Station = client.station  # type: ignore[attr-defined]
    demo = client.get("/api/campaigns/demo-40m-2/results").json()["quality"]
    assert demo["demo"] and not demo["raw"] and demo["channels"][0]["n"] > 0

    uuid = run_campaign(client)
    q = client.get(f"/api/campaigns/{uuid}/results").json()["quality"]
    n_meas = station.db.query("SELECT COUNT(*) FROM measurements")[0][0]
    assert q["raw"] and not q["demo"]
    assert sum(c["n"] for c in q["channels"]) == n_meas
    assert q["interruptions"] == []

    r = client.get(f"/api/campaigns/{uuid}/export", params={"kind": "aggregates"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "agregats.csv" in r.headers["content-disposition"]
    agg = rows(r.text, ",")
    assert agg[0][:3] == ["window_start_utc", "window_start_ms", "window_s"]
    cid = station.db.query("SELECT id FROM campaigns WHERE uuid = ?", (uuid,))[0][0]
    n_agg = station.db.query("SELECT COUNT(*) FROM aggregates WHERE campaign_id = ?",
                             (cid,))[0][0]
    assert len(agg) - 1 == n_agg
    assert {row[5] for row in agg[1:]} == {"A", "B"}
    assert {row[6] for row in agg[1:]} == {"Dipôle", "Verticale"}

    r = client.get(f"/api/campaigns/{uuid}/export",
                   params={"kind": "measurements", "dialect": "fr"})
    meas = rows(r.text, ";")
    head = meas[0]
    assert head[0] == "ts_utc" and len(meas) - 1 == n_meas
    first = dict(zip(head, meas[1], strict=True))
    assert first["ts_utc"].endswith("Z") and "," in first["rssi_dbfs"]  # decimal comma
    assert first["quality"] in ("OK", "ABSENT")

    assert client.get(f"/api/campaigns/{uuid}/export", params={"kind": "x"}).status_code == 400
    assert client.get("/api/campaigns/nope/export").status_code == 404
