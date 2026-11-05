"""Results of a digital campaign: gap between antennas per station, then by distance,
azimuth, take-off angle and propagation class; stations decoded by each antenna.

Only validated tracks count. A gap is antenna − reference, in dB, on the guided levels:

- simultaneous reception: the same transmission on every channel (same slot), paired;
- switched reception: pivot method (cahier des charges, section 10): every measurement
  on an antenna is compared with the reference interpolated between its measurements just
  before and just after, on the same station (the fading in between is assumed linear).
"""

from __future__ import annotations

import math
import statistics
from itertools import combinations, pairwise
from typing import Any

from open_antenna_validation.digital import messages
from open_antenna_validation.digital.tracks import REASON_LABELS, STATE_LABELS
from open_antenna_validation.storage import Database

DISTANCE_BINS_KM = (0, 100, 400, 1000, 2000, 3800, 6000, 10000, 20100)
AZIMUTH_SECTOR_DEG = 30
ANGLE_BINS_DEG = (0, 5, 10, 15, 20, 30, 45, 60, 90)


def _summary(gaps: list[float]) -> dict[str, Any]:
    if not gaps:
        return {"n": 0, "median": None, "mean": None, "ci95": None}
    mean = statistics.fmean(gaps)
    ci = 1.96 * statistics.stdev(gaps) / math.sqrt(len(gaps)) if len(gaps) > 1 else None
    return {"n": len(gaps), "median": round(statistics.median(gaps), 2),
            "mean": round(mean, 2), "ci95": None if ci is None else round(ci, 2)}


def _pivot(points: list[tuple[int, int, float]], channel: int, reference: int) -> list[float]:
    """points: (slot_ms, channel, level dB) of one station, sorted by slot."""
    refs = [(t, lv) for t, ch, lv in points if ch == reference]
    gaps = []
    for t, ch, lv in points:
        if ch != channel:
            continue
        before = [r for r in refs if r[0] < t]
        after = [r for r in refs if r[0] > t]
        if not before or not after:
            continue
        (t1, l1), (t2, l2) = before[-1], after[0]
        gaps.append(lv - (l1 + (l2 - l1) * (t - t1) / (t2 - t1)))
    return gaps


def _paired(points: list[tuple[int, int, float]], channel: int, reference: int) -> list[float]:
    by_slot: dict[int, dict[int, float]] = {}
    for t, ch, lv in points:
        by_slot.setdefault(t, {})[ch] = lv
    return [v[channel] - v[reference] for v in by_slot.values()
            if channel in v and reference in v]


def _bin(value: float | None, edges: tuple[int, ...]) -> str | None:
    if value is None:
        return None
    for lo, hi in pairwise(edges):
        if lo <= value < hi:
            return f"{lo}-{hi}"
    return None


