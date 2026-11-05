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
        calls = {t["callsign"] for t in tracks}
        assert calls <= {"F6SIM", "F5SIM", "G4SIM", "DL1SIM", "I2SIM", "EA7SIM", "LA9SIM",
                         "SV1SIM", "K1SIM", "PY2SIM", "ZS6SIM", "JA1SIM"}
        assert all(t["closed"] == 1 for t in tracks)  # the end of the campaign closes them
        assert any(t["state"] == "validated" for t in tracks)
        k1 = [t for t in tracks if t["callsign"] == "K1SIM" and t["locator"]]
        if k1:
            assert 5000 < k1[0]["distance_km"] < 6200 and k1[0]["propagation_class"] == "dxn"
    finally:
        station.shutdown()
        station.db.close()
