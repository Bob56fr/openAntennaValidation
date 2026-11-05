"""Tracks: one per station, followed through its decodes (cahier des charges, section 10).

States (stored in English, shown in French):

- ``candidate``: first decode;
- ``tracking``: consistent decodes (same callsign, same slot parity, audio frequency within
  the tolerance);
- ``validated``: end of QSO (RR73, 73) or silence of N slots, with at least K measured
  slots; a validated track goes on while the station keeps the same frequency and parity
  (several QSOs in a row);
- ``rejected``: an anomaly, the reason is kept and the measurements of the track are left
  out of the results (the raw decodes stay in the database).

Reasons: ``collision`` (another station on the same frequency and parity), ``overlap``
(another signal closer than ``overlap_hz`` in the same slot), ``frequency_jump``,
``parity_change``, ``unresolved_hash``, ``too_few`` (silence before K measured slots).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from open_antenna_validation.config import DigitalConfig
from open_antenna_validation.digital import messages
from open_antenna_validation.digital.messages import CountryFile, Sender
from open_antenna_validation.digital.modes import Mode, slot_parity
from open_antenna_validation.storage import Database

STATE_LABELS = {"candidate": "CANDIDATE", "tracking": "SUIVIE", "validated": "VALIDÉE",
                "rejected": "REJETÉE"}
REASON_LABELS = {
    "collision": "Collision (même fréquence, même parité)",
    "overlap": "Signal à moins de 50 Hz",
    "frequency_jump": "Saut de fréquence",
    "parity_change": "Changement de parité",
    "unresolved_hash": "Indicatif haché non résolu",
    "too_few": "Trop peu de mesures",
}


@dataclass
class SlotMessage:
    """One message of a slot, after decoding on every channel and measuring."""

    message: str
    sender: Sender
    audio_hz: float
    measured: bool          # level measured on at least one channel
    overlap: bool = False
    doubtful: bool = False  # "?" from jt9: never starts or feeds a track
    decode_ids: list[int] = field(default_factory=list)


@dataclass
class _Open:
    id: int
    call: str
    parity: int
    audio_hz: float
    grid: str
    last_slot: int
    measured: int
    state: str
    qso_end: bool


class TrackBook:
    def __init__(self, db: Database, campaign_id: int, mode: Mode, cfg: DigitalConfig,
                 station_grid: str, band: str, countries: CountryFile | None) -> None:
        self.db = db
        self.campaign_id = campaign_id
        self.mode = mode
        self.cfg = cfg
        self.station = messages.grid_to_latlon(station_grid) if station_grid else None
        self.ground_km = cfg.ground_wave_km.get(band, cfg.ground_wave_default_km)
        self.countries = countries
        self.tolerance = cfg.freq_tolerance_hz[mode.name]
        self.silence_ms = int(cfg.silence_slots[mode.name]) * mode.slot_ms
        self.min_measures = int(cfg.min_measures[mode.name])
        self.open: dict[str, _Open] = {}
        # Locator of each station, kept for the campaign (applied to its later messages).
        self.grids: dict[str, str] = {}
        self._reload()

    def _reload(self) -> None:
        """Open tracks after a restart of the service."""
        for r in self.db.query(
                "SELECT * FROM tracks WHERE campaign_id = ? AND closed = 0", (self.campaign_id,)):
            self.open[r["callsign"]] = _Open(
                r["id"], r["callsign"], int(r["parity"] or 0), float(r["audio_hz"] or 0),
                r["locator"], int(r["last_ms"]), int(r["n_measures"]), r["state"],
                bool(r["qso_end"]))
        for r in self.db.query("SELECT callsign, locator FROM tracks WHERE campaign_id = ? "
                               "AND locator != ''", (self.campaign_id,)):
            self.grids.setdefault(r["callsign"], r["locator"])

    # --- position -------------------------------------------------------------------

    def _position(self, call: str, grid: str) -> dict[str, Any]:
        out: dict[str, Any] = {"distance_km": None, "azimuth_deg": None,
                               "propagation_class": "", "takeoff_deg": None,
                               "approximate": 0, "country": ""}
        country = self.countries.lookup(call) if self.countries else None
        if country:
            out["country"] = country.name
        if self.station is None:
            return out
        if grid:
            lat, lon = messages.grid_to_latlon(grid)
        elif country:
            lat, lon = country.lat, country.lon
            out["approximate"] = 1
        else:
            return out
        d, az = messages.distance_azimuth(*self.station, lat, lon)
        c = self.cfg
        out.update(distance_km=round(d, 1), azimuth_deg=round(az, 1),
                   propagation_class=messages.propagation_class(
                       d, self.ground_km, c.nvis_km, c.skip_km, c.one_hop_km))
        if d <= c.one_hop_km:
            angle = messages.takeoff_angle(d, c.f2_height_km)
            out["takeoff_deg"] = None if angle is None else round(angle, 1)
        return out

    # --- state machine --------------------------------------------------------------

    def _new(self, call: str, msg: SlotMessage, slot_ms: int, parity: int,
             state: str = "candidate", reason: str = "") -> _Open:
        grid = self.grids.get(call, "")
        pos = self._position(call, grid)
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO tracks (campaign_id, callsign, locator, distance_km, azimuth_deg,"
                " propagation_class, state, reject_reason, audio_hz, parity, first_ms, last_ms,"
                " digital_mode, country, approximate, takeoff_deg, closed)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.campaign_id, call, grid, pos["distance_km"], pos["azimuth_deg"],
                 pos["propagation_class"], state, reason, msg.audio_hz, parity, slot_ms,
                 slot_ms, self.mode.label, pos["country"], pos["approximate"],
                 pos["takeoff_deg"], 1 if state == "rejected" else 0))
            track_id = int(cur.lastrowid or 0)
        return _Open(track_id, call, parity, msg.audio_hz, grid, slot_ms, 0, state, False)

    def _close(self, t: _Open, state: str, reason: str = "") -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE tracks SET state = ?, reject_reason = ?, closed = 1 "
                         "WHERE id = ?", (state, reason, t.id))
        self.open.pop(t.call, None)

    def _attach(self, t: _Open, msg: SlotMessage, slot_ms: int) -> None:
        if msg.measured:
            t.measured += 1
        t.last_slot = slot_ms
        if t.state == "candidate":
            t.state = "tracking"
        if msg.sender.end_of_qso:
            t.qso_end = True
        if t.qso_end and t.measured >= self.min_measures:
            t.state = "validated"
        grid = msg.sender.grid
        changed = False
        if grid:
            self.grids.setdefault(t.call, grid)
            if not t.grid:
                t.grid = grid
            elif grid[:4] != t.grid[:4]:
                changed = True  # portable station: signalled, not rejected
        pos = self._position(t.call, t.grid)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE tracks SET state = ?, last_ms = ?, n_measures = ?, qso_end = ?,"
                " locator = ?, distance_km = ?, azimuth_deg = ?, propagation_class = ?,"
                " takeoff_deg = ?, approximate = ?, country = ?,"
                " locator_changed = MAX(locator_changed, ?) WHERE id = ?",
                (t.state, slot_ms, t.measured, int(t.qso_end), t.grid, pos["distance_km"],
                 pos["azimuth_deg"], pos["propagation_class"], pos["takeoff_deg"],
                 pos["approximate"], pos["country"], int(changed), t.id))
            conn.executemany("UPDATE decodes SET track_id = ? WHERE id = ?",
                             [(t.id, d) for d in msg.decode_ids])

    def update(self, slot_ms: int, slot: list[SlotMessage]) -> None:
        parity = slot_parity(slot_ms, self.mode)
        uses_parity = self.mode.name != "wspr"
        for msg in slot:
            s = msg.sender
            if msg.doubtful or not (s.call or s.unresolved):
                continue
            if s.unresolved:
                hashed = self._new(messages.UNRESOLVED_HASH, msg, slot_ms, parity, "rejected",
                                   "unresolved_hash")
                with self.db.transaction() as conn:
                    conn.executemany("UPDATE decodes SET track_id = ? WHERE id = ?",
                                     [(hashed.id, d) for d in msg.decode_ids])
                continue
            current = self.open.get(s.call)
            if current is not None:
                reason = ""
                if uses_parity and parity != current.parity:
                    reason = "parity_change"
                elif abs(msg.audio_hz - current.audio_hz) > self.tolerance:
                    reason = "frequency_jump"
                if reason:
                    # A validated track ends there; any other is rejected.
                    if current.state == "validated":
                        self._close(current, "validated")
                    else:
                        self._close(current, "rejected", reason)
                    current = None
            if current is None:
                current = self._new(s.call, msg, slot_ms, parity)
                self.open[s.call] = current
            t = current
            # Another station on the same frequency and parity.
            for other in list(self.open.values()):
                if other.call != t.call and (not uses_parity or other.parity == parity) \
                        and abs(other.audio_hz - msg.audio_hz) < self.tolerance:
                    for victim in (other, t):
                        if victim.call in self.open:
                            self._close(victim, "rejected", "collision")
            if msg.overlap:
                self._attach(t, msg, slot_ms)
                if t.call in self.open:
                    self._close(t, "rejected", "overlap")
                continue
            if t.call in self.open:
                self._attach(t, msg, slot_ms)
            else:
                # Rejected by the collision: its decodes still point at it.
                with self.db.transaction() as conn:
                    conn.executemany("UPDATE decodes SET track_id = ? WHERE id = ?",
                                     [(t.id, d) for d in msg.decode_ids])
        self.close_silent(slot_ms)

    def close_silent(self, now_slot_ms: int) -> None:
        for t in list(self.open.values()):
            if now_slot_ms - t.last_slot >= self.silence_ms:
                self._finish(t)

    def _finish(self, t: _Open) -> None:
        if t.state == "validated" or t.measured >= self.min_measures:
            self._close(t, "validated")
        else:
            self._close(t, "rejected", "too_few")

    def close_all(self) -> None:
        for t in list(self.open.values()):
            self._finish(t)
