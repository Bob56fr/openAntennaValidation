"""Hardware identity tracking: a new identity opens a new measurement series."""

from __future__ import annotations

from dataclasses import dataclass

from open_antenna_validation.hardware.base import DeviceInfo, hardware_identity
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms


@dataclass(frozen=True)
class IdentityCheck:
    hardware_id: int
    identity: str
    previous: str | None

    @property
    def changed(self) -> bool:
        """True when a device was used before and this one differs (not on first start)."""
        return self.previous is not None and self.previous != self.identity


def record_hardware(
    db: Database, device: DeviceInfo, antenna: str, gain_mode: str
) -> IdentityCheck:
    identity = hardware_identity(device, antenna, gain_mode)
    ts = now_ms()
    with db.transaction() as conn:
        last = conn.execute(
            "SELECT identity FROM hardware ORDER BY last_seen_ms DESC, id DESC LIMIT 1"
        ).fetchone()
        previous = str(last["identity"]) if last else None
        row = conn.execute("SELECT id FROM hardware WHERE identity = ?", (identity,)).fetchone()
        if row:
            hardware_id = int(row["id"])
            conn.execute("UPDATE hardware SET last_seen_ms = ? WHERE id = ?", (ts, hardware_id))
        else:
            cur = conn.execute(
                "INSERT INTO hardware (identity, driver, model, serial, antenna, gain_mode,"
                " first_seen_ms, last_seen_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (identity, device.driver, device.model, device.serial, antenna, gain_mode, ts, ts),
            )
            hardware_id = int(cur.lastrowid or 0)
    check = IdentityCheck(hardware_id=hardware_id, identity=identity, previous=previous)
    if check.changed:
        db.log_event("hardware_change", {"previous": previous, "current": identity})
    return check
