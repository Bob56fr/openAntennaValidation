"""Campaign measurement engine (switched mode), run by the acquisition worker.

Each cycle visits every input once: switch, read ``dwell`` samples, drop the first
``blanking`` samples (the switch transient measured by the bench), and measure every frame
of the rest: power of each signal in its width, and the local noise floor around it
(median of the neighbouring bins outside any signal, brought to the signal width).

Every measurement period, one value per signal and per channel goes to the service:
the power mean for carriers and digital signals, the median for voices (SSB, AM) whose
envelope varies; the floor median; quality bits. The campaign clock is the sample counter:
a simulated receiver running faster than real time runs a campaign faster too.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt

from open_antenna_validation.dsp.measure import Spectrum, clipping_ratio, db
from open_antenna_validation.hardware import SdrDriver

Floats = npt.NDArray[np.float64]

Q_SATURATION = 1
Q_LOST = 2
Q_ABSENT = 4
QUALITY_LABELS = {Q_SATURATION: "SATURATION", Q_LOST: "PERTE_FLUX", Q_ABSENT: "ABSENT"}
MEDIAN_CLASSES = ("ssb", "am_fm")
MIN_FFT = 256


def _pow2_below(n: int) -> int:
    return 1 << max(0, math.floor(math.log2(max(1, n))))


class CampaignRunner:
    def __init__(self, driver: SdrDriver, spec: dict[str, Any], start_ms: int) -> None:
        self.driver = driver
        self.spec = spec
        self.inputs: list[str] = spec["inputs"]
        self.rate = driver.sample_rate()
        cycle_hz = float(spec["rate_hz"])
        self.dwell = max(1, int(self.rate / (len(self.inputs) * cycle_hz)))
        self.blank = min(self.dwell - MIN_FFT, int(spec["blanking_ms"] * self.rate / 1000))
        if self.blank < 0:
            raise ValueError("Cadence trop rapide pour la neutralisation après bascule.")
        self.fft = min(int(spec["fft_size"]), _pow2_below(self.dwell - self.blank))
        if self.fft < MIN_FFT:
            raise ValueError("Durée utile par entrée trop courte pour mesurer.")
        self.spectrum = Spectrum(self.fft, self.rate)
        self.period = int(spec["period_s"] * self.rate)
        self.start_ms = start_ms
        self.samples = 0
        self.next_emit = self.period
        self.clip_threshold = float(spec["clip_threshold"])
        self.clip_alert = float(spec["clip_alert_ratio"])
        self._prepare_bins()
        self._reset()

    # --- bins of every signal and of its noise neighbourhood ----------------------

    def _prepare_bins(self) -> None:
        size, enbw = self.fft, self.spectrum.enbw_bins
        bin_hz = self.rate / size
        occupied = np.zeros(size, dtype=bool)
        spans = []
        for s in self.spec["signals"]:
            lo = max(0, math.ceil(self.spectrum.bin_of(s["offset_hz"] - s["width_hz"] / 2)))
            top = self.spectrum.bin_of(s["offset_hz"] + s["width_hz"] / 2)
            hi = min(size, math.floor(top) + 1)
            hi = max(hi, lo + 1)
            spans.append((lo, hi))
            occupied[max(0, lo - 2) : min(size, hi + 2)] = True
        centre = size // 2
        occupied[centre - 2 : centre + 3] = True  # DC spike
        self.signal_bins = spans
        self.noise_bins = []
        self.noise_scale = []
        for s, (lo, hi) in zip(self.spec["signals"], spans, strict=True):
            reach = max(5_000.0, 3 * s["width_hz"]) / bin_hz
            nlo = max(0, int(lo - reach))
            nhi = min(size, int(hi + reach))
            idx = np.arange(nlo, nhi)
            idx = idx[~occupied[idx]]
            self.noise_bins.append(idx)
            # Floor per bin brought to the measured width (bins of the signal / ENBW).
            self.noise_scale.append((hi - lo) / enbw)
        self.enbw = enbw

    def _reset(self) -> None:
        n_ch, n_sig = len(self.inputs), len(self.spec["signals"])
        self.power: list[list[list[float]]] = [[[] for _ in range(n_sig)] for _ in range(n_ch)]
        self.floor: list[list[list[float]]] = [[[] for _ in range(n_sig)] for _ in range(n_ch)]
        self.spectra = [np.zeros(self.fft) for _ in range(n_ch)]
        self.spectra_n = [0] * n_ch
        self.saturated = [False] * n_ch
        self.dropped_at_start = self.driver.dropped_samples()

    # --- one cycle -----------------------------------------------------------------

    def cycle(self) -> list[dict[str, Any]]:
        """Visit every input once; returns the events to send (measurement, spectra)."""
        for ch, name in enumerate(self.inputs):
            self.driver.set_antenna(name)
            chunk = self.driver.read_iq(self.dwell)
            self.samples += self.dwell
            usable = chunk[self.blank :]
            if clipping_ratio(usable, self.clip_threshold) >= self.clip_alert:
                self.saturated[ch] = True
            frames = Spectrum(self.fft, self.rate).frames(usable)
            if not len(frames):
                continue
            self.spectra[ch] += frames.sum(axis=0)
            self.spectra_n[ch] += len(frames)
            for k, ((lo, hi), nb, scale) in enumerate(
                zip(self.signal_bins, self.noise_bins, self.noise_scale, strict=True)
            ):
                p = frames[:, lo:hi].sum(axis=1) / self.enbw
                self.power[ch][k].extend(p.tolist())
                if len(nb):
                    fl = np.median(frames[:, nb], axis=1) * scale
                    self.floor[ch][k].extend(fl.tolist())
        if self.samples < self.next_emit:
            return []
        events = self._emit()
        self.next_emit += self.period
        return events

    def _emit(self) -> list[dict[str, Any]]:
        ts = self.start_ms + int((self.next_emit - self.period) * 1000 / self.rate)
        lost = self.driver.dropped_samples() > self.dropped_at_start
        values = []
        for ch in range(len(self.inputs)):
            for k, s in enumerate(self.spec["signals"]):
                powers = self.power[ch][k]
                floors = self.floor[ch][k]
                if not powers:
                    continue
                arr = np.asarray(powers)
                level = float(np.median(arr)) if s["class"] in MEDIAN_CLASSES else float(arr.mean())
                floor = float(np.median(floors)) if floors else None
                quality = (Q_SATURATION if self.saturated[ch] else 0) | (Q_LOST if lost else 0)
                values.append([s["id"], ch, round(float(db(level)), 2),
                               None if floor is None else round(float(db(floor)), 2),
                               quality, len(powers)])
        events: list[dict[str, Any]] = [{
            "type": "measurement", "campaign": self.spec["campaign"], "ts_ms": ts,
            "period_ms": int(self.period * 1000 / self.rate), "values": values,
        }]
        group = max(1, self.fft // int(self.spec.get("display_points", 1024)))
        for ch, name in enumerate(self.inputs):
            if not self.spectra_n[ch]:
                continue
            mean = self.spectra[ch] / self.spectra_n[ch]
            in_db = np.asarray(db(mean))[: (self.fft // group) * group]
            trimmed = in_db.reshape(-1, group).max(axis=1)
            events.append({
                "type": "spectrum", "ts_ms": ts, "antenna": name, "campaign": self.spec["campaign"],
                "center_hz": self.spec["center_hz"],
                "f_start_hz": self.driver.frequency() - self.rate / 2,
                "f_step_hz": self.rate / self.fft * group,
                "bandwidth_hz": self.driver.instant_bandwidth(),
                "bins_dbfs": [round(float(v), 1) for v in trimmed],
                "floor_dbfs": round(float(db(self.spectrum.noise_floor_bin(mean))), 1),
                "clip_ratio": 0.0, "clip_alert": self.saturated[ch],
                "overload": self.driver.overload(),
                "dropped": self.driver.dropped_samples(), "frames": self.spectra_n[ch],
            })
        self._reset()
        return events
