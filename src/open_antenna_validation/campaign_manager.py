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
from open_antenna_validation.detect import same_signal, select
from open_antenna_validation.engine import Q_ABSENT, Q_LOST, Q_MISALIGNED, Q_SATURATION
from open_antenna_validation.installation import current_chain
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

log = logging.getLogger(__name__)

CLASSES = ("carrier", "digital", "ssb", "am_fm")
INPUTS = "ABCDEFGHIJ"
EXCLUDED = Q_SATURATION | Q_LOST | Q_MISALIGNED
MAX_SIGNALS = 20
MAX_CHANNELS = 10
VOICE = ("ssb", "am_fm")
GIB = 1 << 30


class CampaignError(Exception):
    """Invalid campaign request or action (message in French)."""


def window_ms_for(cfg: Config, duration_s: float) -> int:
    hours = duration_s / 3600
    for upto_h, window_s in cfg.campaign.windows:
        if hours <= upto_h:
            return int(window_s * 1000)
    return int(cfg.campaign.windows[-1][1] * 1000)


def active_profile(db: Database, identity: str | list[str | None] | None, band: str) -> Any:
    """Profile of one SDR (switched mode) or of a set of SDRs in channel order (simultaneous)."""
    identities = identity if isinstance(identity, list) else [identity]
    if not identities or not all(identities):
        return None
    rows = db.query(
        "SELECT p.*, r.accepted_ms, r.uuid AS run_uuid, r.ended_ms, r.kind AS run_kind"
        " FROM hardware_profiles p"
        " JOIN qualification_runs r ON r.id = p.qualification_id"
        " WHERE p.active = 1 AND p.identities_json = ? AND p.band = ? AND"
        " (p.valid_until_ms IS NULL OR p.valid_until_ms > ?) ORDER BY p.created_ms DESC LIMIT 1",
        (json.dumps(identities), band, now_ms()),
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
                    request: dict[str, Any], devices: list[dict[str, Any]] | None = None,
                    free_bytes: int | None = None) -> str:
    """``devices``: reports of every SDR of the station (simultaneous mode), main one first."""
    if db.query("SELECT 1 FROM campaigns WHERE state IN ('running', 'interrupted')"):
        raise CampaignError("Une campagne est déjà en cours : arrêtez-la d'abord.")
    simultaneous = request.get("reception", "switched") == "simultaneous"
    if simultaneous:
        sdrs = _simultaneous_sdrs(request, devices or [receiver])
        bandwidth = min(float(d.get("bandwidth_hz") or 0) for d in sdrs)
        inputs = [str(d.get("input") or "") for d in sdrs]
    else:
        if receiver.get("state") != "running":
            raise CampaignError("Le récepteur doit être en marche pour lancer une campagne.")
        inputs = receiver.get("antennas") or []
        if len(inputs) < 2:
            raise CampaignError("Le mode commuté demande un SDR à plusieurs entrées.")
        inputs = inputs[:2]
        sdrs = [receiver]
        bandwidth = float(receiver.get("bandwidth_hz") or 0)
    title = str(request.get("title", "")).strip()
    auto = request.get("mode", "fixed") == "auto"
    max_signals = int(request.get("max_signals") or cfg.campaign.max_signals)
    if not 1 <= max_signals <= MAX_SIGNALS:
        raise CampaignError(f"De 1 à {MAX_SIGNALS} signaux suivis.")

    if auto:
        low, high = float(request.get("low_hz") or 0), float(request.get("high_hz") or 0)
        if high <= low:
            raise CampaignError("Indiquez une bande : Fmin doit être inférieure à Fmax.")
        if high - low + 2 * NOISE_REACH_HZ > bandwidth:
            raise CampaignError(
                f"La bande demandée fait {(high - low) / 1e3:.1f} kHz : elle doit tenir dans la "
                f"bande utile du SDR ({bandwidth / 1e3:.0f} kHz, moins le voisinage du bruit). "
                "Faites plusieurs campagnes.")
        center = (low + high) / 2
        sdr_hz = center  # detection keeps the signals away from the DC spike
        signals: list[dict[str, Any]] = []
    else:
        low, high, center, sdr_hz, signals = _fixed_frequencies(cfg, request, bandwidth)
    band = next((b.name for b in cfg.bands if b.low_hz <= center <= b.high_hz), "")
    if not 1e6 <= center <= 30e6:
        raise CampaignError("Les fréquences doivent être dans la bande HF (1 à 30 MHz).")
    return _create(db, cfg, request, sdrs, inputs, simultaneous, title, auto, max_signals,
                   low, high, center, sdr_hz, band, signals, free_bytes)


