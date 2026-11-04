"""Automatic band mode (cahier des charges, section 9): survey of a band and detection and
classification of its signals from their spectral shape, without demodulation.

The survey alternates the inputs for a while and keeps, for each slice of time, the average
power of every FFT bin. Detection then works on these spectrograms:

- local noise floor per bin: a low percentile of the median spectrum over a sliding window
  (signals occupy little of the band, so the percentile sits on the noise);
- candidate bins: the 90th percentile over time stands ``detect_db`` above the floor
  (intermittent signals count too); contiguous bins, small gaps allowed, form a signal;
- per signal: occupied width, duty cycle (share of slices where it is present), envelope
  spread (standard deviation of its band power in dB while present), SNR;
- class from width and behaviour: narrow and steady = carrier/CW, narrow and intermittent =
  narrow digital, 1.5 to 4 kHz = SSB, 4 to 12 kHz = AM/FM, anything else is ignored;
- ranking: constant envelope first, then steadiness, then SNR (section 9).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.ndimage import percentile_filter

from open_antenna_validation.dsp.measure import Spectrum, db
from open_antenna_validation.hardware import HardwareError, SdrDriver

Floats = npt.NDArray[np.float64]
FLOOR_WINDOW_HZ = 8_000.0
FLOOR_PERCENTILE = 30
GAP_BINS = 2
# Occupied width: bins within this many dB of the signal peak. Measured from the peak, not
# from the floor: the window sidelobes of a strong signal stand far above the floor.
OCCUPIED_DB = 20.0
CLASS_PRIORITY = {"carrier": 3, "digital": 2, "ssb": 1, "am_fm": 1}


def survey(
    driver: SdrDriver, seconds: float, fft_size: int, slice_s: float = 0.5,
    inputs: list[str] | None = None,
) -> dict[str, Any]:
    """Alternate the inputs (default: the first two of the SDR), one slice each; returns the
    per-input spectrograms (linear)."""
    if not inputs:
        device = driver.detect()[0]
        inputs = driver.antennas(device)[:2] or [""]
    rate = driver.sample_rate()
    n = int(rate * slice_s)
    skip = int(rate * 0.05)
    slices = max(len(inputs), int(seconds / slice_s))
    grams: dict[str, list[Floats]] = {name: [] for name in inputs}
    for k in range(slices):
        name = inputs[k % len(inputs)]
        if name:
            driver.set_antenna(name)
        driver.read_iq(skip)
        frames = Spectrum(fft_size, rate).frames(driver.read_iq(n))
        if len(frames):
            grams[name].append(frames.mean(axis=0))
    if not any(grams.values()):
        raise HardwareError("Relevé de la bande impossible : pas d'échantillons.")
    return {"rate": rate, "fft_size": fft_size, "sdr_hz": driver.frequency(),
            "inputs": inputs, "grams": {k: np.array(v) for k, v in grams.items() if v}}


def _groups(mask: npt.NDArray[np.bool_], gap: int) -> list[tuple[int, int]]:
    """Runs of True (end exclusive), joining runs separated by at most ``gap`` False."""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return []
    out = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        i = int(i)
        if i - prev > gap + 1:
            out.append((start, prev + 1))
            start = i
        prev = i
    out.append((start, prev + 1))
    return out


def classify(width_hz: float, duty: float, spread_db: float) -> str | None:
    if width_hz <= 400:
        return "carrier" if duty >= 0.9 and spread_db <= 2.5 else "digital"
    if width_hz < 1_500:
        return "digital"  # several narrow digital signals side by side, PSK...
    if width_hz <= 4_000:
        return "ssb"
    if width_hz <= 12_000:
        return "am_fm"
    return None


def detect(
    grams: dict[str, Floats], rate: float, fft_size: int, sdr_hz: float, *,
    low_hz: float, high_hz: float, usable_hz: float, detect_db: float = 8.0,
    min_snr_db: float = 12.0, min_duty: float = 0.2,
    dc_guard_hz: float = 2_000.0, noise_reach_hz: float = 5_000.0,
    class_width_hz: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Signals found in the spectrograms, best first, inside [low_hz, high_hz]."""
    widths = class_width_hz or {"carrier": 200.0, "digital": 100.0, "ssb": 2_700.0,
                                "am_fm": 8_000.0}
    bin_hz = rate / fft_size
    # Inputs alternate in time: their spectrograms are paired slice by slice, and a signal
    # present on either input counts.
    stacks = list(grams.values())
    t = min(len(g) for g in stacks)
    gram = np.max(np.stack([g[:t] for g in stacks]), axis=0)
    gram_db = np.asarray(db(gram), dtype=np.float64)
    median_db = np.median(gram_db, axis=0)
    window = max(5, int(FLOOR_WINDOW_HZ / bin_hz) | 1)
    floor_db = percentile_filter(median_db, FLOOR_PERCENTILE, size=window, mode="nearest")
    p90_db = np.percentile(gram_db, 90, axis=0)
    freqs = np.asarray(sdr_hz + (np.arange(fft_size) - fft_size / 2) * bin_hz, dtype=np.float64)
    half = usable_hz / 2
    inside = (np.abs(freqs - sdr_hz) <= half - noise_reach_hz) & (freqs >= low_hz) & \
        (freqs <= high_hz)
    candidates: list[dict[str, Any]] = []
    for glo, ghi in _groups((p90_db > floor_db + detect_db) & inside, GAP_BINS):
        top = float(np.max(p90_db[glo:ghi]))
        core = np.nonzero(p90_db[glo:ghi] >= top - OCCUPIED_DB)[0]
        lo, hi = glo + int(core[0]), glo + int(core[-1]) + 1
        width = (hi - lo) * bin_hz
        power = gram[:, lo:hi].sum(axis=1)
        noise = float(np.mean(10 ** (floor_db[lo:hi] / 10)) * (hi - lo))
        present = np.asarray(db(power), dtype=np.float64) > float(db(noise)) + detect_db
        duty = float(present.mean())
        spread = float(np.std(np.asarray(db(power[present])))) if present.sum() > 1 else 0.0
        snr = float(np.percentile(np.asarray(db(power)), 90)) - float(db(noise))
        if duty < min_duty or snr < min_snr_db:
            continue  # too rare or too weak to measure, or a skirt of a stronger signal
        cls = classify(width, duty, spread)
        if cls is None:
            continue
        peak = lo + int(np.argmax(p90_db[lo:hi]))
        centre = float(np.sum(freqs[lo:hi] * gram[:, lo:hi].mean(axis=0))
                       / np.sum(gram[:, lo:hi].mean(axis=0)))
        measure = max(widths[cls], width + 2 * bin_hz) if cls in ("ssb", "am_fm") \
            else max(widths[cls], min(width + 2 * bin_hz, 400.0))
        if abs(centre - sdr_hz) < measure / 2 + dc_guard_hz:
            continue  # the DC spike of the receiver, or a signal sitting on it
        if centre - measure / 2 < low_hz or centre + measure / 2 > high_hz:
            continue
        candidates.append({
            "freq_hz": round(centre, 1), "class": cls, "width_hz": round(measure, 1),
            "occupied_hz": round(width, 1), "duty": round(duty, 3), "spread_db": round(spread, 2),
            "snr_db": round(snr, 1), "peak_hz": round(float(freqs[peak]), 1),
        })
    candidates.sort(key=lambda c: (-CLASS_PRIORITY[c["class"]], -min(c["duty"], 0.95),
                                   c["spread_db"], -c["snr_db"]))
    return {"candidates": candidates, "floor_dbfs": round(float(np.median(floor_db[inside])), 2)
            if inside.any() else None, "slices": t, "bin_hz": bin_hz,
            "quiet": quiet_point(p90_db, freqs, inside, candidates, sdr_hz,
                                 widths.get("ssb", 2_700.0), dc_guard_hz)}


