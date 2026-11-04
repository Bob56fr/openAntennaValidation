"""Timestamps: integer milliseconds since the Unix epoch, UTC, everywhere."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="milliseconds")