def _simultaneous_sdrs(request: dict[str, Any], devices: list[dict[str, Any]]
                       ) -> list[dict[str, Any]]:
    channels = request.get("channels") or []
    if not 2 <= len(channels) <= MAX_CHANNELS:
        raise CampaignError(f"Le mode simultané demande de 2 à {MAX_CHANNELS} SDR, "
                            "un par antenne.")
    by_serial = {(d.get("device") or {}).get("serial"): d for d in devices}
    sdrs = []
    for c in channels:
        serial = c.get("serial")
        d = by_serial.get(serial)
        if d is None:
            raise CampaignError(f"SDR inconnu : {serial}.")
        if d.get("state") != "running":
            raise CampaignError(f"Le SDR n° {serial} n'est pas en marche.")
        sdrs.append(d)
    if len({c.get("serial") for c in channels}) != len(channels):
        raise CampaignError("Un SDR par antenne : chaque SDR ne peut servir qu'une fois.")
    return sdrs


def _fixed_frequencies(cfg: Config, request: dict[str, Any], bandwidth: float
                       ) -> tuple[float, float, float, float, list[dict[str, Any]]]:
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
    # Keep the noise neighbourhood (5 kHz) inside the usable band too.
    if high - low + 10_000 > bandwidth:
        raise CampaignError(
            f"Les fréquences couvrent {(high - low) / 1e3:.1f} kHz : elles doivent tenir dans la "
            f"bande utile du SDR ({bandwidth / 1e3:.0f} kHz). Faites plusieurs campagnes.")
    center = (low + high) / 2
    return low, high, center, dc_free_tuning(signals, center, bandwidth), signals


