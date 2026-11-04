"""Operator installation (cahier des charges, section 7): antenna catalogue, receive chains,
cable catalogue and chain losses versus frequency.

A chain is never edited in place: saving a chain creates a new version, so that campaigns
keep pointing at the chain they were measured with. Losses are positive dB, gains negative.
"""

from __future__ import annotations

import json
import math
import uuid as uuidlib
from typing import Any

from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

ANTENNA_TYPES = {
    "dipole": "Dipôle", "inverted_v": "V inversé", "vertical_quarter": "Verticale ¼ λ",
    "vertical_radials": "Verticale avec radians", "efhw": "EFHW", "long_wire": "Long fil",
    "delta_loop": "Delta loop", "quad": "Quad", "magnetic_loop": "Boucle magnétique",
    "yagi": "Yagi", "moxon": "Moxon", "active_loop": "Boucle active", "mini_whip": "Mini-whip",
    "beverage": "Beverage", "other": "Autre",
}
POLARIZATIONS = {"horizontal": "Horizontale", "vertical": "Verticale", "mixed": "Mixte"}

# Attenuation in dB per 100 m at 10 and 30 MHz, read from manufacturers' datasheets.
# TO BE VERIFIED against the datasheet of the cable actually used (cahier des charges,
# section 17): the operator may always enter a measured loss instead.
CABLES: dict[str, dict[str, Any]] = {
    "RG58": {"label": "RG58 C/U", "db_100m": {10e6: 4.9, 30e6: 8.6}},
    "RG8X": {"label": "RG8X (Mini-8)", "db_100m": {10e6: 3.5, 30e6: 6.0}},
    "H155": {"label": "H155", "db_100m": {10e6: 3.3, 30e6: 5.8}},
    "RG213": {"label": "RG213/U", "db_100m": {10e6: 2.2, 30e6: 3.8}},
    "AIRCELL7": {"label": "Aircell 7", "db_100m": {10e6: 2.1, 30e6: 3.7}},
    "LMR400": {"label": "LMR-400", "db_100m": {10e6: 1.3, 30e6: 2.2}},
}
CABLES_VERIFIED = False
ELEMENT_KINDS = {
    "coax": "Coaxial", "balun": "Balun ou unun", "choke": "Self d'arrêt", "arrester": "Parafoudre",
    "filter": "Filtre", "relay": "Relais", "preamp": "Préampli", "other": "Autre",
}


class InstallationError(Exception):
    """Invalid installation data (message in French)."""


def cable_loss_db(cable: str, length_m: float, freq_hz: float) -> float:
    """Loss of ``length_m`` of ``cable`` at ``freq_hz``: a·√f + b·f through the two points."""
    if cable not in CABLES:
        raise InstallationError(f"Câble inconnu : {cable}.")
    (f1, l1), (f2, l2) = sorted(CABLES[cable]["db_100m"].items())
    # Skin effect (√f) plus dielectric loss (f), fitted on the two datasheet points.
    det = math.sqrt(f1) * f2 - math.sqrt(f2) * f1
    a = (l1 * f2 - l2 * f1) / det
    b = (math.sqrt(f1) * l2 - math.sqrt(f2) * l1) / det
    per_100m = max(0.0, a * math.sqrt(freq_hz) + b * freq_hz)
    return float(per_100m * length_m / 100.0)


def catalogue() -> dict[str, Any]:
    return {
        "antenna_types": ANTENNA_TYPES,
        "polarizations": POLARIZATIONS,
        "element_kinds": ELEMENT_KINDS,
        "cables": {k: {"label": v["label"],
                       "db_100m": {str(int(f)): x for f, x in v["db_100m"].items()}}
                   for k, v in CABLES.items()},
        "cables_verified": CABLES_VERIFIED,
    }


# --- antennas --------------------------------------------------------------------

ANTENNA_FIELDS = ("type", "name", "model", "polarization", "height_m", "height_note",
                  "azimuth_deg", "orientation_note", "bands_note")


TEXT_FIELDS = ("model", "polarization", "height_note", "orientation_note", "bands_note")


def _check_antenna(data: dict[str, Any]) -> None:
    if not str(data.get("name", "")).strip():
        raise InstallationError("Donnez un nom à l'antenne.")
    if data.get("type") not in ANTENNA_TYPES:
        raise InstallationError("Type d'antenne inconnu.")
    if data.get("polarization") and data["polarization"] not in POLARIZATIONS:
        raise InstallationError("Polarisation inconnue.")
    az = data.get("azimuth_deg")
    if az is not None and not 0 <= float(az) < 360:
        raise InstallationError("L'azimut doit être compris entre 0 et 359°.")
    h = data.get("height_m")
    if h is not None and not 0 <= float(h) <= 300:
        raise InstallationError("Hauteur hors plage (0 à 300 m).")


def save_antenna(db: Database, data: dict[str, Any], antenna_id: int | None = None) -> int:
    _check_antenna(data)
    values: list[Any] = []
    for f in ANTENNA_FIELDS:
        v = data.get(f)
        if v is None and f in TEXT_FIELDS:
            v = ""
        values.append(v.strip() if isinstance(v, str) else v)
    with db.transaction() as conn:
        if antenna_id is None:
            cur = conn.execute(
                f"INSERT INTO antennas (uuid, {', '.join(ANTENNA_FIELDS)}, created_ms)"
                f" VALUES (?, {', '.join('?' * len(ANTENNA_FIELDS))}, ?)",
                (str(uuidlib.uuid4()), *values, now_ms()),
            )
            return int(cur.lastrowid or 0)
        sets = ", ".join(f"{f} = ?" for f in ANTENNA_FIELDS)
        if not conn.execute("SELECT 1 FROM antennas WHERE id = ?", (antenna_id,)).fetchone():
            raise InstallationError("Antenne introuvable.")
        conn.execute(f"UPDATE antennas SET {sets} WHERE id = ?", (*values, antenna_id))
        return antenna_id


