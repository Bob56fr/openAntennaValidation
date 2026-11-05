"""Audio of a digital transmission from its channel symbols (simulator, tests).

Same shaping as WSJT-X (lib/ft8/gen_ft8wave.f90, gfsk_pulse.f90): the instantaneous
frequency is the tone sequence smoothed by a Gaussian pulse of bandwidth-time product
``bt`` (FT8 2.0, FT4 1.0), plain FSK for WSPR; short cosine ramps at both ends.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from open_antenna_validation.digital.modes import AUDIO_RATE, Mode

Floats = npt.NDArray[np.float64]


def gfsk_pulse(bt: float, t: Floats) -> Floats:
    c = math.pi * math.sqrt(2 / math.log(2))
    erf = np.vectorize(math.erf)
    return np.asarray(0.5 * (erf(c * bt * (t + 0.5)) - erf(c * bt * (t - 0.5))), dtype=float)


def frequency_track(tones: list[int], mode: Mode, f0_hz: float) -> Floats:
    """Instantaneous frequency of every audio sample of the transmission."""
    nsps = mode.nsps
    steps = np.asarray(tones, dtype=float) * mode.spacing_hz
    if mode.bt <= 0:
        return np.asarray(f0_hz + np.repeat(steps, nsps), dtype=float)
    # Pulse over three symbols, centred on its own symbol (gen_ft8wave: 3*nsps samples).
    t = (np.arange(3 * nsps) - 1.5 * nsps) / nsps
    pulse = gfsk_pulse(mode.bt, t)
    n = len(tones)
    track = np.zeros((n + 2) * nsps)
    for j, step in enumerate(steps):
        track[j * nsps : j * nsps + 3 * nsps] += step * pulse
    # The first and last symbols extend outwards (dummy symbols), as in gen_ft8wave.
    track[: 2 * nsps] += steps[0] * pulse[nsps:]
    track[n * nsps :] += steps[-1] * pulse[: 2 * nsps]
    return np.asarray(f0_hz + track[nsps : (n + 1) * nsps], dtype=float)


def waveform(tones: list[int], mode: Mode, f0_hz: float, amplitude: float = 1.0) -> Floats:
    """Real audio at 12 kHz, ``amplitude`` peak."""
    freq = frequency_track(tones, mode, f0_hz)
    phase = 2 * math.pi * np.cumsum(freq) / AUDIO_RATE
    wave = np.sin(phase)
    ramp = mode.nsps // 8
    edge = 0.5 * (1 - np.cos(math.pi * np.arange(ramp) / ramp))
    wave[:ramp] *= edge
    wave[-ramp:] *= edge[::-1]
    return np.asarray(amplitude * wave, dtype=float)


def complex_waveform(tones: list[int], mode: Mode, offset_hz: float,
                     rate: float) -> npt.NDArray[np.complex128]:
    """Complex baseband transmission at ``rate`` (simulated receiver), tone 0 at
    ``offset_hz`` from the tuned frequency, unit amplitude."""
    freq = frequency_track(tones, mode, offset_hz)
    # Resample the frequency track to the receiver rate (holds within a sample).
    n = round(len(freq) * rate / AUDIO_RATE)
    idx = np.minimum((np.arange(n) * AUDIO_RATE / rate).astype(int), len(freq) - 1)
    phase = 2 * math.pi * np.cumsum(freq[idx]) / rate
    wave = np.exp(1j * phase)
    ramp = max(1, int(mode.symbol_s * rate / 8))
    edge = 0.5 * (1 - np.cos(math.pi * np.arange(ramp) / ramp))
    wave[:ramp] *= edge
    wave[-ramp:] *= edge[::-1]
    return np.asarray(wave)