def _create(db: Database, cfg: Config, request: dict[str, Any], sdrs: list[dict[str, Any]],
            inputs: list[str], simultaneous: bool, title: str, auto: bool, max_signals: int,
            low: float, high: float, center: float, sdr_hz: float, band: str,
            signals: list[dict[str, Any]], free_bytes: int | None) -> str:
    # Channels: the two inputs of one SDR (switched), or one SDR per antenna (simultaneous).
    aa = bool(request.get("aa"))
    channels = request.get("channels") or []
    n_ch = len(sdrs) if simultaneous else 2
    if len(channels) != n_ch:
        raise CampaignError("Choisissez une antenne pour chacune des entrées A et B.")
    antenna_ids = [int(c["antenna_id"]) for c in channels]
    for aid in antenna_ids:
        if not db.query("SELECT 1 FROM antennas WHERE id = ? AND archived = 0", (aid,)):
            raise CampaignError("Antenne introuvable dans le catalogue.")
    if aa and len(set(antenna_ids)) != 1:
        raise CampaignError("Une campagne A/A utilise la même antenne sur toutes les voies.")
    if not aa and len(set(antenna_ids)) != len(antenna_ids):
        raise CampaignError("Choisissez des antennes différentes (ou cochez « campagne A/A »).")
    reference = int(request.get("reference", 0))
    if not 0 <= reference < n_ch:
        raise CampaignError("Antenne de référence invalide.")
    receiver = sdrs[0]
    identities: list[str | None] = [d.get("identity") for d in sdrs]

    duration_s = float(request.get("duration_s", 0))
    if not 60 <= duration_s <= cfg.campaign.max_duration_days * 86_400:
        raise CampaignError(
            f"Durée hors plage (1 minute à {cfg.campaign.max_duration_days:g} jours).")
    rate_hz = float(request.get("rate_hz") or cfg.qualification.switch_rate_hz)

    # Hardware profile: bench of one SDR (switched) or A/A calibration of the set (simultaneous).
    profile = active_profile(db, identities if simultaneous else receiver.get("identity"), band)
    if profile is not None and profile["accepted_ms"] is None:
        raise CampaignError("Le profil de qualification de ce SDR n'est pas accepté : "
                            "acceptez ses limites dans l'onglet Qualification.")
    if profile is not None and not request.get("accept_limits"):
        raise CampaignError("Confirmez que vous acceptez les limites du profil de qualification.")
    if simultaneous:
        blanking = 0.0
    else:
        blanking = float(profile["blanking_ms"]) if profile is not None \
            else cfg.campaign.default_blanking_ms
    margins_json = json.loads(profile["margins_json"]) if profile is not None else {}
    correction = json.loads(profile["correction_json"]) if profile is not None else {}

    window_ms = window_ms_for(cfg, duration_s)
    period_s = min(cfg.campaign.period_s, window_ms / 1000 / 3)
    volume = estimate_volume_bytes(len(signals) or max_signals + 1, n_ch, duration_s, period_s,
                                   window_ms / 1000)
    if free_bytes is not None:
        need = max(int(volume * 1.2), volume + GIB)
        if free_bytes < need:
            raise CampaignError(
                f"Espace disque insuffisant : la campagne demande {need / GIB:.1f} Go avec la "
                f"marge, il reste {free_bytes / GIB:.1f} Go. Libérez de la place ou "
                "raccourcissez la campagne.")
    start = now_ms()
    uuid = str(uuidlib.uuid4())
    device = receiver.get("device") or {}
    models = [str((d.get("device") or {}).get("model", "SDR")) for d in sdrs]
    sdr_label = (f"{n_ch} × {models[0]}" if len(set(models)) == 1 else ", ".join(models)) \
        if simultaneous else f"{device.get('model', 'SDR')}"
    params = {
        "aa": aa, "auto": auto, "max_signals": max_signals,
        "rate_hz": rate_hz, "period_s": period_s, "window_ms": window_ms,
        "blanking_ms": blanking, "center_hz": center, "low_hz": low, "high_hz": high,
        "sdr_hz": sdr_hz, "sdr": sdr_label,
        "reception": "simultaneous" if simultaneous else "switched",
        "devices": [str((d.get("device") or {}).get("serial", "")) for d in sdrs],
        "identities": identities, "heterogeneous": len(set(models)) > 1,
        "identity": receiver.get("identity"), "inputs": inputs,
        "operator": cfg.station.callsign, "locator": cfg.station.locator,
        "timezone": cfg.station.timezone,
        "margins": margins_json.get("margins", {}),
        "correction": {"freqs_hz": correction.get("freqs_hz", []),
                       "port_db": correction.get("port_db", []),
                       "channels_db": correction.get("channels_db")},
        "qualification": None if profile is None else {
            "kind": profile["run_kind"], "run": profile["run_uuid"],
            "date_ms": profile["ended_ms"],
            "blanking_ms": profile["blanking_ms"], "useful": profile["useful_fraction"],
            "verdicts": margins_json.get("verdicts", []),
            "aa_campaign": margins_json.get("aa_campaign"),
            "alignment_ms": correction.get("alignment_ms")},
        "load_test": request.get("load_test"),
        "volume_bytes": volume,
        "antennas": [],
    }
    if not title:
        names = [db.query("SELECT name FROM antennas WHERE id = ?", (a,))[0]["name"]
                 for a in antenna_ids]
        title = f"A/A : {names[0]}" if aa else (
            f"{names[0]} contre {names[1]}" if n_ch == 2 else f"{n_ch} antennes : "
            + ", ".join(names))
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO campaigns (uuid, title, callsign, reception_mode, measure_mode, low_hz,"
            " high_hz, band, state, created_ms, start_ms, planned_end_ms, n_channels,"
            " reference_channel, profile_id, visibility, params_json) VALUES"
            " (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid, title, cfg.station.callsign, "simultaneous" if simultaneous else "switched",
             "auto" if auto else "fixed", low, high, band, start, start,
             start + int(duration_s * 1000), n_ch, reference,
             profile["id"] if profile is not None else None, request.get("visibility", "private"),
             json.dumps(params, ensure_ascii=False)))
        cid = int(cur.lastrowid or 0)
        chains = []
        for k, aid in enumerate(antenna_ids):
            chain = current_chain(db, aid)
            chains.append(chain["id"] if chain else None)
            hw = conn.execute("SELECT id FROM hardware WHERE identity = ?",
                              (identities[k] if simultaneous else receiver.get("identity"),)
                              ).fetchone()
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
    auto: bool = False
    surveying: bool = False
    last_survey_ms: int | None = None
    last_ts: int | None = None
    corrections: dict[int, list[float]] = field(default_factory=dict)
    reception: str = "switched"
    devices: list[str] = field(default_factory=list)
    # Last period reported by each channel: a window closes when every channel is past it.
    channel_last: dict[int, int] = field(default_factory=dict)
    # Wall time of each period start, per channel, until every channel has reported it.
    walls: dict[int, dict[int, float]] = field(default_factory=dict)
    align_ms: list[float] = field(default_factory=list)
    # Clock drift between SDRs: periods in a row beyond half the tolerance; resync asked.
    drift_count: int = 0
    resync: bool = False

    @property
    def simultaneous(self) -> bool:
        return self.reception == "simultaneous"


