"""Campaign queries for the result pages: list and full result set.

The result set is the contract shared by the local page and, later, the WordPress plugin:
per signal and per channel, the window medians as measured at the SDR (installation view);
the page adds the chain losses itself for the antenna view, from the segments.
"""

from __future__ import annotations

import json
from typing import Any

from open_antenna_validation.installation import (
    ANTENNA_TYPES,
    POLARIZATIONS,
    chain_elements,
    element_loss_db,
)
from open_antenna_validation.storage import Database

LETTERS = "ABCDEFGHIJ"


class CampaignNotFound(Exception):
    pass


def _params(row: Any) -> dict[str, Any]:
    params: dict[str, Any] = json.loads(row["params_json"] or "{}")
    return params


def list_campaigns(db: Database, *, show_demo: bool = True) -> list[dict[str, Any]]:
    """Campaigns, most recent first. Demonstration ones only while no real campaign exists."""
    rows = db.query(
        "SELECT c.*, (SELECT GROUP_CONCAT(a.type, ',') FROM campaign_channels ch"
        " JOIN antennas a ON a.id = ch.antenna_id WHERE ch.campaign_id = c.id) AS types"
        " FROM campaigns c ORDER BY COALESCE(c.start_ms, c.created_ms) DESC"
    )
    items = []
    for r in rows:
        params = _params(r)
        items.append({
            "uuid": r["uuid"], "title": r["title"], "band": r["band"], "low_hz": r["low_hz"],
            "high_hz": r["high_hz"], "state": r["state"], "reception_mode": r["reception_mode"],
            "measure_mode": r["measure_mode"], "start_ms": r["start_ms"], "end_ms": r["end_ms"],
            "n_channels": r["n_channels"], "demo": bool(params.get("demo")),
            "visibility": r["visibility"],
            "sdr": params.get("sdr", ""), "operator": params.get("operator", r["callsign"]),
            "antenna_types": [ANTENNA_TYPES.get(t, t) for t in (r["types"] or "").split(",") if t],
            "type_codes": sorted({t for t in (r["types"] or "").split(",") if t}),
        })
    real = [c for c in items if not c["demo"]]
    if real or not show_demo:
        return real
    return items


def _chain_losses(db: Database, chain_id: int | None, band: str, freq_hz: float) -> dict[str, Any]:
    """Losses of a chain at the campaign centre frequency (per-band values first)."""
    if chain_id is None:
        return {"elements": [], "loss_db": 0.0}
    elements = []
    for row, e in zip(
        db.query("SELECT db_json FROM chain_elements WHERE chain_id = ? ORDER BY position",
                 (chain_id,)),
        chain_elements(db, chain_id), strict=True,
    ):
        per_band = json.loads(row["db_json"] or "{}").get(band)
        value = per_band if per_band is not None else element_loss_db(e, freq_hz)
        elements.append({"kind": e["kind"], "label": e["label"], "db": round(value, 2)})
    return {"elements": elements, "loss_db": round(sum(x["db"] for x in elements), 2)}


def campaign_results(db: Database, uuid: str) -> dict[str, Any]:
    meta = campaign_meta(db, uuid)
    cid = int(db.query("SELECT id FROM campaigns WHERE uuid = ?", (uuid,))[0]["id"])
    n_ch = int(meta["campaign"]["n_channels"])
    windows = db.query(
        "SELECT MIN(window_start_ms) AS first, MAX(window_start_ms) AS last, MAX(window_ms) AS step"
        " FROM aggregates WHERE campaign_id = ?", (cid,),
    )[0]
    first, step = windows["first"], windows["step"]
    count = 0 if first is None else (windows["last"] - first) // step + 1
    signals = []
    for sig in meta["signals"]:
        rssi: list[list[float | None]] = [[None] * count for _ in range(n_ch)]
        floor: list[list[float | None]] = [[None] * count for _ in range(n_ch)]
        for a in db.query(
            "SELECT channel_idx, window_start_ms, rssi_med, floor_med FROM aggregates"
            " WHERE campaign_id = ? AND signal_id = ?", (cid, sig["key"]),
        ):
            i = (a["window_start_ms"] - first) // step
            rssi[a["channel_idx"]][i] = a["rssi_med"]
            floor[a["channel_idx"]][i] = a["floor_med"]
        signals.append({**sig, "rssi": rssi, "floor": floor})
    quality = meta.pop("quality")
    return {**meta, "windows": {"start_ms": first, "step_ms": step, "count": count},
            "signals": signals, "quality": quality}


