"""Campaigns in switched mode with imposed frequencies (cahier des charges, section 8).

Creation checks the request against the receiver and the hardware profile, then writes the
campaign, its channels (antenna and current receive chain), its first segment, its signals
and its first activity period. While it runs, the measurement events of the engine become
raw measurements; each closed window becomes aggregates, with the input correction of the
profile applied (raw measurements are never modified). The band is calm when no signal is
present for a while. An A/A campaign (same antenna on both inputs) ends by writing the
margins per signal class into the hardware profile.
"""

from __future__ import annotations

import json
import logging
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from open_antenna_validation.config import Config
from open_antenna_validation.engine import Q_ABSENT, Q_LOST, Q_SATURATION
from open_antenna_validation.installation import current_chain
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

log = logging.getLogger(__name__)

CLASSES = ("carrier", "digital", "ssb", "am_fm")
INPUTS = "ABCDEFGHIJ"
EXCLUDED = Q_SATURATION | Q_LOST
MAX_SIGNALS = 20


class CampaignError(Exception):
    """Invalid campaign request or action (message in French)."""


def window_ms_for(cfg: Config, duration_s: float) -> int:
    hours = duration_s / 3600
    for upto_h, window_s in cfg.campaign.windows:
        if hours <= upto_h:
            return int(window_s * 1000)
    return int(cfg.campaign.windows[-1][1] * 1000)


def active_profile(db: Database, identity: str | None, band: str) -> Any:
    if not identity:
        return None
    rows = db.query(
        "SELECT p.*, r.accepted_ms, r.uuid AS run_uuid, r.ended_ms FROM hardware_profiles p"
        " JOIN qualification_runs r ON r.id = p.qualification_id"
        " WHERE p.active = 1 AND p.identities_json = ? AND p.band = ? AND"
        " (p.valid_until_ms IS NULL OR p.valid_until_ms > ?) ORDER BY p.created_ms DESC LIMIT 1",
        (json.dumps([identity]), band, now_ms()),
    )
    return rows[0] if rows else None


def _interp(freqs: list[float], values: list[float], f: float) -> float:
    return float(np.interp(f, freqs, values)) if freqs else 0.0


def estimate_volume_bytes(n_signals: int, n_channels: int, duration_s: float,
                          period_s: float, window_s: float) -> int:
    rows = duration_s / period_s * n_signals * n_channels
    windows = duration_s / window_s * n_signals * (n_channels + n_channels * (n_channels - 1) / 2)
    return int(rows * 90 + windows * 260)


DC_GUARD_HZ = 2_000.0
NOISE_REACH_HZ = 5_000.0


