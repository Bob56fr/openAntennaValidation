"""CSV export of a campaign: window aggregates, or raw measurements.

Two dialects: standard (comma, decimal point; spreadsheets set to English, Python, R) and
French spreadsheet (semicolon, decimal comma). Rows are streamed: a month of raw
measurements is several million lines. Raw levels are never modified; the correction of
the qualification profile is given in its own column.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation.campaigns import LETTERS, CampaignNotFound
from open_antenna_validation.engine import QUALITY_LABELS
from open_antenna_validation.storage import Database

KINDS = ("aggregates", "measurements", "decodes", "tracks")
FILE_WORDS = {"aggregates": "agregats", "measurements": "mesures", "decodes": "decodages",
              "tracks": "pistes"}
BATCH = 5000


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _quality_text(q: int) -> str:
    return "+".join(label for bit, label in QUALITY_LABELS.items() if q & bit) or "OK"


class _Writer:
    def __init__(self, french: bool) -> None:
        self.french = french
        self.buf = io.StringIO()
        self.w = csv.writer(self.buf, delimiter=";" if french else ",", lineterminator="\r\n")

    def row(self, values: list[Any]) -> str:
        cells = []
        for v in values:
            if isinstance(v, float):
                text = f"{v:.3f}".rstrip("0").rstrip(".")
                cells.append(text.replace(".", ",") if self.french else text)
            else:
                cells.append("" if v is None else v)
        self.w.writerow(cells)
        out = self.buf.getvalue()
        self.buf.seek(0)
        self.buf.truncate()
        return out


def filename(db: Database, uuid: str, kind: str) -> str:
    rows = db.query("SELECT band, start_ms FROM campaigns WHERE uuid = ?", (uuid,))
    if not rows:
        raise CampaignNotFound(uuid)
    day = datetime.fromtimestamp((rows[0]["start_ms"] or 0) / 1000, UTC).strftime("%Y%m%d")
    band = (rows[0]["band"] or "hf").replace(" ", "")
    return f"oav-{band}-{day}-{uuid[:8]}-{FILE_WORDS[kind]}.csv"


def export_csv(db: Database, uuid: str, kind: str, *, french: bool = False) -> Iterator[str]:
    """Lines of the CSV file (UTF-8 with BOM, so that spreadsheets read the accents)."""
    rows = db.query("SELECT * FROM campaigns WHERE uuid = ?", (uuid,))
    if not rows:
        raise CampaignNotFound(uuid)
    if kind not in KINDS:
        raise ValueError(kind)
    c = rows[0]
    cid = int(c["id"])
    names = {int(r["idx"]): r["name"] or "" for r in db.query(
        "SELECT ch.idx, a.name FROM campaign_channels ch LEFT JOIN antennas a"
        " ON a.id = ch.antenna_id WHERE ch.campaign_id = ?", (cid,))}
    signals = {int(s["id"]): s for s in db.query(
        "SELECT id, freq_hz, class, width_hz FROM signals WHERE campaign_id = ?", (cid,))}
    w = _Writer(french)
    yield "\ufeff"
    if kind == "aggregates":
        yield w.row(["window_start_utc", "window_start_ms", "window_s", "freq_hz", "class",
                     "channel", "antenna", "n", "rssi_med_dbfs", "rssi_p10", "rssi_p90",
                     "rssi_sd", "floor_med_dbfs", "snr_med_db", "dref_med_db", "dref_p10",
                     "dref_p90"])
        query = ("SELECT * FROM aggregates WHERE campaign_id = ?"
                 " ORDER BY window_start_ms, signal_id, channel_idx")
        for r in _batches(db, query, cid):
            s = signals.get(int(r["signal_id"]))
            ch = int(r["channel_idx"])
            yield w.row([_iso(r["window_start_ms"]), r["window_start_ms"], r["window_ms"] / 1000,
                         s["freq_hz"] if s else None, s["class"] if s else "",
                         LETTERS[ch], names.get(ch, ""), r["n"], r["rssi_med"], r["rssi_p10"],
                         r["rssi_p90"], r["rssi_sd"], r["floor_med"], r["snr_med"],
                         r["dref_med"], r["dref_p10"], r["dref_p90"]])
        return
    if kind == "decodes":
        # One line per message, slot and channel: decoded there, or only measured there.
        yield w.row(["slot_utc", "slot_ms", "mode", "channel", "antenna", "decoded", "message",
                     "callsign", "locator", "snr_db", "dt_s", "audio_hz", "power_dbm",
                     "level_dbfs", "floor_dbfs", "guided", "flags", "track_id", "track_state"])
        query = ("SELECT d.*, t.state AS track_state FROM decodes d LEFT JOIN tracks t"
                 " ON t.id = d.track_id WHERE d.campaign_id = ? ORDER BY d.slot_ms, d.message,"
                 " d.channel_idx")
        for r in _batches(db, query, cid):
            ch = int(r["channel_idx"])
            yield w.row([_iso(r["slot_ms"]), r["slot_ms"], r["digital_mode"], LETTERS[ch],
                         names.get(ch, ""), int(r["decoded"]), r["message"], r["callsign"],
                         r["locator"], r["snr_db"], r["dt_s"], r["audio_hz"], r["power_dbm"],
                         r["level_dbfs"], r["floor_dbfs"], r["guided"], r["flags"],
                         r["track_id"], r["track_state"] or ""])
        return
    if kind == "tracks":
        yield w.row(["track_id", "callsign", "mode", "state", "reject_reason", "locator",
                     "locator_changed", "country", "approximate_position", "distance_km",
                     "azimuth_deg", "propagation_class", "takeoff_deg", "audio_hz", "parity",
                     "measured_slots", "qso_end", "first_utc", "last_utc"])
        query = "SELECT * FROM tracks WHERE campaign_id = ? ORDER BY first_ms, id"
        for r in _batches(db, query, cid):
            yield w.row([r["id"], r["callsign"], r["digital_mode"], r["state"],
                         r["reject_reason"], r["locator"], int(r["locator_changed"]),
                         r["country"], int(r["approximate"]), r["distance_km"],
                         r["azimuth_deg"], r["propagation_class"], r["takeoff_deg"],
                         r["audio_hz"], r["parity"], r["n_measures"], int(r["qso_end"]),
                         _iso(r["first_ms"]), _iso(r["last_ms"])])
        return
    params = json.loads(c["params_json"] or "{}")
    corrections = cm.corrections_for(params, list(signals.values()), int(c["n_channels"]),
                                     c["reception_mode"] == "simultaneous")
    yield w.row(["ts_utc", "ts_ms", "freq_hz", "class", "channel", "antenna", "rssi_dbfs",
                 "floor_dbfs", "snr_db", "quality", "quality_bits", "correction_db"])
    query = ("SELECT * FROM measurements WHERE campaign_id = ?"
             " ORDER BY ts_ms, signal_id, channel_idx")
    for r in _batches(db, query, cid):
        sid, ch = int(r["signal_id"]), int(r["channel_idx"])
        s = signals.get(sid)
        corr = corrections.get(sid)
        yield w.row([_iso(r["ts_ms"]), r["ts_ms"], s["freq_hz"] if s else None,
                     s["class"] if s else "", LETTERS[ch], names.get(ch, ""), r["rssi_dbfs"],
                     r["floor_dbfs"], r["snr_db"], _quality_text(int(r["quality"])),
                     int(r["quality"]), round(corr[ch], 3) if corr else 0.0])


def _batches(db: Database, query: str, cid: int) -> Iterator[Any]:
    offset = 0
    while True:
        rows = db.query(f"{query} LIMIT {BATCH} OFFSET {offset}", (cid,))
        yield from rows
        if len(rows) < BATCH:
            return
        offset += BATCH
