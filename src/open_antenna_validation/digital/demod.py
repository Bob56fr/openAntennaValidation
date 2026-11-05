"""Upper-sideband audio at 12 kHz from the IQ stream of a receiver, in real time.

The 3 kHz passband (dial frequency to dial + 3 kHz) is cut out of the IQ stream in the
frequency domain (overlap-save: FFT of blocks of N samples, the bins of the passband kept
with a smooth mask, inverse FFT of K bins), which also decimates by N/K: a few million
operations per second even at 2.4 MS/s. The slot audio is then resampled to exactly
12 kHz and shifted so that the dial frequency is audio 0 Hz.

Levels: a complex tone of power P (dBFS of the receiver) gives a real audio tone of power
P/2; ``IQ_TO_AUDIO_DB`` brings audio levels back to the receiver scale.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from open_antenna_validation.digital.modes import AUDIO_RATE

Complex = npt.NDArray[np.complex128]
AUDIO_CENTER_HZ = 1500.0
PASS_HZ = 1550.0     # half-width of the flat passband around the audio centre
STOP_HZ = 1750.0     # half-width beyond which the mask is zero
IQ_TO_AUDIO_DB = 10 * math.log10(2)


def _pow2_at_least(n: float) -> int:
    return 1 << max(0, math.ceil(math.log2(max(1.0, n))))


class UsbDemodulator:
    """Feed IQ chunks with ``push``; ``take(start, count)`` returns the baseband between two
    input sample indices, at the intermediate rate ``out_rate``."""

    def __init__(self, rate: float, audio_center_offset_hz: float) -> None:
        self.rate = rate
        # Block of about a quarter of a second, K output bins for at least 4 kHz.
        self.n = _pow2_at_least(rate / 4)
        self.k = min(self.n, _pow2_at_least(4 * STOP_HZ * self.n / rate))
        # Overlap-save with a zero-phase mask: N/16 samples are dropped at both block edges.
        self.margin = self.n // 16
        self.step = self.n - 2 * self.margin
        self.drop = self.margin * self.k // self.n
        self.ratio = self.n // self.k         # input samples per output sample
        self.out_rate = rate / self.ratio
        self.shift_bin = round(audio_center_offset_hz * self.n / rate)
        # What the bin shift misses: the audio centre sits at +residual in the baseband.
        self.residual_hz = audio_center_offset_hz - self.shift_bin * rate / self.n
        f = np.fft.fftfreq(self.k, 1 / self.out_rate)
        taper = np.clip((STOP_HZ - np.abs(f)) / (STOP_HZ - PASS_HZ), 0, 1)
        self.mask = 0.5 - 0.5 * np.cos(math.pi * taper)
        self.bins = (self.shift_bin + np.fft.fftfreq(self.k, 1 / self.k).astype(int)) % self.n
        self.buffer = np.zeros(0, dtype=np.complex128)
        self.buffer_start = 0                 # input index of buffer[0]
        self.started = False
        self.out: list[Complex] = []
        self.out_start = 0                    # input index of the first kept output sample

    def reset(self, at_index: int) -> None:
        """Start again at input index ``at_index`` (new frequency, new campaign)."""
        self.buffer = np.zeros(0, dtype=np.complex128)
        self.buffer_start = at_index
        self.started = False
        self.out = []
        self.out_start = at_index

    def push(self, iq: npt.NDArray[np.complexfloating], first_index: int) -> None:
        if not self.started:
            self.reset(first_index)
            self.started = True
            # Leading pad so that the first real sample is kept after the overlap drop.
            pad = self.margin
            self.buffer = np.zeros(pad, dtype=np.complex128)
            self.buffer_start = first_index - pad
        expected = self.buffer_start + len(self.buffer)
        if first_index > expected:  # lost samples: zeros keep the time base
            self.buffer = np.concatenate(
                [self.buffer, np.zeros(first_index - expected, dtype=np.complex128)])
        self.buffer = np.concatenate([self.buffer, np.asarray(iq, dtype=np.complex128)])
        while len(self.buffer) >= self.n:
            self._block(self.buffer[: self.n], self.buffer_start)
            self.buffer = self.buffer[self.step :]
            self.buffer_start += self.step

    def _block(self, x: Complex, start: int) -> None:
        spec = np.fft.fft(x)
        y = np.fft.ifft(spec[self.bins] * self.mask) * (self.k / self.n)
        # The bin shift is relative to the block start: back to the stream's time base.
        y *= np.exp(-2j * math.pi * ((self.shift_bin * start) % self.n) / self.n)
        kept = y[self.drop : self.drop + self.step // self.ratio]
        if not self.out:
            self.out_start = start + self.drop * self.ratio
        self.out.append(np.asarray(kept, dtype=np.complex128))

    @property
    def available_until(self) -> int:
        """Input index up to which the baseband is ready."""
        return self.out_start + sum(len(o) for o in self.out) * self.ratio

    def take(self, start: int, count_in: int) -> Complex:
        """Baseband of input samples [start, start + count_in), zeros where missing; the
        samples before ``start`` are released."""
        data = np.concatenate(self.out) if self.out else np.zeros(0, dtype=np.complex128)
        first = (start - self.out_start) // self.ratio
        n = count_in // self.ratio
        result = np.zeros(n, dtype=np.complex128)
        lo, hi = max(0, first), min(len(data), first + n)
        if hi > lo:
            result[lo - first : hi - first] = data[lo:hi]
        keep_from = max(0, min(len(data), first + n))
        self.out = [data[keep_from:]] if keep_from < len(data) else []
        self.out_start += keep_from * self.ratio
        return result


def to_audio(baseband: Complex, in_rate: float, residual_hz: float,
             seconds: float) -> npt.NDArray[np.float32]:
    """Real audio at 12 kHz, ``seconds`` long, dial frequency at 0 Hz."""
    m = round(seconds * AUDIO_RATE)
    if len(baseband) == 0:
        return np.zeros(m, dtype=np.float32)
    # FFT resampling to exactly 12 kHz (band-limited: the baseband is ±1.75 kHz).
    spec = np.fft.fft(baseband)
    n_new = round(len(baseband) * AUDIO_RATE / in_rate)
    out = np.zeros(n_new, dtype=np.complex128)
    half = min(len(baseband), n_new) // 2
    out[:half] = spec[:half]
    out[-half:] = spec[-half:]
    z = np.fft.ifft(out) * (n_new / len(baseband))
    t = np.arange(len(z)) / AUDIO_RATE
    audio = np.real(z * np.exp(2j * math.pi * (AUDIO_CENTER_HZ - residual_hz) * t))
    result = np.zeros(m, dtype=np.float32)
    result[: min(m, len(audio))] = audio[:m]
    return result
