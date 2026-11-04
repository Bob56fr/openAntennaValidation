"""Level measurements on IQ blocks.

Convention: a complex sine of amplitude 1 is 0 dBFS. FFT bin powers are
normalised so that a tone reads its own power in its peak bin; band powers sum
the bins and divide by the window's equivalent noise bandwidth, so they read
the true power of noise and tones alike.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
# Bins this far above the first median are treated as signals for the noise floor.
SIGNAL_MARGIN_DB = 6.0
# The noise floor ignores the band edges (anti-aliasing roll-off) and the DC spike.
FLOOR_USABLE_FRACTION = 0.8
DC_GUARD_BINS = 3


def db(power: float | FloatArray) -> float | FloatArray:
    return 10 * np.log10(np.maximum(power, 1e-20))


def clipping_ratio(iq: npt.NDArray[np.complex64], threshold: float) -> float:
    if len(iq) == 0:
        return 0.0
    rails = np.maximum(np.abs(iq.real), np.abs(iq.imag))
    return float(np.mean(rails >= threshold))


class Spectrum:
    """Hann-windowed FFT frames, centred (index 0 = -fs/2)."""

    def __init__(self, size: int, fs: float) -> None:
        self.size = size
        self.fs = fs
        self._window = np.hanning(size)
        coherent = float(np.sum(self._window))
        self._scale = 1.0 / coherent**2
        # Equivalent noise bandwidth, in bins (1.5 for Hann).
        self.enbw_bins = size * float(np.sum(self._window**2)) / coherent**2
        self._pending = np.zeros(0, dtype=np.complex64)

    def frames(self, iq: npt.NDArray[np.complex64]) -> FloatArray:
        """Bin powers of each complete frame (rows); leftovers wait for the next block."""
        data = np.concatenate([self._pending, iq]) if len(self._pending) else iq
        count = len(data) // self.size
        self._pending = data[count * self.size :].copy()
        if count == 0:
            return np.zeros((0, self.size))
        blocks = data[: count * self.size].reshape(count, self.size) * self._window
        spectra = np.fft.fftshift(np.fft.fft(blocks, axis=1), axes=1)
        powers: FloatArray = (np.abs(spectra) ** 2) * self._scale
        return powers

    def bin_of(self, offset_hz: float) -> float:
        return offset_hz / self.fs * self.size + self.size / 2

    def band_power(self, powers: FloatArray, offset_hz: float, width_hz: float) -> FloatArray:
        """Power in [offset - width/2, offset + width/2] for each frame (last axis = bins)."""
        lo = max(0, math.ceil(self.bin_of(offset_hz - width_hz / 2)))
        hi = min(self.size, math.floor(self.bin_of(offset_hz + width_hz / 2)) + 1)
        result: FloatArray = np.sum(powers[..., lo:hi], axis=-1) / self.enbw_bins
        return result

    def noise_floor_bin(self, mean_powers: FloatArray) -> float:
        """Median bin power outside signals, over the usable part of the band."""
        margin = int(self.size * (1 - FLOOR_USABLE_FRACTION) / 2)
        usable = np.ones(self.size, dtype=bool)
        usable[:margin] = False
        usable[self.size - margin :] = False
        centre = self.size // 2
        usable[centre - DC_GUARD_BINS : centre + DC_GUARD_BINS + 1] = False
        values = mean_powers[usable]
        first = float(np.median(values))
        quiet = values[values < first * 10 ** (SIGNAL_MARGIN_DB / 10)]
        return float(np.median(quiet)) if len(quiet) else first

    def noise_in_band(self, floor_bin: float, width_hz: float) -> float:
        """Noise power in a band of ``width_hz`` for a given per-bin floor."""
        bins = width_hz / self.fs * self.size
        return floor_bin * bins / self.enbw_bins


class PeakHold:
    """Maximum over the last ``hold_s`` seconds."""

    def __init__(self, hold_s: float) -> None:
        self.hold_s = hold_s
        self._values: deque[tuple[float, float]] = deque()

    def update(self, t: float, value: float) -> float:
        while self._values and self._values[-1][1] <= value:
            self._values.pop()
        self._values.append((t, value))
        while self._values[0][0] < t - self.hold_s:
            self._values.popleft()
        return self._values[0][1]

    def reset(self) -> None:
        self._values.clear()
