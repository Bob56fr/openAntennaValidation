"""Decoding side of a digital campaign, in the service: gathers the slot audio of every
channel, then decodes, measures and tracks in a thread of its own (a WSPR slot takes a few
seconds of processing, the acquisition must never wait for it).
"""

from __future__ import annotations

import logging
import queue
import statistics
import threading
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from open_antenna_validation.config import Config
from open_antenna_validation.digital import messages, wsjtx
from open_antenna_validation.digital.modes import MODES, Mode
from open_antenna_validation.digital.pipeline import SlotProcessor
from open_antenna_validation.digital.tracks import TrackBook
from open_antenna_validation.storage import Database

log = logging.getLogger(__name__)
LIVE_MESSAGES = 60
DT_SLOTS = 20


class DigitalSession:
    def __init__(self, db: Database, cfg: Config, campaign_id: int, uuid: str,
                 params: dict[str, Any], n_channels: int, simultaneous: bool, band: str,
                 workdir: Path, tools: wsjtx.Tools | None = None) -> None:
        digital = params["digital"]
        self.mode: Mode = MODES[digital["mode"]]
        self.uuid = uuid
        self.n_channels = n_channels
        self.simultaneous = simultaneous
        self.tools = tools or wsjtx.find_tools(cfg.digital.wsjtx_path)
        countries = messages.CountryFile.load(self.tools.cty_path) if self.tools.cty_path \
            else None
        # Shared connection (serialised by Database): reports from the web threads.
        self._db = db
        self.book = TrackBook(db, campaign_id, self.mode, cfg.digital, cfg.station.locator,
                              band, countries)
        self.processor = SlotProcessor(db, self.tools, self.mode, campaign_id,
                                       float(digital["dial_hz"]), workdir, self.book,
                                       cfg.digital.decode_depth, cfg.digital.overlap_hz)
        self._pending: dict[int, dict[int, np.ndarray]] = {}
        self._queue: queue.Queue[tuple[int, dict[int, np.ndarray]] | None] = queue.Queue()
        self._lock = threading.Lock()
        self.live: deque[dict[str, Any]] = deque(maxlen=LIVE_MESSAGES)
        self.dts: deque[float] = deque(maxlen=DT_SLOTS)
        self.slots_done = 0
        self.last_slot_ms: int | None = None
        self.errors = 0
        self._thread = threading.Thread(target=self._run, name="digital", daemon=True)
        self._thread.start()

    # --- input (acquisition event threads) ------------------------------------------

    def add(self, event: dict[str, Any]) -> None:
        if event.get("campaign") != self.uuid:
            return
        slot = int(event["slot_ms"])
        audio = np.frombuffer(event["audio"], dtype=np.float32)
        ready: list[tuple[int, dict[int, np.ndarray]]] = []
        with self._lock:
            self._pending.setdefault(slot, {})[int(event["channel"])] = audio
            expected = self.n_channels if self.simultaneous else 1
            for s in sorted(self._pending):
                got = self._pending[s]
                # Complete, or given up: a later slot already arrived from every channel.
                if len(got) >= expected or s < slot - self.mode.slot_ms:
                    ready.append((s, self._pending.pop(s)))
        for item in ready:
            self._queue.put(item)

    def flush(self) -> None:
        with self._lock:
            ready = sorted(self._pending.items())
            self._pending.clear()
        for item in ready:
            self._queue.put(item)

    # --- processing thread ----------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            slot, audio = item
            try:
                result = self.processor.process(slot, audio)
            except Exception:
                self.errors += 1
                log.exception("Décodage du créneau %s", slot)
                continue
            self.slots_done += 1
            self.last_slot_ms = slot
            if result.dt_median_s is not None:
                self.dts.append(result.dt_median_s)
            for m in result.messages:
                self.live.appendleft({"slot_ms": slot, **m})

    def close(self, final: bool) -> None:
        """Stop after the slots already received; ``final``: the campaign is over, every
        open track is closed."""
        self.flush()
        self._queue.put(None)
        self._thread.join(timeout=120)
        if final:
            self.book.close_all()
        self.processor.close()

    # --- report ---------------------------------------------------------------------

    def clock_offset_s(self) -> float | None:
        """Median DT of the recent decodes: how late the station clock is, seen by the
        decoders (DT of the transmissions of well-synchronised stations is about zero)."""
        return round(statistics.median(self.dts), 2) if len(self.dts) >= 3 else None

    def report(self) -> dict[str, Any]:
        rows = self._db.query(
            "SELECT state, reject_reason, COUNT(*) AS n FROM tracks WHERE campaign_id = "
            "(SELECT id FROM campaigns WHERE uuid = ?) GROUP BY state, reject_reason",
            (self.uuid,))
        return {
            "mode": self.mode.label, "slots": self.slots_done, "last_slot_ms": self.last_slot_ms,
            "decoder": self.tools.bin_dir, "clock_offset_s": self.clock_offset_s(),
            "errors": self.errors, "live": list(self.live)[:20],
            "tracks": [dict(r) for r in rows],
        }


def close_tracks(db: Database, cfg: Config, campaign_id: int, params: dict[str, Any]) -> None:
    """Close the tracks a campaign left open (service stopped past the end of the campaign)."""
    digital = params.get("digital")
    if not digital:
        return
    TrackBook(db, campaign_id, MODES[digital["mode"]], cfg.digital, cfg.station.locator,
              str(digital.get("band", "")), None).close_all()