def load_running(db: Database) -> Running | None:
    rows = db.query("SELECT * FROM campaigns WHERE state IN ('running', 'interrupted')"
                    " ORDER BY start_ms DESC LIMIT 1")
    if not rows:
        return None
    c = rows[0]
    params = json.loads(c["params_json"])
    signals = [dict(r) for r in db.query(
        "SELECT id, freq_hz, class, width_hz FROM signals WHERE campaign_id = ?"
        " AND valid_to_ms IS NULL ORDER BY id", (c["id"],))]
    run = Running(uuid=c["uuid"], id=int(c["id"]), planned_end_ms=int(c["planned_end_ms"]),
                  window_ms=int(params["window_ms"]), reference=int(c["reference_channel"]),
                  n_ch=int(c["n_channels"]), params=params, signals=signals,
                  last_present_ms=int(c["start_ms"]), auto=c["measure_mode"] == "auto",
                  reception=c["reception_mode"], devices=list(params.get("devices") or []))
    last_ts = db.query("SELECT MAX(ts_ms) FROM measurements WHERE campaign_id = ?",
                       (run.id,))[0][0]
    run.last_ts = int(last_ts) if last_ts is not None else None
    _set_corrections(run)
    last = db.query("SELECT MAX(window_start_ms) FROM aggregates WHERE campaign_id = ?",
                    (run.id,))[0][0]
    run.window_start = None if last is None else int(last) + run.window_ms
    return run


def _set_corrections(run: Running) -> None:
    run.corrections.update(corrections_for(run.params, run.signals, run.n_ch, run.simultaneous))


def corrections_for(params: dict[str, Any], signals: list[Any], n_ch: int,
                    simultaneous: bool) -> dict[int, list[float]]:
    """Correction (dB) added to each channel's raw level, per signal, from the profile."""
    out: dict[int, list[float]] = {}
    corr = params.get("correction") or {}
    if corr.get("channels_db"):
        # Simultaneous mode: gain of each SDR against the reference, versus frequency.
        for s in signals:
            out[int(s["id"])] = [
                _interp(corr["freqs_hz"], corr["channels_db"][k], s["freq_hz"])
                if k < len(corr["channels_db"]) else 0.0 for k in range(n_ch)]
        return out
    if simultaneous:
        return out  # not calibrated yet: no correction (the switched bench does not apply)
    inputs = params.get("inputs") or []
    for s in signals:
        port = _interp(corr.get("freqs_hz", []), corr.get("port_db", []), s["freq_hz"])
        # port = A - B on the same signal: B reads low by port, A is the scale of reference.
        out[int(s["id"])] = [0.0 if k < len(inputs) and inputs[k] == "A" else port
                             for k in range(n_ch)]
    return out


def period_s(run: Running, cfg: Config) -> float:
    """Measurement period: slower while the band is calm."""
    normal = float(run.params["period_s"])
    return max(normal, cfg.campaign.calm_period_s) if run.calm else normal


def engine_start_ms(run: Running) -> int:
    """Start of the next measurement grid, the same for every SDR of the campaign."""
    if run.last_ts is None:
        return now_ms()
    return max(now_ms(), run.last_ts + int(run.params["period_s"] * 1000))


