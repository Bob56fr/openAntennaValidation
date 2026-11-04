"""Demonstration campaigns (cahier des charges, section 13; decision D9).

Two fictitious 40 m campaigns over 5 days, with 2 and 6 antennas, written into the real
schema (antennas, chains, campaign, channels, segments, signals, aggregates, activity) so
that the result pages are exercised exactly as with a real campaign. They are flagged
``demo`` in ``params_json``, never published and hidden as soon as a real campaign exists.

The propagation model is the one of the P0 mock-ups: a smooth day/night weight from the
sun elevation at the station, each antenna with its own day and night gain and noise,
a slow fading common to all antennas, and per-window measurement noise.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

DEMO_LOCATOR = "JN18eu"
DEMO_LAT, DEMO_LON = 48.854, 2.375
DEMO_CALLSIGN = "DÉMO"
# 12 h 00 at the station.
START_MS = int(datetime(2026, 11, 3, 11, 0, tzinfo=UTC).timestamp() * 1000)
WINDOW_MS = 15 * 60_000
N_WINDOWS = 5 * 96
SEGMENT_AT = 2.5 * 96 - 8  # window index where the coax of B is replaced
UTC_OFFSET_H = 1.0  # Europe/Paris in November

BAND = "40 m"
LOW_HZ, HIGH_HZ = 7_000_000.0, 7_200_000.0
CLASS_SCALE = {"carrier": 0.8, "digital": 0.9, "ssb": 1.7}
# Level of the class (dBFS at the antenna feed), day / night; noise in the measurement width.
CLASS_BASE = {"carrier": (-96.0, -101.0), "digital": (-104.0, -108.0), "ssb": (-89.0, -93.0)}
CLASS_NOISE = {"carrier": (-128.0, -121.0), "digital": (-131.0, -124.0), "ssb": (-114.0, -107.0)}
CLASS_WIDTH = {"carrier": 200.0, "digital": 50.0, "ssb": 2_700.0}

# freq (Hz), class, level offset (dB)
SIGNALS = [
    (7_003_500.0, "carrier", 4.0), (7_023_000.0, "carrier", -1.0), (7_038_600.0, "digital", -2.0),
    (7_047_500.0, "digital", 1.0), (7_074_000.0, "digital", 3.0), (7_090_000.0, "ssb", 1.0),
    (7_145_000.0, "ssb", 4.0), (7_182_000.0, "ssb", -2.0),
]


@dataclass(frozen=True)
class DemoAntenna:
    letter: str
    type: str
    name: str
    short: str
    height: str
    polarization: str
    orientation: str
    chain: tuple[tuple[str, str, float], ...]  # (kind, label, loss dB at 7.1 MHz)
    chain2: tuple[tuple[str, str, float], ...] | None
    day: float
    night: float
    noise_day: float
    noise_night: float


ANTENNAS = [
    DemoAntenna("A", "dipole", "Dipôle 40 m", "dipôle", "8 m", "horizontal", "Axe 110° / 290°",
                (("coax", "RG213, 25 m", 0.5),), None, 0.0, 0.0, 0.0, 0.0),
    DemoAntenna("B", "vertical_quarter", "Verticale ¼ λ, 16 radians", "verticale", "Pied au sol",
                "vertical", "Omnidirectionnelle",
                (("coax", "RG58, 40 m", 2.1), ("relay", "Relais", 0.2)),
                (("coax", "RG213, 40 m", 0.8),), -4.6, 2.4, 2.2, 3.1),
    DemoAntenna("C", "delta_loop", "Delta loop 40 m", "delta loop", "Sommet à 12 m", "horizontal",
                "Plan 20° / 200°", (("coax", "H155, 30 m", 0.8),), None, 1.6, 1.1, -0.4, -0.6),
    DemoAntenna("D", "efhw", "EFHW 40–10 m", "EFHW", "10 m", "horizontal", "Axe 60° / 240°",
                (("coax", "RG58, 15 m", 0.8), ("balun", "Unun 49:1", 1.0)), None,
                -0.4, 0.9, 0.3, 0.4),
    DemoAntenna("E", "long_wire", "Long fil 20 m", "long fil", "7 m", "mixed", "Fil vers 150°",
                (("coax", "RG213, 10 m", 0.2), ("balun", "Unun 9:1", 0.8)), None,
                -2.1, -1.2, 1.6, 1.4),
    DemoAntenna("F", "mini_whip", "Mini-whip active", "mini-whip", "Mât de 6 m", "vertical",
                "Omnidirectionnelle", (("coax", "RG58, 20 m", 1.0),), None, -9.5, -7.2, -8.4, -6.1),
]

DEMOS = {
    "demo-40m-2": {"title": "Dipôle contre verticale ¼ λ (démonstration)", "antennas": 2,
                   "sdr": "2 × SDRplay RSP2 Pro", "seed": 20261103,
                   "margins": {"carrier": 0.2, "digital": 0.3, "ssb": 0.9, "all": 0.4},
                   "qualification": {"date": "1er nov. 2026", "duration": "6 h",
                                     "bias": "+0,15 dB (corrigé)", "alignment_ms": 1.8,
                                     "load_cpu_pct": 34}},
    "demo-40m-6": {"title": "Six antennes filaires et verticales (démonstration)", "antennas": 6,
                   "sdr": "6 × RTL-SDR Blog V4", "seed": 20261104,
                   "margins": {"carrier": 0.4, "digital": 0.5, "ssb": 1.4, "all": 0.7},
                   "qualification": {"date": "1er nov. 2026", "duration": "8 h",
                                     "bias": "de −0,6 à +0,4 dB selon la voie (corrigé)",
                                     "alignment_ms": 3.1, "load_cpu_pct": 71}},
}


def sun_elevation(ts_ms: float, lat: float = DEMO_LAT, lon: float = DEMO_LON) -> float:
    """Sun elevation in degrees (NOAA/USNO approximation, same as the result pages)."""
    r = math.pi / 180
    d = ts_ms / 864e5 - 10957.5
    g = (357.529 + 0.98560028 * d) * r
    q = (280.459 + 0.98564736 * d) % 360
    lam = (q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) * r
    e = (23.439 - 0.00000036 * d) * r
    ra = math.atan2(math.cos(e) * math.sin(lam), math.cos(lam)) / r
    dec = math.asin(math.sin(e) * math.sin(lam))
    eot = ((q - ra + 180) % 360 - 180) * 4
    utc_h = (ts_ms / 36e5) % 24
    solar = (utc_h + lon / 15 + eot / 60) % 24
    ha = (solar - 12) * 15 * r
    return math.asin(math.sin(lat * r) * math.sin(dec)
                     + math.cos(lat * r) * math.cos(dec) * math.cos(ha)) / r


def _smooth(a: float, b: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _loss(antenna: DemoAntenna, window: int) -> float:
    chain = antenna.chain2 if (antenna.chain2 and window >= SEGMENT_AT) else antenna.chain
    return sum(e[2] for e in chain)


def ensure_demo_campaigns(db: Database) -> int:
    """Create the demonstration campaigns if missing. Returns the number created."""
    created = 0
    for uuid, spec in DEMOS.items():
        if db.query("SELECT 1 FROM campaigns WHERE uuid = ?", (uuid,)):
            continue
        _create(db, uuid, spec)
        created += 1
    return created


def _create(db: Database, uuid: str, spec: dict[str, Any]) -> None:
    rng = np.random.default_rng(spec["seed"])
    ants = ANTENNAS[: spec["antennas"]]
    n_ch = len(ants)
    created = now_ms()
    end_ms = START_MS + N_WINDOWS * WINDOW_MS
    seg_ms = START_MS + int(SEGMENT_AT) * WINDOW_MS

    # Per-window context, shared by all signals.
    ts = START_MS + np.arange(N_WINDOWS) * WINDOW_MS
    elev = np.array([sun_elevation(t + WINDOW_MS / 2) for t in ts])
    w = np.array([_smooth(-6, 12, e) for e in elev])
    local_h = ((ts / 36e5 + UTC_OFFSET_H) % 24).astype(int)
    day_idx = np.arange(N_WINDOWS) // 96
    calm = np.isin(day_idx, (1, 3)) & (local_h >= 3) & (local_h < 5)
    day_off = rng.normal(0, 0.35, (n_ch, 6))

    with db.transaction() as conn:
        antenna_ids: list[int] = []
        chain_ids: list[int | None] = []
        chain2_ids: list[int | None] = []
        for a in ants:
            cur = conn.execute(
                "INSERT INTO antennas (uuid, type, name, polarization, height_note,"
                " orientation_note, created_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{uuid}-{a.letter}", a.type, a.name, a.polarization, a.height, a.orientation,
                 created),
            )
            antenna_ids.append(int(cur.lastrowid or 0))
            for chain, ids in ((a.chain, chain_ids), (a.chain2, chain2_ids)):
                if chain is None:
                    ids.append(None)
                    continue
                c = conn.execute("INSERT INTO chains (antenna_id, created_ms) VALUES (?, ?)",
                                 (antenna_ids[-1], created))
                chain_id = int(c.lastrowid or 0)
                for pos, (kind, label, loss) in enumerate(chain):
                    conn.execute(
                        "INSERT INTO chain_elements (chain_id, position, kind, label, db_json)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (chain_id, pos, kind, label, json.dumps({BAND: loss})),
                    )
                ids.append(chain_id)

        params = {
            "demo": True, "sdr": spec["sdr"], "locator": DEMO_LOCATOR,
            "timezone": "Europe/Paris", "operator": DEMO_CALLSIGN,
            "margins": spec["margins"], "qualification": spec["qualification"],
            "antennas": [{"letter": a.letter, "short": a.short} for a in ants],
            "spacing_note": "Antennes distantes de 18 m (environ 0,4 λ à 7,1 MHz)." if n_ch == 2
            else "Six antennes sur un même terrain (distances saisies : de 9 à 25 m).",
        }
        cur = conn.execute(
            "INSERT INTO campaigns (uuid, title, callsign, reception_mode, measure_mode, low_hz,"
            " high_hz, band, state, created_ms, start_ms, planned_end_ms, end_ms, n_channels,"
            " reference_channel, visibility, params_json) VALUES"
            " (?, ?, ?, 'simultaneous', 'auto', ?, ?, ?, 'done', ?, ?, ?, ?, ?, 0, 'none', ?)",
            (uuid, spec["title"], DEMO_CALLSIGN, LOW_HZ, HIGH_HZ, BAND, created, START_MS, end_ms,
             end_ms, n_ch, json.dumps(params, ensure_ascii=False)),
        )
        cid = int(cur.lastrowid or 0)
        for k in range(n_ch):
            conn.execute(
                "INSERT INTO campaign_channels (campaign_id, idx, antenna_id, chain_id, color_slot)"
                " VALUES (?, ?, ?, ?, ?)", (cid, k, antenna_ids[k], chain_ids[k], k + 1))
        for idx, (start, end, reason, ids) in enumerate((
            (START_MS, seg_ms, "", chain_ids),
            (seg_ms, end_ms, "Coax de B remplacé (RG58 → RG213)",
             [c2 or c1 for c1, c2 in zip(chain_ids, chain2_ids, strict=True)]),
        )):
            if n_ch < 2 and idx == 1:
                break
            conn.execute(
                "INSERT INTO segments (campaign_id, idx, start_ms, end_ms, reason, config_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (cid, idx, start, end, reason, json.dumps({"chains": ids})),
            )

        # Activity: calm periods between active ones.
        state, since = "active", START_MS
        for i in range(N_WINDOWS + 1):
            now_state = "calm" if i < N_WINDOWS and calm[i] else "active"
            if i == N_WINDOWS or now_state != state:
                conn.execute(
                    "INSERT INTO activity (campaign_id, state, start_ms, end_ms)"
                    " VALUES (?, ?, ?, ?)",
                    (cid, state, since, int(ts[i]) if i < N_WINDOWS else end_ms))
                if i < N_WINDOWS:
                    state, since = now_state, int(ts[i])

        rows: list[tuple[Any, ...]] = []
        for freq, cls, level in SIGNALS:
            sig = conn.execute(
                "INSERT INTO signals (campaign_id, freq_hz, class, width_hz, source, valid_from_ms,"
                " valid_to_ms) VALUES (?, ?, ?, ?, 'auto', ?, ?)",
                (cid, freq, cls, CLASS_WIDTH[cls], START_MS, end_ms))
            sid = int(sig.lastrowid or 0)
            scale = CLASS_SCALE[cls]
            base = CLASS_BASE[cls][0] * w + CLASS_BASE[cls][1] * (1 - w) + level
            noise = CLASS_NOISE[cls][0] * w + CLASS_NOISE[cls][1] * (1 - w)
            fade = rng.normal(0, 3.2, N_WINDOWS)
            nf = rng.normal(0, 0.8, N_WINDOWS)
            sig_off = rng.normal(0, 0.5, n_ch)
            rssi_inst = np.empty((n_ch, N_WINDOWS))
            floor_inst = np.empty((n_ch, N_WINDOWS))
            for k, a in enumerate(ants):
                gain = a.day * w + a.night * (1 - w) + day_off[k, day_idx] + sig_off[k]
                rssi_ant = base + fade + gain + rng.normal(0, 0.65 * scale, N_WINDOWS)
                floor_ant = noise + nf + a.noise_day * w + a.noise_night * (1 - w) \
                    + rng.normal(0, 0.35, N_WINDOWS)
                losses = np.array([_loss(a, i) for i in range(N_WINDOWS)])
                rssi_inst[k] = rssi_ant - losses
                floor_inst[k] = floor_ant - losses
            for k in range(n_ch):
                for i in range(N_WINDOWS):
                    fl = float(floor_inst[k, i])
                    if calm[i]:
                        rows.append((cid, sid, k, int(ts[i]), WINDOW_MS, 0,
                                     None, None, None, None, None, None,
                                     round(fl, 2), round(fl - 0.6, 2), round(fl + 0.6, 2),
                                     None, None, None, None, None, None, None))
                        continue
                    r = float(rssi_inst[k, i])
                    sd = 2.0 * scale
                    dref = r - float(rssi_inst[0, i])
                    dsd = 0.0 if k == 0 else 0.9 * scale
                    snr = r - fl
                    rows.append((
                        cid, sid, k, int(ts[i]), WINDOW_MS, 120,
                        round(r, 2), round(r - 1.28 * sd, 2), round(r + 1.28 * sd, 2),
                        round(r - 2.5 * sd, 2), round(r + 2.5 * sd, 2), round(sd, 2),
                        round(fl, 2), round(fl - 0.6, 2), round(fl + 0.6, 2),
                        round(snr, 2), round(snr - 1.28 * sd, 2), round(snr + 1.28 * sd, 2),
                        round(dref, 2), round(dref - 1.28 * dsd, 2), round(dref + 1.28 * dsd, 2),
                        round(dsd, 2),
                    ))
        conn.executemany(
            "INSERT INTO aggregates (campaign_id, signal_id, channel_idx, window_start_ms,"
            " window_ms,"
            " n, rssi_med, rssi_p10, rssi_p90, rssi_min, rssi_max, rssi_sd, floor_med, floor_p10,"
            " floor_p90, snr_med, snr_p10, snr_p90, dref_med, dref_p10, dref_p90, dref_sd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
