"""Switching bench (cahier des charges, section 6, level 1): measurements on the driver
and their analysis.

Measurements run in the acquisition worker, which owns the driver. Analysis functions are
pure (numpy arrays in, JSON-ready dicts out) so they can be tested on synthetic streams.

- ``measure_switching``: alternate the inputs every ``dwell`` and keep the whole stream
  with the position of every switch command.
- ``analyze_switching``: with a level step between inputs (attenuator on one branch),
  find the effective switch instant of every switch, hence latency and jitter; average the
  power envelopes aligned on that instant (hundreds of switches: the noise of one switch
  would exceed the ±0.2 dB tolerance) to read the glitch and the settling time. Without a
  step (symmetric splitter), the envelopes are aligned on the command instead.
- ``measure_levels``: average spectrum of each input, alternating to cancel slow drifts,
  reduced to sub-bands: level difference between inputs versus frequency, own noise floor.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import numpy.typing as npt

from open_antenna_validation.dsp.measure import Spectrum, db
from open_antenna_validation.hardware import HardwareError, SdrDriver

Floats = npt.NDArray[np.float64]
MAX_POINTS = 400


def _inputs(driver: SdrDriver) -> list[str]:
    device = driver.detect()[0]
    inputs = driver.antennas(device)
    if len(inputs) < 2:
        raise HardwareError("Ce SDR n'a qu'une entrée : le banc de commutation est sans objet.")
    return inputs[:2]


def _moving_average(x: Floats, n: int) -> Floats:
    """Centred moving average; at the edges, the mean of the samples actually available."""
    if n <= 1:
        return x
    kernel = np.ones(n)
    out: Floats = np.convolve(x, kernel, mode="same") / np.convolve(np.ones(len(x)), kernel,
                                                                    mode="same")
    return out


def _last_sustained(flags: npt.NDArray[np.bool_], min_run: int) -> int:
    """End index of the last run of True at least ``min_run`` long (0 if none).

    The first run is always kept: right after the switch, the transition itself counts.
    """
    edges = np.diff(np.concatenate(([0], flags.astype(np.int8), [0])))
    starts, ends = np.nonzero(edges == 1)[0], np.nonzero(edges == -1)[0]
    last = 0
    for k, (a, b) in enumerate(zip(starts, ends, strict=True)):
        if k == 0 and a == 0 or b - a >= min_run:
            last = int(b)
    return last


def _thin(values: Floats, points: int = MAX_POINTS) -> list[float]:
    step = max(1, math.ceil(len(values) / points))
    return [round(float(v), 3) for v in values[::step]]


# --- switching -------------------------------------------------------------------


def measure_switching(driver: SdrDriver, switches: int, dwell_s: float) -> dict[str, Any]:
    """Alternate the two inputs ``switches`` times, ``dwell_s`` on each; keep the stream."""
    a, b = _inputs(driver)
    rate = driver.sample_rate()
    dwell = int(rate * dwell_s)
    driver.set_antenna(a)
    driver.read_iq(rate // 5)  # settle on the first input
    dropped_before = driver.dropped_samples()
    first = driver.sample_counter()
    chunks = []
    commands: list[tuple[int, str]] = []
    for k in range(switches):
        target = b if k % 2 == 0 else a
        cmd = driver.set_antenna(target)
        if cmd is None:
            raise HardwareError("Le SDR n'est pas ouvert.")
        commands.append((cmd.sample - first, target))
        chunks.append(driver.read_iq(dwell))
    iq = np.concatenate(chunks)
    return {
        "power": (np.abs(iq) ** 2).astype(np.float64),
        "rate": rate,
        "dwell": dwell,
        "commands": commands,
        "inputs": [a, b],
        "dropped": driver.dropped_samples() - dropped_before,
    }


def analyze_switching(
    power: Floats,
    rate: int,
    dwell: int,
    commands: list[tuple[int, str]],
    inputs: list[str],
    *,
    step_min_db: float = 6.0,
    tolerance_db: float = 0.2,
    smoothing_s: float = 1e-4,
) -> dict[str, Any]:
    """Latency, jitter, glitch and settling time of the switches (see module doc)."""
    smooth_n = max(4, int(rate * smoothing_s))
    smooth = _moving_average(power, smooth_n)
    pre_n = max(1, int(0.4 * dwell))
    post_from = int(0.5 * dwell)
    into_b = inputs[1]
    effects: list[int] = []  # index of the effective switch, per command
    latencies: list[float] = []
    steps_db: list[float] = []
    for c, target in commands:
        if c - pre_n < 0 or c + dwell > len(power):
            effects.append(-1)
            continue
        pre = float(np.median(smooth[c - pre_n : c]))
        post = float(np.median(smooth[c + post_from : c + dwell]))
        step = float(db(post) - db(pre))
        steps_db.append(step if target == into_b else -step)
        if abs(step) < step_min_db:
            effects.append(-1)
            continue
        mid = (pre * post) ** 0.5  # midpoint in dB
        seg = smooth[c : c + post_from]
        crossed = np.nonzero(seg >= mid)[0] if post > pre else np.nonzero(seg <= mid)[0]
        if not len(crossed):
            effects.append(-1)
            continue
        effects.append(c + int(crossed[0]))
        latencies.append(crossed[0] / rate * 1e3)

    detected = len(latencies) >= max(3, len(commands) // 4)
    # Envelopes into input B, relative to the final level, aligned on the effect (or command).
    before = int(0.002 * rate)
    after = post_from
    rel: list[Floats] = []
    for (c, target), eff in zip(commands, effects, strict=True):
        if target != into_b:
            continue
        anchor = eff if detected and eff >= 0 else (c if not detected else -1)
        if anchor < before or anchor + after > len(power):
            continue
        # Mean, not median: the median of noise power sits below its mean.
        final = float(np.mean(power[c + post_from : c + dwell]))
        rel.append(power[anchor - before : anchor + after] / max(final, 1e-30))
    if not rel:
        raise HardwareError("Aucune bascule exploitable dans l'enregistrement.")
    stack = np.array(rel)
    mean_db = np.asarray(db(_moving_average(stack.mean(axis=0), smooth_n)), dtype=np.float64)
    per_switch_db = np.asarray(db(np.apply_along_axis(_moving_average, 1, stack, smooth_n)))
    p05 = np.percentile(per_switch_db, 5, axis=0)
    p95 = np.percentile(per_switch_db, 95, axis=0)
    t_ms = (np.arange(-before, after) / rate) * 1e3

    # Settling: last instant after the anchor where the averaged envelope leaves the tolerance.
    # With a detected step, the transition itself is part of the settling time.
    # The averaged envelope keeps some noise: smooth it more for this test, and never use a
    # tolerance below 4 times the residual noise measured at the end of the window.
    settle_env = np.asarray(
        db(_moving_average(stack.mean(axis=0), 3 * smooth_n)), dtype=np.float64)[before:]
    after_anchor = mean_db[before:]
    tail = settle_env[int(0.6 * len(settle_env)) :]
    # 4 sigma: over a few hundred correlated samples, 3 sigma is still crossed by noise alone.
    effective_tol = max(tolerance_db, 4.0 * float(np.std(tail)), abs(float(np.mean(tail))) * 1.5)
    outside = np.nonzero(np.abs(settle_env) > effective_tol)[0]
    settle_ms = float(_last_sustained(np.abs(settle_env) > effective_tol, 3 * smooth_n))         / rate * 1e3
    glitch_db = float(np.max(np.abs(after_anchor[outside[0]:]))) if len(outside) else 0.0
    if detected:
        # Ignore the step itself: the glitch is the overshoot beyond the final level.
        overshoot = after_anchor[after_anchor > 0]
        glitch_db = float(np.max(overshoot)) if len(overshoot) else 0.0

    lat = np.array(latencies) if latencies else np.array([np.nan])
    hist_edges = np.arange(0.0, max(1.0, float(np.nanmax(lat)) * 1.3) + 0.1, 0.1) if detected \
        else np.array([0.0, 0.1])
    hist, _ = np.histogram(lat[~np.isnan(lat)], bins=hist_edges)
    return {
        "detected": detected,
        "switches": len(commands),
        "detections": len(latencies),
        "step_db": round(float(np.median(steps_db)), 2) if steps_db else None,
        "latency_ms": {
            "p50": round(float(np.nanpercentile(lat, 50)), 3),
            "p99": round(float(np.nanpercentile(lat, 99)), 3),
            "max": round(float(np.nanmax(lat)), 3),
            "jitter": round(float(np.nanstd(lat)), 3),
        } if detected else None,
        "settle_ms": round(settle_ms, 3),
        "settle_tolerance_db": round(effective_tol, 3),
        # The source varies too much (voices, fading): settling and glitch are not reliable.
        "noisy": effective_tol > 2 * tolerance_db,
        "glitch_db": round(glitch_db, 2),
        "aligned_on": "effect" if detected else "command",
        "envelope": {
            "t_ms": _thin(t_ms), "mean_db": _thin(mean_db),
            "p05_db": _thin(p05), "p95_db": _thin(p95),
        },
        "histogram": {"edges_ms": [round(float(e), 2) for e in hist_edges[:-1]],
                      "counts": [int(x) for x in hist]},
    }


# --- levels ----------------------------------------------------------------------


def levels_spread_db(levels: dict[str, Any]) -> float:
    """Spread of the input difference across sub-bands: large with a varying source."""
    diff = np.asarray(levels["difference_db"], dtype=np.float64)
    return float(np.median(np.abs(diff - np.median(diff))))


def measure_levels(
    driver: SdrDriver, seconds: float, fft_size: int, subbands: int, *, half_period_s: float = 0.5,
) -> dict[str, Any]:
    """Average spectrum of each input, reduced to ``subbands`` across the usable band."""
    a, b = _inputs(driver)
    rate = driver.sample_rate()
    spec = Spectrum(fft_size, rate)
    sums = {a: np.zeros(fft_size), b: np.zeros(fft_size)}
    counts = {a: 0, b: 0}
    half = int(rate * half_period_s)
    skip = int(rate * 0.05)
    periods = max(2, 2 * round(seconds / (2 * half_period_s)))
    for k in range(periods):
        target = a if k % 2 == 0 else b
        driver.set_antenna(target)
        driver.read_iq(skip)  # past the switch transient
        # A fresh framer per period: no leftover samples of the other input.
        frames = Spectrum(fft_size, rate).frames(driver.read_iq(half))
        sums[target] += frames.sum(axis=0)
        counts[target] += len(frames)
    usable = driver.instant_bandwidth()
    lo = int(spec.bin_of(-usable / 2))
    hi = int(spec.bin_of(usable / 2))
    edges = np.linspace(lo, hi, subbands + 1).astype(int)
    freqs = [driver.frequency() + ((e0 + e1) / 2 - fft_size / 2) * rate / fft_size
             for e0, e1 in itertools.pairwise(edges)]
    levels: dict[str, list[float]] = {}
    floors: dict[str, float] = {}
    totals: dict[str, float] = {}
    for name in (a, b):
        mean = sums[name] / max(1, counts[name])
        levels[name] = [round(float(db(mean[e0:e1].mean())), 3)
                        for e0, e1 in itertools.pairwise(edges)]
        floors[name] = round(float(db(spec.noise_floor_bin(mean))), 2)
        totals[name] = round(float(db(mean[lo:hi].mean())), 3)
    return {
        "inputs": [a, b],
        "freqs_hz": [round(f, 1) for f in freqs],
        "level_db": levels,
        "difference_db": [round(x - y, 3) for x, y in zip(levels[a], levels[b], strict=True)],
        "floor_dbfs": floors,
        "total_db": totals,
        "bin_hz": rate / fft_size,
    }
