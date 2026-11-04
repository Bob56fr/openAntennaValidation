"""Qualification runs (cahier des charges, section 6): guided bench steps, hardware profile,
verdict per signal class, acceptance.

The bench measures what the switch costs (latency, glitch, settling, hence the blanked time),
the imbalance of the inputs versus frequency, the isolation and the receiver's own noise
floor. The margins per signal class come from the A/A campaign, delivered with the campaign
engine (P3, decision D12): until then the verdict per class rests on the useful time only.
"""

from __future__ import annotations

import json
import uuid as uuidlib
from dataclasses import dataclass
from typing import Any

from open_antenna_validation.bench import levels_spread_db
from open_antenna_validation.config import QualificationConfig
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

DAY_MS = 86_400_000


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    setup: str
    measures: tuple[str, ...]  # "levels", "switching"
    optional: bool = False


STEPS = (
    Step("floor", "Plancher propre du SDR",
         "Branchez une charge 50 Ω sur chacune des entrées A et B (à défaut, laissez-les "
         "libres). Gardez le gain prévu pour la campagne.", ("levels",)),
    Step("symmetric", "Splitter symétrique",
         "Branchez une seule source sur un splitter et ses deux sorties sur A et B, avec deux "
         "câbles identiques. Un générateur de bruit large bande est conseillé ; à défaut, une "
         "antenne.", ("levels", "switching")),
    Step("swapped", "Câbles inversés",
         "Sans rien changer d'autre, inversez les deux câbles entre le splitter et les entrées "
         "A et B.", ("levels",)),
    Step("attenuator", "Atténuateur sur B",
         "Remettez les câbles dans le premier sens et intercalez l'atténuateur entre le "
         "splitter et l'entrée B. La bascule devient une marche visible dans le flux.",
         ("levels", "switching")),
    Step("isolation", "Isolation (option)",
         "Source directement sur l'entrée A, charge 50 Ω sur l'entrée B.", ("levels",),
         optional=True),
)
STEP_KEYS = {s.key: s for s in STEPS}
REQUIRED = tuple(s.key for s in STEPS if not s.optional)

CLASS_LABELS = {"carrier": "Porteuses et CW", "digital": "Signaux numériques", "ssb": "SSB"}


class QualificationError(Exception):
    """Invalid request on a qualification run (message in French)."""


def steps_payload() -> list[dict[str, Any]]:
    return [{"key": s.key, "title": s.title, "setup": s.setup, "measures": list(s.measures),
             "optional": s.optional} for s in STEPS]


def create_run(db: Database, *, receiver: dict[str, Any], band: str, attenuator_db: float,
               switch_rate_hz: float) -> str:
    if receiver.get("state") != "running":
        raise QualificationError("Le récepteur doit être en marche pour qualifier le SDR.")
    if len(receiver.get("antennas") or []) < 2:
        raise QualificationError(
            "Ce SDR n'a qu'une entrée : le banc de commutation est sans objet.")
    run = str(uuidlib.uuid4())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO qualification_runs (uuid, kind, reception_mode, band, center_hz,"
            " sample_rate, gain_json, switch_rate_hz, hardware_json, started_ms, status,"
            " results_json) VALUES (?, 'bench', 'switched', ?, ?, ?, ?, ?, ?, ?, 'running', ?)",
            (run, band, receiver.get("center_hz") or 0.0, receiver.get("sample_rate") or 0,
             json.dumps({"index": receiver.get("gain_index"),
                         "label": _gain_label(receiver)}, ensure_ascii=False),
             switch_rate_hz, json.dumps([receiver.get("identity")]), now_ms(),
             json.dumps({"attenuator_db": attenuator_db, "steps": {}})),
        )
    return run


def _gain_label(receiver: dict[str, Any]) -> str | None:
    steps = receiver.get("gain_steps") or []
    index = receiver.get("gain_index")
    return steps[index] if isinstance(index, int) and 0 <= index < len(steps) else None


def _row(db: Database, run: str) -> Any:
    rows = db.query("SELECT * FROM qualification_runs WHERE uuid = ?", (run,))
    if not rows:
        raise QualificationError("Qualification introuvable.")
    return rows[0]


