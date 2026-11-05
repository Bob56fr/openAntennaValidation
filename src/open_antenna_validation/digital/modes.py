"""Digital modes decoded by WSJT-X (decision D22): timing and tone layout.

Values from the WSJT-X sources (lib/ft8/ft8_params.f90, lib/ft4/ft4_params.f90,
lib/wsprd/wsprd.c) and checked against the encoders (ft8code, ft4code, wsprcode): audio at
12 kHz, ``nsps`` samples per symbol, tone spacing = 12000 / nsps (WSPR: 12000 / 8192).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AUDIO_RATE = 12_000
ModeName = Literal["ft8", "ft4", "wspr"]


@dataclass(frozen=True)
class Mode:
    name: ModeName
    label: str
    slot_s: float          # receive period
    nsps: int              # samples per symbol at 12 kHz
    symbols: int           # channel symbols of a transmission
    tones: int             # tones of the alphabet
    start_s: float         # nominal start of a transmission after the slot start
    bt: float              # Gaussian smoothing of the frequency (0: plain FSK)
    audio_low_hz: float    # audio band searched by the decoder
    audio_high_hz: float

    @property
    def spacing_hz(self) -> float:
        return AUDIO_RATE / self.nsps

    @property
    def symbol_s(self) -> float:
        return self.nsps / AUDIO_RATE

    @property
    def duration_s(self) -> float:
        return self.symbols * self.symbol_s

    @property
    def width_hz(self) -> float:
        """Occupied bandwidth of one transmission."""
        return self.tones * self.spacing_hz

    @property
    def slot_ms(self) -> int:
        return round(self.slot_s * 1000)


MODES: dict[ModeName, Mode] = {
    "ft8": Mode("ft8", "FT8", 15.0, 1920, 79, 8, 0.5, 2.0, 200.0, 3000.0),
    "ft4": Mode("ft4", "FT4", 7.5, 576, 105, 4, 0.5, 1.0, 200.0, 3000.0),
    "wspr": Mode("wspr", "WSPR", 120.0, 8192, 162, 4, 1.0, 0.0, 1400.0, 1600.0),
}


def slot_start_ms(ts_ms: int, mode: Mode) -> int:
    """Start of the UTC slot that contains ``ts_ms``."""
    return ts_ms - ts_ms % mode.slot_ms


def slot_parity(slot_ms: int, mode: Mode) -> int:
    """0 for the even slots of the minute (FT8 :00 and :30, FT4 :00, :15…), else 1."""
    return (slot_ms // mode.slot_ms) % 2
