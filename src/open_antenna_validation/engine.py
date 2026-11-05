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
import random
import time
from typing import Any

import numpy as np
import numpy.typing as npt

from open_antenna_validation.digital import demod
from open_antenna_validation.digital.modes import MODES
from open_antenna_validation.dsp.measure import Spectrum, clipping_ratio, db
from open_antenna_validation.hardware import SdrDriver

Floats = npt.NDArray[np.float64]

Q_SATURATION = 1
Q_LOST = 2
Q_ABSENT = 4
Q_MISALIGNED = 8
QUALITY_LABELS = {Q_SATURATION: "SATURATION", Q_LOST: "PERTE_FLUX", Q_ABSENT: "ABSENT",
                  Q_MISALIGNED: "DÉSALIGNÉE"}
MEDIAN_CLASSES = ("ssb", "am_fm")
MIN_FFT = 256
# One input per SDR (simultaneous mode): no switching, chunks of this length.
CHUNK_S = 0.1


def _pow2_below(n: int) -> int:
    return 1 << max(0, math.floor(math.log2(max(1, n))))


class CampaignRunner:
    def __init__(self, driver: SdrDriver, spec: dict[str, Any], start_ms: int) -> None:
        self.driver = driver
        self.spec = spec
        self.inputs: list[str] = spec["inputs"]
        self.rate = driver.sample_rate()
        # Simultaneous mode: this SDR measures one channel, numbered from channel_base.
        self.channel_base = int(spec.get("channel_base", 0))
        self.switching = len(self.inputs) > 1
        # Digital campaign: decodable slots, switching only between two slots (section 10).
        self.digital: dict[str, Any] | None = spec.get("digital")
        if self.digital is not None:
            self.dwell = int(self.rate * CHUNK_S)
            self.blank = int(spec["blanking_ms"] * self.rate / 1000) if self.switching else 0
            if not self.switching and self.inputs[0]:
                driver.set_antenna(self.inputs[0])
        elif self.switching:
            cycle_hz = float(spec["rate_hz"])
            self.dwell = max(1, int(self.rate / (len(self.inputs) * cycle_hz)))
            self.blank = min(self.dwell - MIN_FFT,
                             int(spec["blanking_ms"] * self.rate / 1000))
        else:
            self.dwell = int(self.rate * CHUNK_S)
            self.blank = 0
            if self.inputs[0]:
                driver.set_antenna(self.inputs[0])
        if self.blank < 0:
            raise ValueError("Cadence trop rapide pour la neutralisation après bascule.")
        self.fft = min(int(spec["fft_size"]), _pow2_below(self.dwell - self.blank))
        if self.fft < MIN_FFT:
            raise ValueError("Durée utile par entrée trop courte pour mesurer.")
        self.spectrum = Spectrum(self.fft, self.rate)
        self.period = int(spec["period_s"] * self.rate)
        # The service gives the start of the measurement grid: the same for every SDR of a
        # simultaneous campaign, and after the last period when the engine restarts.
        self.start_ms = int(spec["start_ms"]) if spec.get("start_ms") else start_ms
        self.samples = 0
        self.next_emit = self.period
        self.clip_threshold = float(spec["clip_threshold"])
        self.clip_alert = float(spec["clip_alert_ratio"])
        # Simultaneous mode: the first period starts at the wall-clock instant start_ms on
        # every SDR (real receivers; a simulator faster than real time has no wall clock).
        self.aligned = not (spec.get("align") and driver.realtime)
        self._prepare_bins()
        self._reset()
        if self.digital is not None:
            self._prepare_digital(self.digital)

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
        # Load of this worker over the period (test before launch) and wall time of the
        # first sample of the period (alignment of the SDRs, real receivers only).
        self.cpu_start = time.process_time()
        self.busy_s = 0.0
        self.period_first: int | None = None
        self.epoch = math.inf

    def _observe(self) -> None:
        """Wall time of sample 0 of the stream, seen after a read: a read returns when its
        last sample has arrived or later (backlog, busy processor), never earlier, so the
        smallest value over many reads is the best estimate."""
        self.epoch = min(self.epoch, time.time() - self.driver.sample_counter() / self.rate)

    # --- one cycle -----------------------------------------------------------------

    def _wait_for_start(self) -> None:
        """Drop the samples received before the common start instant.

        The wall time of a sample is known when it is read without delay: first catch up
        with the stream (a read that has to wait means no backlog is left), then drop up to
        the start instant.
        """
        self.aligned = True
        step = max(1, int(self.rate * 0.005))
        for _ in range(400):
            begin = time.perf_counter()
            self.driver.read_iq(step)
            if time.perf_counter() - begin > 0.002:
                break
        for _ in range(40):  # 0.2 s of reads at the live edge of the stream
            self.driver.read_iq(step)
            self._observe()
        start_sample = int((self.start_ms / 1000 - self.epoch) * self.rate)
        skip = start_sample - self.driver.sample_counter()
        if 0 < skip < 10 * self.rate:
            self.driver.read_iq(skip)
        self._reset()

    # --- digital campaigns ----------------------------------------------------------

    def _prepare_digital(self, digital: dict[str, Any]) -> None:
        self.dmode = MODES[digital["mode"]]
        self.dial_hz = float(digital["dial_hz"])
        offset = self.dial_hz + demod.AUDIO_CENTER_HZ - self.driver.frequency()
        self.demods = [demod.UsbDemodulator(self.rate, offset) for _ in self.inputs]
        self.covered: list[int | None] = [None] * len(self.inputs)
        self.pending: list[int | None] = [None] * len(self.inputs)
        self.current: int | None = None if self.switching else 0
        self.blank_left = 0
        self.seed = int(digital.get("seed", 0))
        air = self.driver.air_epoch()
        self.clock: float | None = air
        if air is not None and not self.driver.realtime:
            # Simulator faster than real time: its own air clock; start on a switching
            # block, as the service does for real receivers.
            block_ms = int(digital["block_ms"])
            now = air + self.driver.sample_counter() / self.rate
            start_ms = max(int(self.start_ms), math.ceil(now * 1000))
            start_ms = -(-start_ms // block_ms) * block_ms
            skip = round((start_ms / 1000 - now) * self.rate)
            while skip > 0:
                n = min(skip, self.dwell)
                self.driver.read_iq(n)
                skip -= n
            self.start_ms = start_ms
            self._reset()

    def _epoch(self) -> float:
        """UTC time of sample 0: exact (simulator), else estimated from the reads and
        refreshed every measurement period (the sample clock drifts from UTC)."""
        if self.clock is None:
            if not math.isfinite(self.epoch):
                self._observe()
            self.clock = self.epoch
        return self.clock

    def _input_for(self, slot: int) -> int:
        n = len(self.inputs)
        if self.dmode.name == "wspr":
            # Balanced pseudo-random order: each antenna once per block of n slots.
            order = list(range(n))
            random.Random(self.seed * 1_000_003 + slot // n).shuffle(order)
            return order[slot % n]
        # FT8 / FT4: every two slots, so that a station sending one slot in two is received
        # alternately on each antenna.
        return (slot // 2) % n

    def _index_of(self, t_s: float) -> int:
        return round((t_s - self._epoch()) * self.rate)

    def _digital_cycle(self) -> list[dict[str, Any]]:
        if not self.aligned:
            self._wait_for_start()
        slot_s = self.dmode.slot_s
        idx = self.driver.sample_counter()
        k = math.floor((self._epoch() + idx / self.rate) / slot_s + 1e-9)
        flushed: list[dict[str, Any]] = []
        if self.switching:
            ch = self._input_for(k)
            if ch != self.current:
                if self.current is not None:
                    # Zeros after the last sample push the end of the last slot of the
                    # antenna that leaves through the demodulator.
                    old = self.demods[self.current]
                    old.push(np.zeros(old.n, dtype=np.complex64), idx)
                    flushed = self._slots_ready(self.current)
                self.driver.set_antenna(self.inputs[ch])
                self.current = ch
                self.blank_left = self.blank
                self.demods[ch].started = False
                self.covered[ch] = idx
                self.pending[ch] = None
        else:
            ch = 0
        boundary = self._index_of((k + 1) * slot_s)
        n = max(1, min(self.dwell, boundary - idx))
        chunk = self.driver.read_iq(n)
        work_start = time.perf_counter()
        first = self.driver.sample_counter() - n
        if self.driver.realtime:
            self._observe()
            if self.period_first is None:
                self.period_first = first
        self.samples += n
        usable = chunk[min(self.blank_left, n) :]
        self.blank_left = max(0, self.blank_left - n)
        self._measure_chunk(ch, usable)
        if self.covered[ch] is None:
            self.covered[ch] = first
        self.demods[ch].push(chunk, first)
        events = flushed + self._slots_ready(ch)
        self.busy_s += time.perf_counter() - work_start
        if self.samples >= self.next_emit:
            if self.driver.air_epoch() is None and math.isfinite(self.epoch):
                self.clock = self.epoch  # best estimate of the period that ends
            events += self._emit()
            self.next_emit += self.period
        return events

    def _slots_ready(self, ch: int) -> list[dict[str, Any]]:
        """Audio of every slot of channel ``ch`` received in full since its activation."""
        d = self.demods[ch]
        slot_s = self.dmode.slot_s
        out: list[dict[str, Any]] = []
        if self.pending[ch] is None:
            covered = self.covered[ch] or 0
            self.pending[ch] = math.ceil((self._epoch() + covered / self.rate) / slot_s - 1e-6)
        while True:
            k = int(self.pending[ch] or 0)
            start = self._index_of(k * slot_s)
            end = self._index_of((k + 1) * slot_s)
            if d.available_until < end:
                return out
            audio = demod.to_audio(d.take(start, end - start), d.out_rate, d.residual_hz,
                                   slot_s)
            out.append({"type": "slot_audio", "campaign": self.spec["campaign"],
                        "channel": self.channel_base + ch, "slot_ms": round(k * slot_s * 1000),
                        "mode": self.dmode.name, "audio": audio.tobytes()})
            self.pending[ch] = k + 1

    def _measure_chunk(self, ch: int, usable: Any) -> None:
        if len(usable) < self.fft:
            return
        if clipping_ratio(usable, self.clip_threshold) >= self.clip_alert:
            self.saturated[ch] = True
        frames = Spectrum(self.fft, self.rate).frames(usable)
        if not len(frames):
            return
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

    def cycle(self) -> list[dict[str, Any]]:
        """Visit every input once; returns the events to send (measurement, spectra)."""
        if self.digital is not None:
            return self._digital_cycle()
        if not self.aligned:
            self._wait_for_start()
        for ch, name in enumerate(self.inputs):
            if self.switching:
                self.driver.set_antenna(name)
            chunk = self.driver.read_iq(self.dwell)
            work_start = time.perf_counter()
            if self.driver.realtime:
                self._observe()
                if self.period_first is None:
                    self.period_first = self.driver.sample_counter() - self.dwell
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
            self.busy_s += time.perf_counter() - work_start
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
                values.append([s["id"], self.channel_base + ch, round(float(db(level)), 2),
                               None if floor is None else round(float(db(floor)), 2),
                               quality, len(powers)])
        events: list[dict[str, Any]] = [{
            "type": "measurement", "campaign": self.spec["campaign"], "ts_ms": ts,
            "period_ms": int(self.period * 1000 / self.rate), "values": values,
            "channel_base": self.channel_base,
            "wall_ms": None if self.period_first is None or math.isinf(self.epoch)
            else round((self.epoch + self.period_first / self.rate) * 1000, 2),
            "cpu_s": round(time.process_time() - self.cpu_start, 4),
            "busy_s": round(self.busy_s, 4), "audio_s": self.period / self.rate,
            "dropped": self.driver.dropped_samples(),
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