def quiet_point(p90_db: Floats, freqs: Floats, inside: npt.NDArray[np.bool_],
                candidates: list[dict[str, Any]], sdr_hz: float, width_hz: float,
                dc_guard_hz: float) -> dict[str, Any] | None:
    """The quietest place of the band, away from the signals and the DC spike: a noise point
    measured all along the campaign (noise floor of each antenna, even when the band is calm)."""
    bin_hz = float(freqs[1] - freqs[0])
    n = max(1, int(width_hz / bin_hz))
    usable = inside.copy()
    usable &= np.abs(freqs - sdr_hz) >= width_hz / 2 + dc_guard_hz
    for c in candidates:
        usable &= np.abs(freqs - c["freq_hz"]) >= (c["width_hz"] + width_hz) / 2 + 500.0
    level = np.convolve(p90_db, np.ones(n) / n, mode="same")
    ok = np.convolve(usable.astype(float), np.ones(n), mode="same") >= n - 0.5
    if not ok.any():
        return None
    idx = int(np.argmin(np.where(ok, level, np.inf)))
    return {"freq_hz": round(float(freqs[idx]), 1), "class": "noise", "width_hz": width_hz}


def select(candidates: list[dict[str, Any]], max_signals: int,
           min_spacing_hz: float = 500.0,
           already: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The best candidates, without two signals overlapping (``already`` chosen count)."""
    chosen: list[dict[str, Any]] = list(already or [])
    start = len(chosen)
    for c in candidates:
        if len(chosen) >= max_signals:
            break
        if all(abs(c["freq_hz"] - o["freq_hz"]) >= (c["width_hz"] + o["width_hz"]) / 2
               + min_spacing_hz for o in chosen):
            chosen.append(c)
    return chosen[start:]


def same_signal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """A re-survey finds the same signal: same class, centre within half its width."""
    return a["class"] == b["class"] and math.isclose(
        a["freq_hz"], b["freq_hz"], abs_tol=max(a["width_hz"], b["width_hz"]) / 2)