def engine_spec(run: Running, cfg: Config, channel: int = 0,
                start_ms: int | None = None) -> dict[str, Any]:
    p = run.params
    period = period_s(run, cfg)
    start = engine_start_ms(run) if start_ms is None else start_ms
    inputs = [p["inputs"][channel]] if run.simultaneous else p["inputs"]
    return {
        "campaign": run.uuid, "inputs": inputs, "channel_base": channel if run.simultaneous else 0,
        "align": run.simultaneous,
        "rate_hz": p["rate_hz"],
        "blanking_ms": p["blanking_ms"], "fft_size": cfg.spectrum.fft_size,
        "period_s": period, "start_ms": start, "clip_threshold": cfg.spectrum.clip_threshold,
        "clip_alert_ratio": cfg.spectrum.clip_alert_ratio, "center_hz": p["center_hz"],
        "sdr_hz": p["sdr_hz"], "display_points": cfg.spectrum.display_points,
        "signals": [{"id": s["id"], "offset_hz": s["freq_hz"] - p["sdr_hz"],
                     "width_hz": s["width_hz"], "class": s["class"]} for s in run.signals],
    }


def survey_request(run: Running, cfg: Config) -> dict[str, Any]:
    c = run.params
    return {"campaign": run.uuid, "sdr_hz": c["sdr_hz"], "low_hz": c["low_hz"],
            "high_hz": c["high_hz"], "seconds": cfg.campaign.survey_s,
            "fft_size": cfg.spectrum.fft_size, "detect_db": cfg.campaign.detect_db,
            "class_width_hz": cfg.campaign.class_width_hz,
            # Simultaneous mode: the first SDR surveys on its own input only.
            "inputs": [c["inputs"][0]] if run.simultaneous else None}


def load_test_spec(cfg: Config, device: dict[str, Any], test_id: str) -> dict[str, Any]:
    """Measurement at full load on one SDR: most signals, spread over the band, shortest
    period. Nothing is stored; only the processor time counts."""
    center = float(device.get("center_hz") or cfg.receiver.center_frequency_hz)
    usable = float(device.get("bandwidth_hz") or 0.0) * 0.8
    n = cfg.campaign.max_signals
    offsets = [usable * ((k + 0.5) / n - 0.5) for k in range(n)]
    return {
        "campaign": test_id, "inputs": [device.get("input") or ""], "channel_base": 0,
        "rate_hz": 1.0, "blanking_ms": 0.0, "fft_size": cfg.spectrum.fft_size,
        "period_s": 1.0, "start_ms": 0,  # one load sample per second
        "clip_threshold": cfg.spectrum.clip_threshold,
        "clip_alert_ratio": cfg.spectrum.clip_alert_ratio, "center_hz": center,
        "sdr_hz": center, "display_points": cfg.spectrum.display_points,
        "signals": [{"id": -1 - k, "offset_hz": off if abs(off) > 2000 else off + 4000,
                     "width_hz": 2700.0, "class": "ssb"} for k, off in enumerate(offsets)],
    }


def apply_survey(db: Database, run: Running, result: dict[str, Any], ts: int) -> dict[str, Any]:
    """New signal set after a survey: keep what is still there, close what vanished, add the
    best new signals up to the limit, and keep one noise point."""
    candidates = result.get("candidates", [])
    existing = [s for s in run.signals if s["class"] != "noise"]
    noise = [s for s in run.signals if s["class"] == "noise"]
    kept = [s for s in existing if any(same_signal(s, c) for c in candidates)]
    closed = [s for s in existing if s not in kept]
    fresh = [c for c in candidates if not any(same_signal(c, k) for k in kept)]
    added = select(fresh, int(run.params["max_signals"]), already=kept)
    quiet = result.get("quiet")
    for n in noise:
        if any(abs(n["freq_hz"] - a["freq_hz"]) < (n["width_hz"] + a["width_hz"]) / 2
               for a in added):
            closed.append(n)
            noise = []
    if not noise and quiet:
        added.append(quiet)
    with db.transaction() as conn:
        for s in closed:
            conn.execute("UPDATE signals SET valid_to_ms = ? WHERE id = ?", (ts, s["id"]))
        for s in added:
            conn.execute(
                "INSERT INTO signals (campaign_id, freq_hz, class, width_hz, source,"
                " valid_from_ms) VALUES (?, ?, ?, ?, 'auto', ?)",
                (run.id, s["freq_hz"], s["class"], s["width_hz"], ts))
    run.signals = [dict(r) for r in db.query(
        "SELECT id, freq_hz, class, width_hz FROM signals WHERE campaign_id = ?"
        " AND valid_to_ms IS NULL ORDER BY id", (run.id,))]
    _set_corrections(run)
    run.last_survey_ms = ts
    summary = {"campaign": run.uuid, "found": len(candidates), "kept": len(kept),
               "added": len([a for a in added if a["class"] != "noise"]),
               "closed": len([c for c in closed if c["class"] != "noise"]),
               "floor_dbfs": result.get("floor_dbfs")}
    db.log_event("survey", summary)
    return summary


