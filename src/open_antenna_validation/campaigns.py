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
            "sdr": params.get("sdr", ""), "operator": params.get("operator", r["callsign"]),
            "antenna_types": [ANTENNA_TYPES.get(t, t) for t in (r["types"] or "").split(",") if t],
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
            "type": ANTENNA_TYPES.get(ch["type"], ch["type"]),
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

    windows = db.query(
        "SELECT MIN(window_start_ms) AS first, MAX(window_start_ms) AS last, MAX(window_ms) AS step"
        " FROM aggregates WHERE campaign_id = ?", (cid,),
    )[0]
    first, step = windows["first"], windows["step"]
    count = 0 if first is None else (windows["last"] - first) // step + 1
    n_ch = int(c["n_channels"])

    signals = []
    for sig in db.query(
        "SELECT id, freq_hz, class, width_hz FROM signals WHERE campaign_id = ? ORDER BY freq_hz",
        (cid,),
    ):
        rssi: list[list[float | None]] = [[None] * count for _ in range(n_ch)]
        floor: list[list[float | None]] = [[None] * count for _ in range(n_ch)]
        for a in db.query(
            "SELECT channel_idx, window_start_ms, rssi_med, floor_med FROM aggregates"
            " WHERE campaign_id = ? AND signal_id = ?", (cid, sig["id"]),
        ):
            i = (a["window_start_ms"] - first) // step
            rssi[a["channel_idx"]][i] = a["rssi_med"]
            floor[a["channel_idx"]][i] = a["floor_med"]
        signals.append({"freq_hz": sig["freq_hz"], "class": sig["class"],
                        "width_hz": sig["width_hz"], "rssi": rssi, "floor": floor})

    activity = [dict(r) for r in db.query(
        "SELECT state, start_ms, end_ms FROM activity WHERE campaign_id = ? ORDER BY start_ms",
        (cid,),
    )]

    return {
        "campaign": {
            "uuid": c["uuid"], "title": c["title"], "band": c["band"], "low_hz": c["low_hz"],
            "high_hz": c["high_hz"], "state": c["state"], "reception_mode": c["reception_mode"],
            "measure_mode": c["measure_mode"], "start_ms": c["start_ms"], "end_ms": c["end_ms"],
            "planned_end_ms": c["planned_end_ms"], "aa": bool(params.get("aa")),
            "n_channels": n_ch, "reference_channel": c["reference_channel"],
            "demo": bool(params.get("demo")), "sdr": params.get("sdr", ""),
            "operator": params.get("operator", c["callsign"]), "locator": params.get("locator", ""),
            "timezone": params.get("timezone", ""),
            "margins": params.get("margins", {}), "qualification": params.get("qualification"),
            "spacing_note": params.get("spacing_note", ""),
        },
        "channels": channels,
        "segments": segments,
        "windows": {"start_ms": first, "step_ms": step, "count": count},
        "signals": signals,
        "activity": activity,
    }
