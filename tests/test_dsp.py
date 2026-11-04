from __future__ import annotations

import numpy as np
import pytest

from open_antenna_validation.dsp.measure import PeakHold, Spectrum, clipping_ratio, db

FS = 250_000


def tone(freq: float, n: int, fs: float = FS, amplitude: float = 1.0) -> np.ndarray:
    return (amplitude * np.exp(2j * np.pi * freq * np.arange(n) / fs)).astype(np.complex64)


def test_spectrum_levels_in_dbfs() -> None:
    spec = Spectrum(2048, FS)
    x = tone(25_000, 2048 * 20, amplitude=0.1)  # -20 dBFS
    powers = spec.frames(x).mean(axis=0)
    assert db(powers.max()) == pytest.approx(-20, abs=1.5)  # scalloping loss at most
    assert db(spec.band_power(powers, 25_000, 2_700)) == pytest.approx(-20, abs=0.1)


def test_noise_floor_and_band_power_of_white_noise() -> None:
    rng = np.random.default_rng(1)
    n = 4096 * 50
    sigma2 = 10 ** (-60 / 10)
    noise = np.sqrt(sigma2 / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    signal_ = noise + tone(60_000, n, amplitude=0.01)
    spec = Spectrum(4096, FS)
    powers = spec.frames(signal_.astype(np.complex64)).mean(axis=0)
    floor = spec.noise_floor_bin(powers)
    # White noise of -60 dBFS over 250 kHz: -60 - 10 log10(250 000 / 2 700) in an SSB width.
    expected = -60 - 10 * np.log10(FS / 2_700)
    assert db(spec.noise_in_band(floor, 2_700)) == pytest.approx(expected, abs=0.5)
    assert db(spec.band_power(powers, -40_000, 2_700)) == pytest.approx(expected, abs=0.7)


def test_spectrum_keeps_leftover_samples() -> None:
    spec = Spectrum(1024, FS)
    assert spec.frames(np.zeros(1000, dtype=np.complex64)).shape == (0, 1024)
    assert spec.frames(np.zeros(1100, dtype=np.complex64)).shape == (2, 1024)


def test_clipping_ratio() -> None:
    x = np.zeros(1000, dtype=np.complex64)
    x[:5] = 1.0 + 0j
    x[5] = 0.2 - 0.995j
    assert clipping_ratio(x, 0.99) == pytest.approx(0.006)


def test_peak_hold() -> None:
    hold = PeakHold(2.0)
    assert hold.update(0.0, -50) == -50
    assert hold.update(1.0, -70) == -50
    assert hold.update(2.5, -70) == -70