def store_measurements(db: Database, cfg: Config, run: Running, event: dict[str, Any]) -> None:
    ts = int(event["ts_ms"])
    run.last_ts = max(run.last_ts or ts, ts)
    base = int(event.get("channel_base", 0))
    channels = [base] if run.simultaneous else list(range(run.n_ch))
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
    if not run.simultaneous or base == run.reference:
        _activity(db, cfg, run, ts, present)
    if run.simultaneous and event.get("wall_ms") is not None:
        _alignment(db, cfg, run, ts, base, float(event["wall_ms"]))
    for ch in channels:
        run.channel_last[ch] = max(run.channel_last.get(ch, ts), ts)
    if run.window_start is None:
        run.window_start = ts - ts % run.window_ms
    # A window closes once every channel has reported a later period.
    while all_reported(run) >= run.window_start + run.window_ms:
        aggregate_window(db, run, run.window_start)
        run.window_start += run.window_ms


def all_reported(run: Running) -> int:
    """Latest period every channel has reported (-1 while one is missing)."""
    if len(run.channel_last) < run.n_ch:
        return -1
    return min(run.channel_last.values())


def _alignment(db: Database, cfg: Config, run: Running, ts: int, channel: int,
               wall: float) -> None:
    """Spread of the wall times of the same period across the SDRs. Beyond the tolerance,
    voices (SSB, AM/FM), whose envelope changes within milliseconds, are not paired."""
    seen = run.walls.setdefault(ts, {})
    seen[channel] = wall
    if len(seen) < run.n_ch:
        for old in [t for t in run.walls if t < ts - 20 * run.window_ms]:
            del run.walls[old]
        return
    spread = max(seen.values()) - min(seen.values())
    del run.walls[ts]
    run.align_ms.append(round(spread, 2))
    run.align_ms = run.align_ms[-2000:]
    # The sample clocks of the SDRs drift apart (1 ppm: 86 ms a day): restart them all on a
    # common instant before the spread reaches the tolerance.
    run.drift_count = run.drift_count + 1 \
        if spread > cfg.campaign.align_tolerance_ms / 2 else 0
    if run.drift_count >= 3:
        run.drift_count = 0
        run.resync = True
    if spread > cfg.campaign.align_tolerance_ms:
        voices = [s["id"] for s in run.signals if s["class"] in VOICE]
        if voices:
            marks = ",".join("?" * len(voices))
            with db.transaction() as conn:
                conn.execute(
                    f"UPDATE measurements SET quality = quality | {Q_MISALIGNED}"
                    f" WHERE campaign_id = ? AND ts_ms = ? AND signal_id IN ({marks})",
                    (run.id, ts, *voices))


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


def finish(db: Database, run: Running, state: str, end_ms: int,
           cfg: Config | None = None) -> None:
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
        if run.simultaneous:
            calibrate_simultaneous(db, cfg, run, end_ms)
        else:
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


