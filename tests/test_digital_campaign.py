"""Digital campaign (P10) through the whole station: simulated decodable stations, the
real WSJT-X decoders, tracks. Skipped without WSJT-X."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation import installation as inst
from open_antenna_validation.acquisition import wait_for
from open_antenna_validation.config import CONFIG_FILENAME, Config, load_config, save_config
from open_antenna_validation.digital import wsjtx
from open_antenna_validation.service import Station

needs_wsjtx = pytest.mark.skipif(not wsjtx.find_tools().has("jt9"), reason="WSJT-X absent")


def _antennas(station: Station) -> list[int]:
    return [inst.save_antenna(station.db, {"type": t, "name": n})
            for t, n in (("dipole", "Dipôle"), ("vertical_quarter", "Verticale"))]


def test_digital_request_checks(station: Station) -> None:
    cfg = Config()
    assert cm.digital_params(cfg, {}, ["A", "B"], False) is None
    p = cm.digital_params(cfg, {"digital": "ft8", "band": "20 m"}, ["A", "B"], False)
    assert p is not None and p["dial_hz"] == 14_074_000 and p["block_ms"] == 60_000
    p = cm.digital_params(cfg, {"digital": "wspr", "band": "40 m"}, ["A", "B"], False)
    assert p is not None and p["block_ms"] == 240_000
    with pytest.raises(cm.CampaignError, match="Pas de sous-bande FT8"):
        cm.digital_params(cfg, {"digital": "ft8", "band": "CB 11 m"}, ["A", "B"], False)


@needs_wsjtx
def test_switched_ft8_campaign_decodes_measures_and_tracks(station_home: Path) -> None:
    path = station_home / CONFIG_FILENAME
    config = load_config(path)
    config.station.locator = "JN18"
    config.receiver.center_frequency_hz = 14_074_000.0
    config.campaign.period_s = 10.0
    save_config(config, path)
    station = Station.from_home(station_home, inline=True)
    try:
        station.startup()
        assert wait_for(lambda: station.receiver.get("state") == "running", 10)
        a, b = _antennas(station)
        uuid = station.start_campaign({
            "digital": "ft8", "band": "20 m", "duration_s": 240,
            "channels": [{"antenna_id": a}, {"antenna_id": b}], "accept_limits": True})
        assert wait_for(lambda: station.campaign is None, 900)
        cid = station.db.query("SELECT id FROM campaigns WHERE uuid = ?", (uuid,))[0]["id"]
        decodes = station.db.query("SELECT * FROM decodes WHERE campaign_id = ?", (cid,))
        assert len(decodes) >= 10
        # Switched: one channel per slot, every two slots in turn; all levels measured on
        # the tones of the re-encoded message (ft8code).
        by_slot = {}
        for d in decodes:
            by_slot.setdefault(d["slot_ms"], set()).add(d["channel_idx"])
        assert all(len(chs) == 1 for chs in by_slot.values())
        assert {next(iter(c)) for c in by_slot.values()} == {0, 1}
        assert all(d["guided"] == "tones" and d["level_dbfs"] is not None for d in decodes
                   if d["decoded"])
        tracks = station.db.query("SELECT * FROM tracks WHERE campaign_id = ?", (cid,))
        # jt9 sometimes decodes a call out of the noise: such a track never validates.
        calls = {t["callsign"] for t in tracks if t["state"] == "validated"}
        assert calls and calls <= {"F6SIM", "F5SIM", "G4SIM", "DL1SIM", "I2SIM", "EA7SIM",
                                   "LA9SIM", "SV1SIM", "K1SIM", "PY2SIM", "ZS6SIM", "JA1SIM"}
        assert all(t["closed"] == 1 for t in tracks)  # the end of the campaign closes them
        assert any(t["state"] == "validated" for t in tracks)
        k1 = [t for t in tracks if t["callsign"] == "K1SIM" and t["locator"]]
        if k1:
            assert 5000 < k1[0]["distance_km"] < 6200 and k1[0]["propagation_class"] == "dxn"
    finally:
        station.shutdown()
        station.db.close()


def test_gaps_by_pivot_and_by_pairs(station: Station) -> None:
    """Switched: B against A interpolated around it; simultaneous: same slot."""
    from open_antenna_validation.digital.results import _paired, _pivot, digital_results

    # A fades linearly (-90 → -84 dB), B sits 3 dB under the A of its instant.
    switched = [(0, 0, -90.0), (30, 1, -92.0), (60, 0, -88.0), (90, 1, -90.0),
                (120, 0, -86.0), (150, 1, -88.0)]
    assert _pivot(switched, 1, 0) == pytest.approx([-3.0, -3.0])  # the last B has no A after
    simultaneous = [(0, 0, -90.0), (0, 1, -93.5), (15, 0, -80.0), (15, 1, -82.5), (30, 0, -85.0)]
    assert _paired(simultaneous, 1, 0) == pytest.approx([-3.5, -2.5])
    db = station.db
    with db.transaction() as conn:
        cid = int(conn.execute(
            "INSERT INTO campaigns (uuid, reception_mode, measure_mode, low_hz, high_hz, state,"
            " created_ms, n_channels) VALUES ('d1', 'simultaneous', 'fixed', 14e6, 14.1e6,"
            " 'done', 0, 2)").lastrowid or 0)
        tid = int(conn.execute(
            "INSERT INTO tracks (campaign_id, callsign, locator, distance_km, azimuth_deg,"
            " propagation_class, state, first_ms, last_ms, takeoff_deg) VALUES"
            " (?, 'DL1SIM', 'JO62', 830, 54, 'skip', 'validated', 0, 30, 33)", (cid,)).lastrowid
                  or 0)
        for slot, ch, level, decoded in ((0, 0, -90.0, 1), (0, 1, -93.0, 0), (15, 0, -91.0, 1),
                                         (15, 1, -94.0, 1)):
            conn.execute(
                "INSERT INTO decodes (campaign_id, track_id, channel_idx, slot_ms, digital_mode,"
                " callsign, message, level_dbfs, decoded) VALUES (?, ?, ?, ?, 'FT8', 'DL1SIM',"
                " 'CQ DL1SIM JO62', ?, ?)", (cid, tid, ch, slot, level, decoded))
    res = digital_results(db, cid, 2, 0, True, "JN18")
    assert res["method"] == "paired" and res["overall"][1]["median"] == -3.0
    st = res["stations"][0]
    assert st["call"] == "DL1SIM" and st["decoded_by"] == [0, 1] and st["gap"][1]["n"] == 2
    assert res["groups"]["class"]["skip"][1]["n"] == 2
    assert res["groups"]["azimuth"]["30"][1]["median"] == -3.0
    assert res["decoded_sets"] == {"0,1": 1}


def test_csv_export_of_decodes_and_tracks(station: Station) -> None:
    from open_antenna_validation.export import export_csv, filename

    db = station.db
    with db.transaction() as conn:
        cid = int(conn.execute(
            "INSERT INTO campaigns (uuid, reception_mode, measure_mode, low_hz, high_hz, state,"
            " created_ms, start_ms, n_channels, band) VALUES ('e1', 'switched', 'fixed', 14e6,"
            " 14.1e6, 'done', 0, 0, 2, '20 m')").lastrowid or 0)
        tid = int(conn.execute(
            "INSERT INTO tracks (campaign_id, callsign, state, first_ms, last_ms, n_measures)"
            " VALUES (?, 'K1SIM', 'validated', 0, 15000, 3)", (cid,)).lastrowid or 0)
        conn.execute(
            "INSERT INTO decodes (campaign_id, track_id, channel_idx, slot_ms, digital_mode,"
            " callsign, message, level_dbfs, decoded, snr_db) VALUES (?, ?, 1, 15000, 'FT8',"
            " 'K1SIM', 'CQ K1SIM FN42', -112.25, 1, -14)", (cid, tid))
    lines = "".join(export_csv(db, "e1", "decodes", french=True)).splitlines()
    assert lines[0].lstrip("\ufeff").startswith("slot_utc;slot_ms;mode;channel")
    assert ";B;" in lines[1] and "-112,25" in lines[1] and lines[1].endswith(";validated")
    tracks = "".join(export_csv(db, "e1", "tracks")).splitlines()
    assert tracks[1].startswith(f"{tid},K1SIM,,validated")
    assert filename(db, "e1", "tracks").endswith("-pistes.csv")


def test_published_digital_summary_hides_the_precise_locator(station: Station) -> None:
    import json

    import jsonschema

    from open_antenna_validation.campaigns import campaign_meta

    db = station.db
    params = {"locator": "JN18EU", "digital": {
        "mode": "ft8", "dial_hz": 14_074_000.0, "band": "20 m", "block_ms": 60_000, "seed": 1,
        "classes": {"ground_km": 40, "nvis_km": 400, "skip_km": 1000, "one_hop_km": 3800,
                    "f2_height_km": 300}}}
    with db.transaction() as conn:
        cid = int(conn.execute(
            "INSERT INTO campaigns (uuid, reception_mode, measure_mode, low_hz, high_hz, state,"
            " created_ms, start_ms, n_channels, band, params_json) VALUES ('p1', 'simultaneous',"
            " 'fixed', 14e6, 14.1e6, 'done', 0, 0, 2, '20 m', ?)",
            (json.dumps(params),)).lastrowid or 0)
        for idx in (0, 1):
            conn.execute("INSERT INTO campaign_channels (campaign_id, idx, color_slot) VALUES"
                         " (?, ?, ?)", (cid, idx, idx + 1))
        for call, loc, dist, az in (("DL1SIM", "JO62", 834.4, 54.2), ("ZZ1SIM", "", 1234.5, 77.7)):
            tid = int(conn.execute(
                "INSERT INTO tracks (campaign_id, callsign, locator, distance_km, azimuth_deg,"
                " propagation_class, state, first_ms, last_ms, approximate) VALUES"
                " (?, ?, ?, ?, ?, 'skip', 'validated', 0, 0, ?)",
                (cid, call, loc, dist, az, int(not loc))).lastrowid or 0)
            for ch, level in ((0, -90.0), (1, -92.0)):
                conn.execute(
                    "INSERT INTO decodes (campaign_id, track_id, channel_idx, slot_ms,"
                    " digital_mode, callsign, message, level_dbfs, decoded) VALUES"
                    " (?, ?, ?, 0, 'FT8', ?, 'x', ?, 1)", (cid, tid, ch, call, level))
    local = campaign_meta(db, "p1")["digital"]
    assert local["home"]["locator"] == "JN18EU"
    assert local["stations"][0]["distance_km"] == 834.4  # as measured from the full locator
    published = campaign_meta(db, "p1", locator_chars=4)
    d = published["digital"]
    assert d["home"]["locator"] == "JN18"
    by_call = {s["call"]: s for s in d["stations"]}
    assert by_call["DL1SIM"]["distance_km"] != 834.4  # from the centre of JN18
    assert by_call["ZZ1SIM"]["distance_km"] == 1200 and by_call["ZZ1SIM"]["azimuth_deg"] == 80
    schema = json.loads((Path(__file__).resolve().parents[1] / "docs" / "wordpress"
                         / "ingest.schema.json").read_text(encoding="utf-8"))
    meta_schema = {**schema["$defs"]["meta"], "$defs": schema["$defs"]}
    jsonschema.validate(json.loads(json.dumps(d)), meta_schema["properties"]["digital"])
