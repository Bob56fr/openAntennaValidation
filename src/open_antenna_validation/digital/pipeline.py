"""One slot of a digital campaign: decode every channel, measure every message on every
channel, store the decodes, update the tracks.

Runs in the service (the acquisition workers only send the slot audio): a message decoded
on one antenna only is still measured on the others, at the same tones and instants
(cahier des charges, section 10).
"""

from __future__ import annotations

import logging
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from open_antenna_validation.digital import measure, messages, wsjtx
from open_antenna_validation.digital.modes import Mode
from open_antenna_validation.digital.tracks import SlotMessage, TrackBook
from open_antenna_validation.digital.wsjtx import Decoded, Tools
from open_antenna_validation.storage import Database

log = logging.getLogger(__name__)
Audio = npt.NDArray[np.float32]


@dataclass
class SlotResult:
    slot_ms: int
    decodes: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    dt_median_s: float | None = None


class SlotProcessor:
    def __init__(self, db: Database, tools: Tools, mode: Mode, campaign_id: int,
                 dial_hz: float, workdir: Path, book: TrackBook, depth: int,
                 overlap_hz: float) -> None:
        self.db = db
        self.tools = tools
        self.mode = mode
        self.campaign_id = campaign_id
        self.dial_hz = dial_hz
        self.workdir = workdir
        self.book = book
        self.depth = depth
        self.overlap_hz = overlap_hz
        self.encoded: dict[str, list[int] | None] = {}
        self.pool = ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) - 1)),
                                       thread_name_prefix="decode")

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _decode(self, channel: int, audio: Audio, slot_ms: int) -> list[Decoded]:
        return wsjtx.decode(self.tools, self.mode, audio, slot_ms, self.dial_hz,
                            self.workdir / f"ch{channel}", self.depth)

    def _tones(self, message: str) -> list[int] | None:
        if message not in self.encoded:
            self.encoded[message] = wsjtx.encode(self.tools, self.mode, message,
                                                 self.workdir / "encode")
        return self.encoded[message]

    def process(self, slot_ms: int, audio: dict[int, Audio]) -> SlotResult:
        channels = sorted(audio)
        found = dict(zip(channels, self.pool.map(
            lambda ch: self._decode(ch, audio[ch], slot_ms), channels), strict=True))
        # The same message decoded on several channels is one transmission.
        by_message: dict[str, dict[int, Decoded]] = {}
        for ch in channels:
            for d in found[ch]:
                by_message.setdefault(d.message, {})[ch] = d
        result = SlotResult(slot_ms)
        dts = [d.dt_s for per in by_message.values() for d in per.values()
               if not d.a_priori]
        result.dt_median_s = statistics.median(dts) if dts else None

        slot: list[SlotMessage] = []
        rows: list[tuple[Any, ...]] = []
        for text, per in by_message.items():
            best = max(per.values(), key=lambda d: d.snr_db)
            best_ch = next(ch for ch, d in per.items() if d is best)
            f0 = measure.tone0_hz(self.mode, best.audio_hz)
            doubtful = all(d.flag == "?" for d in per.values())
            tones = None if doubtful else self._tones(text)
            guided = "tones" if tones else ""
            if tones is None and not doubtful:
                tones = measure.hard_tones(audio[best_ch], self.mode, f0, best.dt_s)
                guided = "seen" if tones else ""
            sender = messages.parse(self.mode.name, text)
            measured = False
            msg_rows = []
            for ch in channels:
                dec = per.get(ch)
                level = None
                if tones is not None:
                    dt = dec.dt_s if dec is not None else best.dt_s
                    f = measure.tone0_hz(self.mode, dec.audio_hz) if dec is not None else f0
                    level = measure.guided_level(audio[ch], self.mode, tones, f, dt)
                if level is not None and level.level_db is not None:
                    measured = True
                msg_rows.append((
                    self.campaign_id, ch, slot_ms, self.mode.label, sender.call,
                    sender.grid, text, None if dec is None else dec.snr_db,
                    (dec or best).audio_hz, sender.power_dbm, int(dec is not None),
                    (dec or best).dt_s,
                    None if level is None or level.level_db is None
                    else round(level.level_db, 2),
                    None if level is None else round(level.floor_db, 2),
                    guided if level is not None else "", _flag(dec)))
            rows.extend(msg_rows)
            slot.append(SlotMessage(text, sender, best.audio_hz, measured, doubtful=doubtful))
            result.messages.append({"message": text, "call": sender.call, "grid": sender.grid,
                                    "audio_hz": best.audio_hz, "snr_db": best.snr_db,
                                    "channels": sorted(per)})
        # Two different stations closer than overlap_hz in the same slot.
        for a in slot:
            for b in slot:
                if a is not b and a.sender.call != b.sender.call \
                        and abs(a.audio_hz - b.audio_hz) < self.overlap_hz:
                    a.overlap = True
        with self.db.transaction() as conn:
            for msg in slot:
                for row in [r for r in rows if r[6] == msg.message]:
                    flags = row[15] + (" overlap" if msg.overlap else "")
                    cur = conn.execute(
                        "INSERT INTO decodes (campaign_id, channel_idx, slot_ms, digital_mode,"
                        " callsign, locator, message, snr_db, audio_hz, power_dbm, decoded, dt_s,"
                        " level_dbfs, floor_dbfs, guided, flags) VALUES"
                        " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (*row[:15], flags.strip()))
                    msg.decode_ids.append(int(cur.lastrowid or 0))
        result.decodes = sum(len(v) for v in found.values())
        self.book.update(slot_ms, slot)
        return result


def _flag(d: Decoded | None) -> str:
    if d is None:
        return ""
    return d.flag