def digital_results(db: Database, cid: int, n_ch: int, reference: int, simultaneous: bool,
                    station_locator: str) -> dict[str, Any]:
    tracks = db.query("SELECT * FROM tracks WHERE campaign_id = ? ORDER BY first_ms", (cid,))
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for t in tracks:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
        if t["state"] == "rejected":
            reasons[t["reject_reason"]] = reasons.get(t["reject_reason"], 0) + 1
    rows = db.query(
        "SELECT d.track_id, d.slot_ms, d.channel_idx, d.level_dbfs, d.decoded, d.snr_db"
        " FROM decodes d JOIN tracks t ON t.id = d.track_id"
        " WHERE d.campaign_id = ? AND t.state = 'validated' AND d.level_dbfs IS NOT NULL"
        " AND d.flags NOT LIKE '%overlap%' ORDER BY d.slot_ms", (cid,))
    per_track: dict[int, list[tuple[int, int, float]]] = {}
    decoded_by: dict[str, set[int]] = {}
    for r in rows:
        per_track.setdefault(int(r["track_id"]), []).append(
            (int(r["slot_ms"]), int(r["channel_idx"]), float(r["level_dbfs"])))
    for r in db.query(
            "SELECT t.callsign, d.channel_idx FROM decodes d JOIN tracks t ON t.id = d.track_id"
            " WHERE d.campaign_id = ? AND t.state = 'validated' AND d.decoded = 1", (cid,)):
        decoded_by.setdefault(r["callsign"], set()).add(int(r["channel_idx"]))
    gap_of = _paired if simultaneous else _pivot
    others = [c for c in range(n_ch) if c != reference]
    stations: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, dict[int, list[float]]]] = {
        "class": {}, "distance": {}, "azimuth": {}, "angle": {}}
    overall: dict[int, list[float]] = {c: [] for c in others}
    home = messages.grid_to_latlon(station_locator) if station_locator else None
    for t in tracks:
        if t["state"] != "validated":
            continue
        call = t["callsign"]
        st = stations.setdefault(call, {
            "call": call, "locator": t["locator"], "country": t["country"],
            "approximate": bool(t["approximate"]), "distance_km": t["distance_km"],
            "azimuth_deg": t["azimuth_deg"], "class": t["propagation_class"],
            "takeoff_deg": t["takeoff_deg"], "tracks": 0, "slots": 0,
            "gaps": {c: [] for c in others}, "decoded_by": sorted(decoded_by.get(call, ()))})
        st["tracks"] += 1
        points = per_track.get(int(t["id"]), [])
        st["slots"] += len({p[0] for p in points})
        if t["locator"] and not st["locator"]:
            st.update(locator=t["locator"], distance_km=t["distance_km"],
                      azimuth_deg=t["azimuth_deg"], approximate=False)
        for c in others:
            st["gaps"][c].extend(gap_of(points, c, reference))
    for st in stations.values():
        keys = {"class": st["class"] or None,
                "distance": _bin(st["distance_km"], DISTANCE_BINS_KM),
                "azimuth": None if st["azimuth_deg"] is None else str(
                    int(st["azimuth_deg"] // AZIMUTH_SECTOR_DEG) * AZIMUTH_SECTOR_DEG),
                "angle": _bin(st["takeoff_deg"], ANGLE_BINS_DEG)}
        for c in others:
            gaps = st["gaps"][c]
            overall[c].extend(gaps)
            for name, key in keys.items():
                if key is not None and gaps:
                    groups[name].setdefault(key, {}).setdefault(c, []).extend(gaps)
        st["gap"] = {c: _summary(st["gaps"][c]) for c in others}
        del st["gaps"]
        if home is not None and st["locator"]:
            st["lat"], st["lon"] = messages.grid_to_latlon(st["locator"])
    # Stations decoded by each combination of antennas (Venn up to 3, UpSet beyond).
    sets: dict[str, int] = {}
    for chans in decoded_by.values():
        key = ",".join(str(c) for c in sorted(chans))
        sets[key] = sets.get(key, 0) + 1
    exclusive = {c: sum(1 for ch in decoded_by.values() if ch == {c}) for c in range(n_ch)}
    pairs = {f"{a},{b}": sum(1 for ch in decoded_by.values() if {a, b} <= ch)
             for a, b in combinations(range(n_ch), 2)}
    return {
        "counts": {"states": counts, "reasons": reasons,
                   "state_labels": STATE_LABELS, "reason_labels": REASON_LABELS},
        "reference": reference, "method": "paired" if simultaneous else "pivot",
        "overall": {c: _summary(v) for c, v in overall.items()},
        "groups": {name: {k: {c: _summary(v) for c, v in by_ch.items()}
                          for k, by_ch in g.items()} for name, g in groups.items()},
        "class_labels": messages.CLASS_LABELS,
        "stations": sorted(stations.values(), key=lambda s: (s["distance_km"] is None,
                                                             s["distance_km"] or 0)),
        "decoded_sets": sets, "exclusive": exclusive, "pairs": pairs,
        "home": None if home is None else {"lat": home[0], "lon": home[1],
                                           "locator": station_locator},
    }
