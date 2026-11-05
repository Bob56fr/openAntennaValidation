"""Level of a decoded transmission, guided by its channel symbols (cahier des charges,
section 10).

For every symbol, the power of each tone of the alphabet over the symbol length (the tones
are orthogonal over a symbol: spacing = 1 / symbol time). The emitted tone carries signal
plus noise; the tones not emitted at the same instant carry noise only (FT8: the tones
next to the emitted one are left out, the Gaussian shaping leaks a little into them).
Signal = mean emitted power − mean noise power.

Levels are on the receiver scale (dBFS of the IQ stream, see ``demod.IQ_TO_AUDIO_DB``);
the floor is the noise power in one tone bin (``mode.spacing_hz`` wide).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from open_antenna_validation.digital.demod import IQ_TO_AUDIO_DB
from open_antenna_validation.digital.modes import AUDIO_RATE, Mode

Audio = npt.NDArray[np.floating]


@dataclass(frozen=True)
class Level:
    level_db: float | None   # None: no signal above the noise
    floor_db: float
    symbols: int             # symbols inside the audio


def _tone_powers(audio: Audio, mode: Mode, f0_hz: float, dt_s: float) -> npt.NDArray[np.float64]:
    """(symbols × tones) power, on the receiver scale; rows outside the audio are dropped."""
    nsps = mode.nsps
    start = round((mode.start_s + dt_s) * AUDIO_RATE)
    idx = start + np.arange(mode.symbols) * nsps
    inside = (idx >= 0) & (idx + nsps <= len(audio))
    if not inside.any():
        return np.zeros((0, mode.tones))
    rows = np.stack([np.asarray(audio[i : i + nsps], dtype=float) for i in idx[inside]])
    n = np.arange(nsps)
    freqs = f0_hz + np.arange(mode.tones) * mode.spacing_hz
    basis = np.exp(-2j * np.pi * np.outer(n, freqs) / AUDIO_RATE)
    spectrum = rows @ basis
    # |X|² · 2 / nsps² is the power of a real tone; x 2 back to the complex receiver scale.
    power = np.abs(spectrum) ** 2 * 2 / nsps**2 * 10 ** (IQ_TO_AUDIO_DB / 10)
    result: npt.NDArray[np.float64] = power
    return result


def guided_level(audio: Audio, mode: Mode, tones: list[int], f0_hz: float, dt_s: float
                 ) -> Level | None:
    """Level of the transmission whose tone 0 is at ``f0_hz``, starting ``dt_s`` after the
    nominal start; None when the transmission is not inside the audio."""
    nsps = mode.nsps
    start = round((mode.start_s + dt_s) * AUDIO_RATE)
    inside = [(start + j * nsps >= 0) and (start + (j + 1) * nsps <= len(audio))
              for j in range(mode.symbols)]
    power = _tone_powers(audio, mode, f0_hz, dt_s)
    if len(power) < mode.symbols // 2:
        return None
    sent = np.asarray([t for t, ok in zip(tones, inside, strict=True) if ok])
    rows = np.arange(len(sent))
    emitted = power[rows, sent]
    mask = np.ones_like(power, dtype=bool)
    mask[rows, sent] = False
    if mode.name == "ft8":
        for d in (-1, 1):
            nb = sent + d
            ok = (nb >= 0) & (nb < mode.tones)
            mask[rows[ok], nb[ok]] = False
    noise = float(power[mask].mean())
    signal = float(emitted.mean()) - noise
    floor_db = 10 * np.log10(max(noise, 1e-30))
    level_db = 10 * np.log10(signal) if signal > 0 else None
    return Level(None if level_db is None else float(level_db), float(floor_db), len(sent))


def hard_tones(audio: Audio, mode: Mode, f0_hz: float, dt_s: float) -> list[int] | None:
    """Strongest tone of each symbol: the channel symbols seen in the audio of a channel
    that decoded the message, when no encoder is installed (WSJT-X for Windows has no
    ft4code nor wsprcode)."""
    power = _tone_powers(audio, mode, f0_hz, dt_s)
    if len(power) != mode.symbols:
        return None
    return [int(t) for t in power.argmax(axis=1)]


def tone0_hz(mode: Mode, reported_hz: float) -> float:
    """Frequency of tone 0: jt9 reports it, wsprd reports the centre of the four tones."""
    if mode.name == "wspr":
        return reported_hz - 1.5 * mode.spacing_hz
    return reported_hz
