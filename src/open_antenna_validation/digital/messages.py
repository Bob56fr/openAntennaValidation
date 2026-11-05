"""Who sent a decoded message, from where: callsign, locator, distance, propagation class.

FT8/FT4 standard messages are ``TO FROM EXTRA`` or ``CQ [MOD] FROM [GRID]`` (WSJT-X User
Guide, protocol of 77-bit messages); a hashed callsign shows as ``<CALL>`` when WSJT-X
resolved it, ``<...>`` otherwise. WSPR messages are ``CALL GRID DBM`` (type 1), ``CALL DBM``
(type 2, compound callsign) or ``<CALL> GRID6 DBM`` (type 3).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

EARTH_RADIUS_KM = 6371.0
GRID = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$")
# RR73 has the shape of a locator but is the end of a QSO in WSJT-X.
NOT_GRIDS = {"RR73"}
END_OF_QSO = {"RR73", "73", "RRR"}
_CALL = re.compile(r"^[A-Z0-9/]{3,11}$")
_CQ_MODIFIER = re.compile(r"^([A-Z]{1,4}|[0-9]{3})$")
UNRESOLVED_HASH = "<...>"


@dataclass(frozen=True)
class Sender:
    call: str               # "" when the message names no sender
    grid: str = ""          # 4 or 6 characters, upper case
    hashed: bool = False    # the sender was sent as a hash (shown <CALL>)
    unresolved: bool = False  # hash not resolved: <...>
    end_of_qso: bool = False
    power_dbm: int | None = None  # WSPR


def _is_call(token: str) -> bool:
    return bool(_CALL.match(token)) and any(c.isdigit() for c in token) \
        and any(c.isalpha() for c in token)


def _strip_hash(token: str) -> tuple[str, bool, bool]:
    if token.startswith("<") and token.endswith(">"):
        inner = token[1:-1]
        return ("" if inner == "..." else inner), True, inner == "..."
    return token, False, False


def _grid(token: str) -> str:
    t = token.upper()
    return t if GRID.match(t) and t not in NOT_GRIDS else ""


def parse_ft(message: str) -> Sender:
    """Sender of an FT8 or FT4 message."""
    tokens = message.upper().split()
    if not tokens:
        return Sender("")
    if tokens[0] == "CQ" or tokens[0].startswith("CQ_") or tokens[0] in ("QRZ", "DE"):
        rest = tokens[1:]
        if len(rest) >= 2 and _CQ_MODIFIER.match(rest[0]) and not _is_call(rest[0]):
            rest = rest[1:]  # CQ DX, CQ NA, CQ 145…
        if not rest:
            return Sender("")
        call, hashed, unresolved = _strip_hash(rest[0])
        if not (unresolved or _is_call(call)):
            return Sender("")
        return Sender(call, _grid(rest[1]) if len(rest) > 1 else "", hashed, unresolved)
    if len(tokens) >= 2:
        call, hashed, unresolved = _strip_hash(tokens[1])
        if unresolved or _is_call(call):
            extra = tokens[2] if len(tokens) > 2 else ""
            if extra == "R" and len(tokens) > 3:  # "TO FROM R GRID"
                extra = tokens[3]
            return Sender(call, _grid(extra), hashed, unresolved,
                          end_of_qso=extra in END_OF_QSO)
    return Sender("")


def parse_wspr(message: str) -> Sender:
    tokens = message.upper().split()
    if not tokens:
        return Sender("")
    call, hashed, unresolved = _strip_hash(tokens[0])
    power = None
    if tokens[-1].lstrip("-").isdigit():
        power = int(tokens[-1])
    grid = _grid(tokens[1]) if len(tokens) == 3 else ""
    if not (unresolved or _is_call(call)):
        return Sender("")
    return Sender(call, grid, hashed, unresolved, power_dbm=power)


def parse(mode_name: str, message: str) -> Sender:
    return parse_wspr(message) if mode_name == "wspr" else parse_ft(message)


# --- geography -------------------------------------------------------------------------


def grid_to_latlon(grid: str) -> tuple[float, float]:
    """Centre of a 4- or 6-character Maidenhead square, in degrees (lat, lon)."""
    g = grid.upper()
    lon = float((ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2)
    lat = float((ord(g[1]) - 65) * 10 - 90 + int(g[3]))
    if len(g) >= 6:
        lon += (ord(g[4]) - 65) * 5 / 60 + 2.5 / 60
        lat += (ord(g[5]) - 65) * 2.5 / 60 + 1.25 / 60
    else:
        lon += 1.0
        lat += 0.5
    return lat, lon


def distance_azimuth(lat1: float, lon1: float, lat2: float, lon2: float
                     ) -> tuple[float, float]:
    """Great-circle distance (km) and initial bearing (degrees from north) from 1 to 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, (math.degrees(math.atan2(y, x)) + 360) % 360