def calibrate_simultaneous(db: Database, cfg: Config | None, run: Running, end_ms: int
                           ) -> dict[str, Any] | None:
    """A/A campaign of the simultaneous mode = calibration of the set of SDRs.

    The same antenna feeds every SDR through a splitter: the median difference of each SDR
    against the reference, per signal, is its gain correction at that frequency (added to
    the correction already applied during the campaign). What remains around these medians
    is the error of the method: margin per class = 2 standard deviations of the window
    medians. The result is a new hardware profile for this set of SDRs and this band.
    """
    rows = db.query(
        "SELECT a.signal_id, s.freq_hz, s.class, a.channel_idx, a.dref_med FROM aggregates a"
        " JOIN signals s ON s.id = a.signal_id WHERE a.campaign_id = ? AND a.channel_idx != ?"
        " AND a.dref_med IS NOT NULL AND s.class != 'noise'", (run.id, run.reference))
    per: dict[tuple[int, int], list[float]] = {}
    info: dict[int, tuple[float, str]] = {}
    for r in rows:
        key = (int(r["signal_id"]), int(r["channel_idx"]))
        per.setdefault(key, []).append(float(r["dref_med"]))
        info[int(r["signal_id"])] = (float(r["freq_hz"]), str(r["class"]))
    if not per:
        db.log_event("error", {"calibration": run.uuid, "message": "aucune mesure exploitable"})
        return None
    sids = sorted(info, key=lambda k: info[k][0])
    freqs = [info[k][0] for k in sids]
    channels_db = [[0.0] * len(sids) for _ in range(run.n_ch)]
    residuals: dict[str, list[float]] = {}
    for (sid, ch), values in per.items():
        med = float(np.median(values))
        applied = run.corrections.get(sid, [0.0] * run.n_ch)
        channels_db[ch][sids.index(sid)] = round(applied[ch] - applied[run.reference] - med, 3)
        residuals.setdefault(info[sid][1], []).extend(v - med for v in values)
    margins = {cls: round(max(0.05, 2 * float(np.std(v, ddof=1))), 2)
               for cls, v in residuals.items() if len(v) >= 3}
    if margins:
        margins["all"] = max(margins.values())
    align = np.asarray(run.align_ms) if run.align_ms else None
    tol = cfg.campaign.align_tolerance_ms if cfg is not None else 5.0
    align_p99 = float(np.percentile(align, 99)) if align is not None else None
    labels = {"carrier": "Porteuses et CW", "digital": "Signaux numériques", "ssb": "SSB",
              "am_fm": "AM et FM"}
    verdicts = [{"class": cls, "label": labels.get(cls, cls),
                 "state": "warn" if cls in VOICE and align_p99 is not None and align_p99 > tol
                 else "ok", "rate_hz": None, "useful": 1.0, "margin_db": margins.get(cls)}
                for cls in ("carrier", "digital", "ssb", "am_fm")]
    c = db.query("SELECT * FROM campaigns WHERE id = ?", (run.id,))[0]
    identities = json.dumps(run.params.get("identities") or [])
    ts = now_ms()
    validity = (cfg.qualification.validity_days if cfg is not None else 180) * 86_400_000
    alignment = None if align is None else {
        "p50": round(float(np.percentile(align, 50)), 2), "p99": round(align_p99 or 0.0, 2)}
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO qualification_runs (uuid, kind, reception_mode, band, center_hz,"
            " sample_rate, hardware_json, started_ms, ended_ms, status, results_json,"
            " verdicts_json, accepted_ms) VALUES (?, 'aa', 'simultaneous', ?, ?, 0, ?, ?, ?,"
            " 'done', ?, ?, ?)",
            (str(uuidlib.uuid4()), c["band"], run.params["center_hz"], identities,
             c["start_ms"], end_ms,
             json.dumps({"aa_campaign": run.uuid, "alignment_ms": alignment}),
             json.dumps(verdicts, ensure_ascii=False), ts))
        conn.execute(
            "UPDATE hardware_profiles SET active = 0 WHERE identities_json = ? AND band = ?",
            (identities, c["band"]))
        conn.execute(
            "INSERT INTO hardware_profiles (qualification_id, identities_json, band, gain_json,"
            " switch_rate_hz, blanking_ms, useful_fraction, correction_json, margins_json,"
            " created_ms, valid_until_ms, active)"
            " VALUES (?, ?, ?, '{}', NULL, 0, 1, ?, ?, ?, ?, 1)",
            (int(cur.lastrowid or 0), identities, c["band"],
             json.dumps({"freqs_hz": freqs, "channels_db": channels_db, "alignment_ms": alignment}),
             json.dumps({"margins": margins, "verdicts": verdicts, "aa_campaign": run.uuid},
                        ensure_ascii=False), ts, ts + validity))
    db.log_event("calibration", {"campaign": run.uuid, "margins": margins,
                                 "alignment_ms": alignment})
    return {"margins": margins, "channels_db": channels_db, "freqs_hz": freqs}