def campaign_meta(db: Database, uuid: str, *, locator_chars: int = 6) -> dict[str, Any]:
    """Everything about a campaign but the window values: the part of the result set that
    is also published as is, and changes only now and then (segments, activity, quality)."""
    rows = db.query("SELECT * FROM campaigns WHERE uuid = ?", (uuid,))
    if not rows:
        raise CampaignNotFound(uuid)
    c = rows[0]
    params = _params(c)
    cid = int(c["id"])
    shorts = {a["letter"]: a["short"] for a in params.get("antennas", [])}

    channels = []
    for ch in db.query(
        "SELECT ch.idx, ch.color_slot, a.* FROM campaign_channels ch"
        " LEFT JOIN antennas a ON a.id = ch.antenna_id WHERE ch.campaign_id = ? ORDER BY ch.idx",
        (cid,),
    ):
        letter = LETTERS[int(ch["idx"])]
        channels.append({
            "idx": ch["idx"], "letter": letter, "color_slot": ch["color_slot"],
            "name": ch["name"], "short": shorts.get(letter, ch["name"]),
            "type": ANTENNA_TYPES.get(ch["type"], ch["type"]), "type_code": ch["type"],
            "height": ch["height_note"] or (f"{ch['height_m']} m" if ch["height_m"] else ""),
            "polarization": POLARIZATIONS.get(ch["polarization"], ch["polarization"]),
            "orientation": ch["orientation_note"],
        })

    segments = []
    for s in db.query(
        "SELECT idx, start_ms, end_ms, reason, config_json FROM segments"
        " WHERE campaign_id = ? ORDER BY idx", (cid,),
    ):
        chains = json.loads(s["config_json"] or "{}").get("chains", [])
        segments.append({
            "idx": s["idx"], "start_ms": s["start_ms"], "end_ms": s["end_ms"],
            "reason": s["reason"],
            "chains": [_chain_losses(db, x, c["band"], (c["low_hz"] + c["high_hz"]) / 2)
                       for x in chains],
        })

    n_ch = int(c["n_channels"])
    windows = db.query("SELECT COUNT(DISTINCT window_start_ms) FROM aggregates"
                       " WHERE campaign_id = ?", (cid,))[0][0]
    signals = [{"key": int(s["id"]), "freq_hz": s["freq_hz"], "class": s["class"],
                "width_hz": s["width_hz"]}
               for s in db.query("SELECT id, freq_hz, class, width_hz FROM signals"
                                 " WHERE campaign_id = ? ORDER BY freq_hz", (cid,))]

    activity = [dict(r) for r in db.query(
        "SELECT state, start_ms, end_ms, reason FROM activity WHERE campaign_id = ?"
        " ORDER BY start_ms", (cid,),
    )]

    return {
        "campaign": {
            "uuid": c["uuid"], "title": c["title"], "band": c["band"], "low_hz": c["low_hz"],
            "high_hz": c["high_hz"], "state": c["state"], "reception_mode": c["reception_mode"],
            "measure_mode": c["measure_mode"], "start_ms": c["start_ms"], "end_ms": c["end_ms"],
            "planned_end_ms": c["planned_end_ms"], "aa": bool(params.get("aa")),
            "n_channels": n_ch, "reference_channel": c["reference_channel"],
            "demo": bool(params.get("demo")), "sdr": params.get("sdr", ""),
            "operator": params.get("operator", c["callsign"]),
            "locator": str(params.get("locator", ""))[:locator_chars],
            "timezone": params.get("timezone", ""),
            "margins": params.get("margins", {}), "qualification": params.get("qualification"),
            "load_test": params.get("load_test"),
            "spacing_note": params.get("spacing_note", ""),
        },
        "channels": channels,
        "segments": segments,
        "signals": signals,
        "activity": activity,
        "quality": _quality(db, cid, n_ch, activity, params, windows, len(signals)),
    }


# Quality bits of the measurements (engine.py), in the order of the table of the page.
QUALITY_CODES = [(1, "saturation"), (2, "lost"), (8, "misaligned"), (4, "absent")]


def _quality(db: Database, cid: int, n_ch: int, activity: list[dict[str, Any]],
             params: dict[str, Any], windows: int, n_signals: int) -> dict[str, Any]:
    """Measurements set aside, per channel and per code, and interruptions."""
    rows = {int(r["channel_idx"]): r for r in db.query(
        "SELECT channel_idx, COUNT(*) AS n,"
        + ", ".join(f" SUM(quality & {bit} != 0) AS {name}" for bit, name in QUALITY_CODES)
        + " FROM measurements WHERE campaign_id = ? GROUP BY channel_idx", (cid,))}
    channels = []
    for k in range(n_ch):
        r = rows.get(k)
        channels.append({"idx": k, "n": int(r["n"]) if r else 0,
                         **{name: int(r[name] or 0) if r else 0 for _, name in QUALITY_CODES}})
    interruptions = [{"start_ms": a["start_ms"], "end_ms": a["end_ms"],
                      "reason": a.get("reason") or ""}
                     for a in activity if a["state"] == "interrupted"]
    if params.get("demo") and not rows:
        # Demonstrations have no raw measurements: plausible figures, marked as such.
        n = windows * n_signals * 30
        channels = [{"idx": k, "n": n, "saturation": n // 900 * (k % 2), "lost": n // 2500,
                     "misaligned": n // 400 if params.get("antennas") and k else 0,
                     "absent": n // 12} for k in range(n_ch)]
    return {"channels": channels, "interruptions": interruptions,
            "raw": bool(rows), "demo": bool(params.get("demo"))}
