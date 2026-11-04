"""Buffer between a vendor streaming thread and read_iq(), with absolute sample indices."""

from __future__ import annotations

import threading
from collections import deque

import numpy as np

from open_antenna_validation.hardware.base import HardwareError, IQBlock

# Depth of the buffer, in seconds of delivered samples.
BUFFER_SECONDS = 2.0


class SampleRing:
    """Blocks pushed by the library thread, read back in exact lengths.

    Samples lost upstream (``gap``) and samples dropped because the reader was too slow
    both advance the absolute index, so sample_counter() stays in step with the stream.
    """

    def __init__(self) -> None:
        self._blocks: deque[tuple[int, IQBlock]] = deque()
        self._buffered = 0
        self._next_index = 0
        self._dropped = 0
        self._failure: str | None = None
        self._ready = threading.Condition(threading.Lock())

    def reset(self) -> None:
        with self._ready:
            self._blocks.clear()
            self._buffered = 0
            self._next_index = 0
            self._dropped = 0
            self._failure = None

    def push(self, block: IQBlock, rate: int, gap: int = 0) -> None:
        limit = int(rate * BUFFER_SECONDS)
        with self._ready:
            if gap > 0:
                self._dropped += gap
                self._next_index += gap
            self._blocks.append((self._next_index, block))
            self._next_index += len(block)
            self._buffered += len(block)
            while self._buffered > limit and len(self._blocks) > 1:
                _, old = self._blocks.popleft()
                self._buffered -= len(old)
                self._dropped += len(old)
            self._ready.notify_all()

    def fail(self, message: str) -> None:
        """The stream ended on its own (device unplugged...): readers get ``message``."""
        with self._ready:
            self._failure = message
            self._ready.notify_all()

    def close(self) -> None:
        with self._ready:
            self._blocks.clear()
            self._buffered = 0
            self._ready.notify_all()

    def counter(self) -> int:
        with self._ready:
            return self._blocks[0][0] if self._blocks else self._next_index

    @property
    def dropped(self) -> int:
        return self._dropped

    def read(self, n: int, timeout_s: float, timeout_message: str) -> IQBlock:
        with self._ready:
            self._ready.wait_for(
                lambda: self._buffered >= n or self._failure is not None, timeout=timeout_s)
            if self._buffered < n:
                raise HardwareError(self._failure if self._failure else timeout_message)
            out = np.empty(n, dtype=np.complex64)
            filled = 0
            while filled < n:
                start, block = self._blocks[0]
                take = min(len(block), n - filled)
                out[filled : filled + take] = block[:take]
                filled += take
                if take == len(block):
                    self._blocks.popleft()
                else:
                    self._blocks[0] = (start + take, block[take:])
            self._buffered -= n
            return out