def archive_antenna(db: Database, antenna_id: int) -> None:
    with db.transaction() as conn:
        conn.execute("UPDATE antennas SET archived = 1 WHERE id = ?", (antenna_id,))


def _demo_antenna_ids(db: Database) -> set[int]:
    rows = db.query(
        "SELECT ch.antenna_id FROM campaign_channels ch JOIN campaigns c ON c.id = ch.campaign_id"
        " WHERE json_extract(c.params_json, '$.demo') = 1")
    return {int(r[0]) for r in rows if r[0] is not None}


def list_antennas(db: Database) -> list[dict[str, Any]]:
    demo = _demo_antenna_ids(db)
    out = []
    for r in db.query("SELECT * FROM antennas WHERE archived = 0 ORDER BY name"):
        if r["id"] in demo:
            continue
        item = {k: r[k] for k in ("id", "uuid", *ANTENNA_FIELDS)}
        item["type_label"] = ANTENNA_TYPES.get(r["type"], r["type"])
        chain = current_chain(db, int(r["id"]))
        item["chain"] = chain
        out.append(item)
    return out


# --- chains ----------------------------------------------------------------------


def check_element(e: dict[str, Any]) -> dict[str, Any]:
    kind = e.get("kind")
    if kind not in ELEMENT_KINDS:
        raise InstallationError("Type de maillon inconnu.")
    out: dict[str, Any] = {"kind": kind, "label": str(e.get("label", "")).strip()}
    if kind == "coax" and e.get("cable_type"):
        if e["cable_type"] not in CABLES:
            raise InstallationError(f"Câble inconnu : {e['cable_type']}.")
        length = e.get("length_m")
        if length is None or not 0 < float(length) <= 1000:
            raise InstallationError("Longueur de câble hors plage (0 à 1 000 m).")
        out["cable_type"], out["length_m"] = e["cable_type"], float(length)
    # Loss (positive) or gain (negative) entered by the operator: overrides the cable model.
    if e.get("db") is not None:
        value = float(e["db"])
        if not -60 <= value <= 60:
            raise InstallationError("Perte ou gain hors plage (−60 à +60 dB).")
        out["db"] = value
    if kind == "coax" and "cable_type" not in out and "db" not in out:
        raise InstallationError(
            "Pour un coaxial, choisissez un câble et une longueur, ou saisissez sa perte.")
    if kind != "coax" and "db" not in out:
        raise InstallationError(
            f"Saisissez la perte (ou le gain) du maillon « {ELEMENT_KINDS[kind]} ».")
    if not out["label"]:
        out["label"] = (f"{CABLES[out['cable_type']]['label']}, {out['length_m']:g} m"
                        if "cable_type" in out else ELEMENT_KINDS[kind])
    return out


def save_chain(
    db: Database, antenna_id: int, elements: list[dict[str, Any]], note: str = ""
) -> int:
    """New version of the receive chain of an antenna."""
    checked = [check_element(e) for e in elements]
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM antennas WHERE id = ?", (antenna_id,)).fetchone():
            raise InstallationError("Antenne introuvable.")
        cur = conn.execute("INSERT INTO chains (antenna_id, note, created_ms) VALUES (?, ?, ?)",
                           (antenna_id, note, now_ms()))
        chain_id = int(cur.lastrowid or 0)
        for pos, e in enumerate(checked):
            conn.execute(
                "INSERT INTO chain_elements (chain_id, position, kind, label, cable_type, length_m,"
                " db_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (chain_id, pos, e["kind"], e["label"], e.get("cable_type", ""), e.get("length_m"),
                 json.dumps({"db": e["db"]} if "db" in e else {})),
            )
    return chain_id


def chain_elements(db: Database, chain_id: int) -> list[dict[str, Any]]:
    out = []
    for r in db.query(
        "SELECT * FROM chain_elements WHERE chain_id = ? ORDER BY position", (chain_id,)
    ):
        e: dict[str, Any] = {"kind": r["kind"], "label": r["label"]}
        if r["cable_type"]:
            e["cable_type"], e["length_m"] = r["cable_type"], r["length_m"]
        stored = json.loads(r["db_json"] or "{}")
        if "db" in stored:
            e["db"] = stored["db"]
        out.append(e)
    return out


def current_chain(db: Database, antenna_id: int) -> dict[str, Any] | None:
    rows = db.query("SELECT id, note, created_ms FROM chains WHERE antenna_id = ?"
                    " ORDER BY created_ms DESC, id DESC LIMIT 1", (antenna_id,))
    if not rows:
        return None
    chain_id = int(rows[0]["id"])
    return {"id": chain_id, "note": rows[0]["note"], "created_ms": rows[0]["created_ms"],
            "elements": chain_elements(db, chain_id)}


def element_loss_db(e: dict[str, Any], freq_hz: float) -> float:
    if "db" in e and e["db"] is not None:
        return float(e["db"])
    if e.get("cable_type"):
        return cable_loss_db(e["cable_type"], float(e["length_m"]), freq_hz)
    return 0.0


def chain_loss_db(db: Database, chain_id: int | None, freq_hz: float) -> float:
    if chain_id is None:
        return 0.0
    return sum(element_loss_db(e, freq_hz) for e in chain_elements(db, chain_id))