def takeoff_angle(distance_km: float, layer_km: float) -> float | None:
    """Elevation of a single hop off a layer at ``layer_km`` (cahier des charges, section
    10): θ = d / 2R, elevation = atan[(cos θ − R/(R+h)) / sin θ]; None beyond one hop."""
    r = EARTH_RADIUS_KM
    theta = distance_km / (2 * r)
    if theta <= 0:
        return 90.0
    elevation = math.degrees(math.atan((math.cos(theta) - r / (r + layer_km)) / math.sin(theta)))
    return elevation if elevation >= 0 else None


def propagation_class(distance_km: float, ground_km: float, nvis_km: float, skip_km: float,
                      hop_km: float) -> str:
    if distance_km < ground_km:
        return "ground"  # ground wave, ambiguous with NVIS
    if distance_km < nvis_km:
        return "nvis"
    if distance_km < skip_km:
        return "skip"
    if distance_km < hop_km:
        return "dx1"
    return "dxn"


CLASS_LABELS = {"ground": "Onde de sol", "nvis": "NVIS", "skip": "Zone de silence",
                "dx1": "DX en un bond", "dxn": "DX en plusieurs bonds"}


# --- countries (cty.dat, by AD1C, shipped with WSJT-X) ----------------------------------


@dataclass(frozen=True)
class Country:
    name: str
    lat: float
    lon: float  # east positive (cty.dat stores west positive)


class CountryFile:
    """Prefix → country, from cty.dat: ``Name: CQ: ITU: Cont: Lat: Lon(W+): TZ: Prefix:``
    then aliases up to ``;``; ``=CALL`` is an exact callsign, (..) [..] <..> {..} ~..~
    are per-alias overrides, ignored here."""

    def __init__(self, text: str) -> None:
        self.prefixes: dict[str, Country] = {}
        self.exact: dict[str, Country] = {}
        country: Country | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if not raw.startswith((" ", "\t")) and line.count(":") >= 8:
                f = [x.strip() for x in line.split(":")]
                country = Country(f[0], float(f[4]), -float(f[5]))
                self.prefixes.setdefault(f[7].lstrip("*"), country)
                continue
            if country is None:
                continue
            for alias in line.rstrip(";").split(","):
                alias = re.sub(r"[\(\[<{~].*$", "", alias.strip())
                if not alias:
                    continue
                if alias.startswith("="):
                    self.exact[alias[1:]] = country
                else:
                    self.prefixes.setdefault(alias, country)

    @classmethod
    def load(cls, path: str) -> CountryFile | None:
        try:
            return cls(Path(path).read_text(encoding="latin-1"))
        except OSError:
            return None

    def lookup(self, call: str) -> Country | None:
        call = call.upper()
        if call in self.exact:
            return self.exact[call]
        base = call
        if "/" in call:
            parts = [p for p in call.split("/") if p]
            # "F/DL5SE": the shorter part that is not a suffix (P, M, QRP, a digit) gives
            # the country.
            suffixes = {"P", "M", "MM", "AM", "QRP", "A", "B", "LH", "R"}
            heads = [p for p in parts if p not in suffixes and not p.isdigit()]
            base = min(heads, key=len) if heads else parts[0]
        for n in range(len(base), 0, -1):
            hit = self.prefixes.get(base[:n])
            if hit is not None:
                return hit
        return None