def store_step(db: Database, run: str, step: str, result: dict[str, Any]) -> None:
    row = _row(db, run)
    results = json.loads(row["results_json"])
    results["steps"][step] = {**result, "ts_ms": now_ms()}
    with db.transaction() as conn:
        conn.execute("UPDATE qualification_runs SET results_json = ? WHERE uuid = ?",
                     (json.dumps(results), run))


def check_step(db: Database, run: str, step: str) -> Step:
    row = _row(db, run)
    if row["status"] != "running":
        raise QualificationError("Cette qualification est terminée : commencez-en une nouvelle.")
    if step not in STEP_KEYS:
        raise QualificationError(f"Étape inconnue : {step}.")
    return STEP_KEYS[step]


# --- profile ---------------------------------------------------------------------


def useful_fraction(blanking_ms: float, rate_hz: float) -> float:
    """Share of time left after blanking, switching at ``rate_hz`` (two inputs)."""
    dwell_ms = 1000.0 / (2.0 * rate_hz)
    return max(0.0, 1.0 - blanking_ms / dwell_ms)


def compute_profile(results: dict[str, Any], cfg: QualificationConfig) -> dict[str, Any]:
    steps = results.get("steps", {})
    missing = [STEP_KEYS[k].title for k in REQUIRED if k not in steps]
    profile: dict[str, Any] = {"missing": missing, "complete": not missing, "warnings": []}
    attenuator = float(results.get("attenuator_db", cfg.attenuator_db))

    floor = steps.get("floor", {}).get("levels")
    if floor:
        profile["own_floor_dbfs"] = floor["floor_dbfs"]

    sym = steps.get("symmetric", {}).get("levels")
    swp = steps.get("swapped", {}).get("levels")
    if sym and swp:
        m1, m2 = sym["difference_db"], swp["difference_db"]
        port = [round((a + b) / 2, 3) for a, b in zip(m1, m2, strict=True)]
        split = [(a - b) / 2 for a, b in zip(m1, m2, strict=True)]
        profile["imbalance"] = {
            "freqs_hz": sym["freqs_hz"], "normal_db": m1, "swapped_db": m2, "port_db": port,
            "port_min_db": min(port), "port_max_db": max(port),
            "splitter_db": round(sum(split) / len(split), 3),
        }

    switching = steps.get("attenuator", {}).get("switching")
    fallback = steps.get("symmetric", {}).get("switching")
    if switching and switching.get("detected"):
        lat = switching["latency_ms"]
        blanking = lat["p99"] + switching["settle_ms"] + cfg.blanking_margin_ms
        profile["switching"] = {**switching, "source": "attenuator"}
        att_levels = steps["attenuator"].get("levels")
        if att_levels and sym:
            measured = sum(att_levels["difference_db"]) / len(att_levels["difference_db"]) \
                - sum(sym["difference_db"]) / len(sym["difference_db"])
            profile["attenuator_check_db"] = round(measured - attenuator, 2)
    elif fallback:
        # No visible step: the settling time measured from the command already includes
        # the latency, and is an upper bound.
        blanking = fallback["settle_ms"] + cfg.blanking_margin_ms
        profile["switching"] = {**fallback, "source": "symmetric"}
    else:
        blanking = None
    if profile.get("switching", {}).get("noisy"):
        profile["warnings"].append(
            "Mesure de la bascule bruitée : la source varie trop (voix, évanouissement) et le "
            "parasite peut être masqué. Refaites les étapes avec un générateur de bruit.")
    if sym and swp and max(levels_spread_db(sym), levels_spread_db(swp)) > cfg.tolerance_db:
        profile["warnings"].append(
            "Déséquilibre irrégulier selon la fréquence : la source n'est pas assez stable. "
            "Un générateur de bruit large bande donne une correction plus fiable.")
    if blanking is not None:
        profile["blanking_ms"] = round(blanking, 3)
        profile["useful_at_rate"] = round(useful_fraction(blanking, cfg.switch_rate_hz), 3)

    iso = steps.get("isolation", {}).get("levels")
    if iso:
        a, b = iso["inputs"]
        isolation = iso["total_db"][a] - iso["total_db"][b]
        own_b = (floor or {}).get("floor_dbfs", {}).get(b)
        limited = own_b is not None and iso["floor_dbfs"][b] <= own_b + 1.0
        profile["isolation"] = {"db": round(isolation, 1), "floor_limited": limited}

    profile["verdicts"] = verdicts(profile.get("blanking_ms"), cfg)
    return profile