def dc_free_tuning(signals: list[dict[str, Any]], center: float, bandwidth: float) -> float:
    """Tuning frequency that keeps every signal away from the DC spike and inside the band.

    Tries the centre first, then shifts by 1 kHz steps on either side.
    """
    half = bandwidth / 2
    for step in range(0, int(half // 1000) + 1):
        for shift in ((0.0,) if step == 0 else (step * 1000.0, -step * 1000.0)):
            tune = center + shift
            ok = all(
                abs(s["freq_hz"] - tune) >= s["width_hz"] / 2 + DC_GUARD_HZ
                and abs(s["freq_hz"] - tune) + s["width_hz"] / 2 + NOISE_REACH_HZ <= half
                for s in signals)
            if ok:
                return tune
    raise CampaignError("Impossible d'écarter toutes les fréquences du pic central du SDR "
                        "en les gardant dans sa bande utile : réduisez l'écart entre elles.")


def create_campaign(db: Database, cfg: Config, receiver: dict[str, Any],
                    request: dict[str, Any]) -> str:
    if receiver.get("state") != "running":
        raise CampaignError("Le récepteur doit être en marche pour lancer une campagne.")
    if db.query("SELECT 1 FROM campaigns WHERE state IN ('running', 'interrupted')"):
        raise CampaignError("Une campagne est déjà en cours : arrêtez-la d'abord.")
    inputs = receiver.get("antennas") or []
    if len(inputs) < 2:
        raise CampaignError("Le mode commuté demande un SDR à plusieurs entrées.")
    title = str(request.get("title", "")).strip()

    # Frequencies.
    freqs = request.get("frequencies") or []
    if not freqs:
        raise CampaignError("Indiquez au moins une fréquence à mesurer.")
    if len(freqs) > MAX_SIGNALS:
        raise CampaignError(f"Au plus {MAX_SIGNALS} fréquences par campagne.")
    signals = []
    for f in freqs:
        cls = f.get("class", "carrier")
        if cls not in CLASSES:
            raise CampaignError(f"Classe de signal inconnue : {cls}.")
        width = float(f.get("width_hz") or cfg.campaign.class_width_hz.get(cls, 200.0))
        signals.append({"freq_hz": float(f["freq_hz"]), "class": cls, "width_hz": width})
    low = min(s["freq_hz"] - s["width_hz"] / 2 for s in signals)
    high = max(s["freq_hz"] + s["width_hz"] / 2 for s in signals)
    bandwidth = float(receiver.get("bandwidth_hz") or 0)
    # Keep the noise neighbourhood (5 kHz) inside the usable band too.
    if high - low + 10_000 > bandwidth:
        raise CampaignError(
            f"Les fréquences couvrent {(high - low) / 1e3:.1f} kHz : elles doivent tenir dans la "
            f"bande utile du SDR ({bandwidth / 1e3:.0f} kHz). Faites plusieurs campagnes.")
    center = (low + high) / 2
    sdr_hz = dc_free_tuning(signals, center, bandwidth)
    band = next((b.name for b in cfg.bands if b.low_hz <= center <= b.high_hz), "")
    if not 1e6 <= center <= 30e6:
        raise CampaignError("Les fréquences doivent être dans la bande HF (1 à 30 MHz).")

    # Channels.
    aa = bool(request.get("aa"))
    channels = request.get("channels") or []
    if len(channels) != 2:
        raise CampaignError("Choisissez une antenne pour chacune des entrées A et B.")
    antenna_ids = [int(c["antenna_id"]) for c in channels]
    for aid in antenna_ids:
        if not db.query("SELECT 1 FROM antennas WHERE id = ? AND archived = 0", (aid,)):
            raise CampaignError("Antenne introuvable dans le catalogue.")
    if aa and antenna_ids[0] != antenna_ids[1]:
        raise CampaignError("Une campagne A/A utilise la même antenne sur les deux entrées.")
    if not aa and antenna_ids[0] == antenna_ids[1]:
        raise CampaignError("Choisissez deux antennes différentes (ou cochez « campagne A/A »).")
    reference = int(request.get("reference", 0))
    if reference not in (0, 1):
        raise CampaignError("Antenne de référence invalide.")

    duration_s = float(request.get("duration_s", 0))
    if not 60 <= duration_s <= cfg.campaign.max_duration_days * 86_400:
        raise CampaignError(
            f"Durée hors plage (1 minute à {cfg.campaign.max_duration_days:g} jours).")
    rate_hz = float(request.get("rate_hz") or cfg.qualification.switch_rate_hz)

    # Hardware profile.
    profile = active_profile(db, receiver.get("identity"), band)
    if profile is not None and profile["accepted_ms"] is None:
        raise CampaignError("Le profil de qualification de ce SDR n'est pas accepté : "
                            "acceptez ses limites dans l'onglet Qualification.")
    if profile is not None and not request.get("accept_limits"):
        raise CampaignError("Confirmez que vous acceptez les limites du profil de qualification.")
    blanking = float(profile["blanking_ms"]) if profile is not None \
        else cfg.campaign.default_blanking_ms
    margins_json = json.loads(profile["margins_json"]) if profile is not None else {}
    correction = json.loads(profile["correction_json"]) if profile is not None else {}

    window_ms = window_ms_for(cfg, duration_s)
    period_s = min(cfg.campaign.period_s, window_ms / 1000 / 3)
    start = now_ms()
    uuid = str(uuidlib.uuid4())
    device = receiver.get("device") or {}
    params = {
        "aa": aa, "rate_hz": rate_hz, "period_s": period_s, "window_ms": window_ms,
        "blanking_ms": blanking, "center_hz": center,
        "sdr_hz": sdr_hz, "sdr": f"{device.get('model', 'SDR')}",
        "identity": receiver.get("identity"), "inputs": inputs[:2],
        "operator": cfg.station.callsign, "locator": cfg.station.locator, "timezone": "",
        "margins": margins_json.get("margins", {}),
        "correction": {"freqs_hz": correction.get("freqs_hz", []),
                       "port_db": correction.get("port_db", [])},
        "qualification": None if profile is None else {
            "kind": "bench", "run": profile["run_uuid"], "date_ms": profile["ended_ms"],
            "blanking_ms": profile["blanking_ms"], "useful": profile["useful_fraction"],
            "verdicts": margins_json.get("verdicts", []),
            "aa_campaign": margins_json.get("aa_campaign")},
        "volume_bytes": estimate_volume_bytes(len(signals), 2, duration_s, period_s,
                                              window_ms / 1000),
        "antennas": [],
    }
    if not title:
        names = [db.query("SELECT name FROM antennas WHERE id = ?", (a,))[0]["name"]
                 for a in antenna_ids]
        title = f"A/A : {names[0]}" if aa else f"{names[0]} contre {names[1]}"
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO campaigns (uuid, title, callsign, reception_mode, measure_mode, low_hz,"
            " high_hz, band, state, created_ms, start_ms, planned_end_ms, n_channels,"
            " reference_channel, profile_id, visibility, params_json) VALUES"
            " (?, ?, ?, 'switched', 'fixed', ?, ?, ?, 'running', ?, ?, ?, 2, ?, ?, ?, ?)",
            (uuid, title, cfg.station.callsign, low, high, band, start, start,
             start + int(duration_s * 1000), reference,
             profile["id"] if profile is not None else None, request.get("visibility", "private"),
             json.dumps(params, ensure_ascii=False)))
        cid = int(cur.lastrowid or 0)
        chains = []
        for k, aid in enumerate(antenna_ids):
            chain = current_chain(db, aid)
            chains.append(chain["id"] if chain else None)
            hw = conn.execute("SELECT id FROM hardware WHERE identity = ?",
                              (receiver.get("identity"),)).fetchone()
            conn.execute(
                "INSERT INTO campaign_channels (campaign_id, idx, antenna_id, chain_id,"
                " hardware_id,"
                " input, color_slot) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, k, aid, chains[-1], hw["id"] if hw else None, inputs[k], k + 1))
        conn.execute(
            "INSERT INTO segments (campaign_id, idx, start_ms, reason, config_json)"
            " VALUES (?, 0, ?, '', ?)", (cid, start, json.dumps({"chains": chains})))
        for s in signals:
            conn.execute(
                "INSERT INTO signals (campaign_id, freq_hz, class, width_hz, source, valid_from_ms)"
                " VALUES (?, ?, ?, ?, 'fixed', ?)",
                (cid, s["freq_hz"], s["class"], s["width_hz"], start))
        conn.execute("INSERT INTO activity (campaign_id, state, start_ms) VALUES (?, 'active', ?)",
                     (cid, start))
    db.log_event("campaign_start", {"campaign": uuid, "aa": aa})
    return uuid


# --- running campaign ----------------------------------------------------------------


@dataclass
class Running:
    uuid: str
    id: int
    planned_end_ms: int
    window_ms: int
    reference: int
    n_ch: int
    params: dict[str, Any]
    signals: list[dict[str, Any]]
    window_start: int | None = None
    last_present_ms: int = 0
    calm: bool = False
    engine_started: bool = False
    interrupted_since: int | None = None
    corrections: dict[int, list[float]] = field(default_factory=dict)


def load_running(db: Database) -> Running | None:
    rows = db.query("SELECT * FROM campaigns WHERE state IN ('running', 'interrupted')"
                    " ORDER BY start_ms DESC LIMIT 1")
    if not rows:
        return None
    c = rows[0]
    params = json.loads(c["params_json"])
    signals = [dict(r) for r in db.query(
        "SELECT id, freq_hz, class, width_hz FROM signals WHERE campaign_id = ? ORDER BY id",
        (c["id"],))]
    run = Running(uuid=c["uuid"], id=int(c["id"]), planned_end_ms=int(c["planned_end_ms"]),
                  window_ms=int(params["window_ms"]), reference=int(c["reference_channel"]),
                  n_ch=int(c["n_channels"]), params=params, signals=signals,
                  last_present_ms=int(c["start_ms"]))
    corr = params.get("correction") or {}
    for s in signals:
        port = _interp(corr.get("freqs_hz", []), corr.get("port_db", []), s["freq_hz"])
        # port = A - B on the same signal: B reads low by port, A is the scale of reference.
        run.corrections[int(s["id"])] = [0.0 if params["inputs"][k] == "A" else port
                                         for k in range(run.n_ch)]
    last = db.query("SELECT MAX(window_start_ms) FROM aggregates WHERE campaign_id = ?",
                    (run.id,))[0][0]
    run.window_start = None if last is None else int(last) + run.window_ms
    return run


def engine_spec(run: Running, cfg: Config) -> dict[str, Any]:
    p = run.params
    return {
        "campaign": run.uuid, "inputs": p["inputs"], "rate_hz": p["rate_hz"],
        "blanking_ms": p["blanking_ms"], "fft_size": cfg.spectrum.fft_size,
        "period_s": p["period_s"], "clip_threshold": cfg.spectrum.clip_threshold,
        "clip_alert_ratio": cfg.spectrum.clip_alert_ratio, "center_hz": p["center_hz"],
        "sdr_hz": p["sdr_hz"], "display_points": cfg.spectrum.display_points,
        "signals": [{"id": s["id"], "offset_hz": s["freq_hz"] - p["sdr_hz"],
                     "width_hz": s["width_hz"], "class": s["class"]} for s in run.signals],
    }


def store_measurements(db: Database, cfg: Config, run: Running, event: dict[str, Any]) -> None:
    ts = int(event["ts_ms"])
    rows = []
    present = False
    for sid, ch, rssi, floor, quality, _n in event["values"]:
        snr = None if floor is None else rssi - floor
        if snr is not None and snr < cfg.campaign.presence_db:
            quality |= Q_ABSENT
        elif not quality & EXCLUDED:
            present = True
        rows.append((run.id, sid, ch, ts, rssi, floor, snr, quality))
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO measurements (campaign_id, signal_id, channel_idx, ts_ms, rssi_dbfs,"
            " floor_dbfs, snr_db, quality) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    _activity(db, cfg, run, ts, present)
    if run.window_start is None:
        run.window_start = ts - ts % run.window_ms
    while ts >= run.window_start + run.window_ms:
        aggregate_window(db, run, run.window_start)
        run.window_start += run.window_ms


def _activity(db: Database, cfg: Config, run: Running, ts: int, present: bool) -> None:
    if present:
        run.last_present_ms = ts
        if run.calm:
            switch_activity(db, run, "active", ts)
            run.calm = False
    elif not run.calm and ts - run.last_present_ms >= cfg.campaign.calm_after_s * 1000:
        calm_from = run.last_present_ms + int(cfg.campaign.calm_after_s * 1000)
        switch_activity(db, run, "calm", calm_from)
        run.calm = True


def switch_activity(db: Database, run: Running, state: str, ts: int, reason: str = "") -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE activity SET end_ms = ? WHERE campaign_id = ? AND end_ms IS NULL",
                     (ts, run.id))
        conn.execute("INSERT INTO activity (campaign_id, state, start_ms, reason)"
                     " VALUES (?, ?, ?, ?)", (run.id, state, ts, reason))


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {k: None for k in ("med", "p10", "p90", "min", "max", "sd")}
    a = np.asarray(values)
    return {"med": round(float(np.median(a)), 2), "p10": round(float(np.percentile(a, 10)), 2),
            "p90": round(float(np.percentile(a, 90)), 2), "min": round(float(np.min(a)), 2),
            "max": round(float(np.max(a)), 2),
            "sd": round(float(a.std(ddof=1)) if len(a) > 1 else 0.0, 2)}


Series = dict[int, tuple[float | None, float | None]]


def _paired(a: Series, b: Series) -> list[float]:
    """RSSI differences a − b over the periods where both are measured."""
    out = []
    for t, (va, _) in a.items():
        vb = b[t][0] if t in b else None
        if va is not None and vb is not None:
            out.append(va - vb)
    return out


def aggregate_window(db: Database, run: Running, start: int) -> None:
    end = start + run.window_ms
    rows = db.query(
        "SELECT signal_id, channel_idx, ts_ms, rssi_dbfs, floor_dbfs, quality FROM measurements"
        " WHERE campaign_id = ? AND ts_ms >= ? AND ts_ms < ?", (run.id, start, end))
    by_signal: dict[int, dict[int, dict[int, tuple[float | None, float | None]]]] = {}
    for r in rows:
        if r["quality"] & EXCLUDED:
            continue
        corr = run.corrections.get(int(r["signal_id"]), [0.0] * run.n_ch)[r["channel_idx"]]
        rssi = None if r["quality"] & Q_ABSENT or r["rssi_dbfs"] is None \
            else r["rssi_dbfs"] + corr
        floor = None if r["floor_dbfs"] is None else r["floor_dbfs"] + corr
        by_signal.setdefault(int(r["signal_id"]), {}).setdefault(int(r["channel_idx"]), {})[
            int(r["ts_ms"])] = (rssi, floor)
    out, pairs = [], []
    for sid, channels in by_signal.items():
        ref = channels.get(run.reference, {})
        for ch, series in channels.items():
            rssi = [v[0] for v in series.values() if v[0] is not None]
            floor = [v[1] for v in series.values() if v[1] is not None]
            snr = [v[0] - v[1] for v in series.values() if v[0] is not None and v[1] is not None]
            dref = _paired(series, ref)
            sr, sf, ss, sd = _stats(rssi), _stats(floor), _stats(snr), _stats(dref)
            out.append((run.id, sid, ch, start, run.window_ms, len(series),
                        sr["med"], sr["p10"], sr["p90"], sr["min"], sr["max"], sr["sd"],
                        sf["med"], sf["p10"], sf["p90"], ss["med"], ss["p10"], ss["p90"],
                        sd["med"], sd["p10"], sd["p90"], sd["sd"]))
        for a in channels:
            for b in channels:
                if a >= b:
                    continue
                d = _paired(channels[a], channels[b])
                st = _stats(d)
                pairs.append((run.id, sid, a, b, start, run.window_ms, "rssi", len(d),
                              st["med"], st["p10"], st["p90"], st["sd"]))
    with db.transaction() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO aggregates (campaign_id, signal_id, channel_idx,"
            " window_start_ms, window_ms, n, rssi_med, rssi_p10, rssi_p90, rssi_min, rssi_max,"
            " rssi_sd, floor_med, floor_p10, floor_p90, snr_med, snr_p10, snr_p90, dref_med,"
            " dref_p10, dref_p90, dref_sd) VALUES"
            " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", out)
        conn.executemany(
            "INSERT OR REPLACE INTO pair_aggregates (campaign_id, signal_id, channel_a, channel_b,"
            " window_start_ms, window_ms, metric, n, med, p10, p90, sd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", pairs)


def finish(db: Database, run: Running, state: str, end_ms: int) -> None:
    """Close the campaign: last windows, activity, state; A/A margins into the profile."""
    if run.window_start is not None:
        while run.window_start < end_ms:
            aggregate_window(db, run, run.window_start)
            run.window_start += run.window_ms
    with db.transaction() as conn:
        conn.execute("UPDATE activity SET end_ms = ? WHERE campaign_id = ? AND end_ms IS NULL",
                     (end_ms, run.id))
        conn.execute("UPDATE campaigns SET state = ?, end_ms = ? WHERE id = ?",
                     (state, end_ms, run.id))
    db.log_event("campaign_end", {"campaign": run.uuid, "state": state})
    if run.params.get("aa") and state == "done":
        aa_margins(db, run)


def aa_margins(db: Database, run: Running) -> dict[str, float] | None:
    """Same antenna on both inputs: what is left of B − A is the error of the method.

    Margin per class = |bias| + 2 standard deviations of the window medians, i.e. the
    smallest difference the method can claim over one window.
    """
    rows = db.query(
        "SELECT s.class, a.dref_med FROM aggregates a JOIN signals s ON s.id = a.signal_id"
        " WHERE a.campaign_id = ? AND a.channel_idx != ? AND a.dref_med IS NOT NULL",
        (run.id, run.reference))
    per_class: dict[str, list[float]] = {}
    for r in rows:
        per_class.setdefault(r["class"], []).append(float(r["dref_med"]))
    margins = {}
    for cls, values in per_class.items():
        if len(values) < 3:
            continue
        a = np.asarray(values)
        margins[cls] = round(abs(float(a.mean())) + 2 * float(a.std(ddof=1)), 2)
    if not margins:
        return None
    margins["all"] = max(margins.values())
    profile_id = db.query("SELECT profile_id FROM campaigns WHERE id = ?", (run.id,))[0][0]
    if profile_id is not None:
        stored = json.loads(db.query("SELECT margins_json FROM hardware_profiles WHERE id = ?",
                                     (profile_id,))[0][0])
        stored.update({"margins": margins, "aa_campaign": run.uuid,
                       "aa_window_s": run.window_ms / 1000})
        for v in stored.get("verdicts", []):
            v["margin_db"] = margins.get(v["class"])
        with db.transaction() as conn:
            conn.execute("UPDATE hardware_profiles SET margins_json = ? WHERE id = ?",
                         (json.dumps(stored, ensure_ascii=False), profile_id))
    db.log_event("aa_margins", {"campaign": run.uuid, "margins": margins})
    return margins