def verdicts(blanking_ms: float | None, cfg: QualificationConfig) -> list[dict[str, Any]]:
    out = []
    for cls, rate in cfg.class_rate_hz.items():
        if blanking_ms is None:
            out.append({"class": cls, "label": CLASS_LABELS.get(cls, cls), "state": "unknown",
                        "rate_hz": rate, "useful": None, "margin_db": None})
            continue
        useful = useful_fraction(blanking_ms, rate)
        state = "ok" if useful >= cfg.useful_ok else ("warn" if useful >= cfg.useful_min else "bad")
        out.append({"class": cls, "label": CLASS_LABELS.get(cls, cls), "state": state,
                    "rate_hz": rate, "useful": round(useful, 3),
                    "margin_db": None})  # margin: A/A campaign (P3)
    return out


def run_payload(db: Database, run: str, cfg: QualificationConfig) -> dict[str, Any]:
    row = _row(db, run)
    results = json.loads(row["results_json"])
    return {
        "uuid": row["uuid"], "status": row["status"], "band": row["band"],
        "center_hz": row["center_hz"], "sample_rate": row["sample_rate"],
        "gain": json.loads(row["gain_json"]), "identities": json.loads(row["hardware_json"]),
        "switch_rate_hz": row["switch_rate_hz"], "started_ms": row["started_ms"],
        "ended_ms": row["ended_ms"], "accepted_ms": row["accepted_ms"],
        "attenuator_db": results.get("attenuator_db"),
        "steps": results.get("steps", {}),
        "profile": compute_profile(results, cfg),
    }


def latest_run(db: Database) -> str | None:
    rows = db.query(
        "SELECT uuid FROM qualification_runs ORDER BY started_ms DESC, id DESC LIMIT 1")
    return str(rows[0]["uuid"]) if rows else None


def finish(db: Database, run: str, cfg: QualificationConfig) -> dict[str, Any]:
    row = _row(db, run)
    if row["status"] != "running":
        raise QualificationError("Cette qualification est déjà terminée.")
    results = json.loads(row["results_json"])
    profile = compute_profile(results, cfg)
    if not profile["complete"]:
        raise QualificationError("Étapes manquantes : " + ", ".join(profile["missing"]) + ".")
    ts = now_ms()
    identities = row["hardware_json"]
    with db.transaction() as conn:
        conn.execute(
            "UPDATE hardware_profiles SET active = 0 WHERE identities_json = ? AND band = ?",
            (identities, row["band"]))
        conn.execute(
            "INSERT INTO hardware_profiles (qualification_id, identities_json, band, gain_json,"
            " switch_rate_hz, blanking_ms, useful_fraction, isolation_db, own_floor_dbfs,"
            " correction_json, margins_json, created_ms, valid_until_ms, active)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (row["id"], identities, row["band"], row["gain_json"], row["switch_rate_hz"],
             profile.get("blanking_ms"), profile.get("useful_at_rate"),
             (profile.get("isolation") or {}).get("db"),
             _mean((profile.get("own_floor_dbfs") or {}).values()),
             json.dumps(profile.get("imbalance") or {}),
             json.dumps({"verdicts": profile["verdicts"]}, ensure_ascii=False),
             ts, ts + cfg.validity_days * DAY_MS),
        )
        conn.execute(
            "UPDATE qualification_runs SET status = 'done', ended_ms = ?, verdicts_json = ?"
            " WHERE uuid = ?", (ts, json.dumps(profile["verdicts"], ensure_ascii=False), run))
    db.log_event("qualification_done", {"run": run, "blanking_ms": profile.get("blanking_ms")})
    return run_payload(db, run, cfg)


def accept(db: Database, run: str, cfg: QualificationConfig) -> dict[str, Any]:
    row = _row(db, run)
    if row["status"] != "done":
        raise QualificationError("Terminez la qualification avant d'en accepter les limites.")
    with db.transaction() as conn:
        conn.execute("UPDATE qualification_runs SET accepted_ms = ? WHERE uuid = ?",
                     (now_ms(), run))
    db.log_event("qualification_accepted", {"run": run})
    return run_payload(db, run, cfg)


def _mean(values: Any) -> float | None:
    items = [float(v) for v in values]
    return round(sum(items) / len(items), 2) if items else None
