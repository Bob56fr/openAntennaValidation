"""Simulated SDR with two antenna inputs: synthetic HF IQ so every phase is testable
without hardware.

The simulated "air" holds carriers, SSB voices (a few tones under a syllabic envelope)
and FT8 transmissions (8-FSK, 6.25 Hz spacing, 12.64 s in each 15 s slot), all under a
slow fading common to both antennas. Antenna B differs from A by a fixed gain on the
signals and on the noise. An antenna switch takes effect after a latency, with a glitch
that settles exponentially, like a real front end. A non-linear gain and ADC clipping
are applied at the end.
"""

from __future__ import annotations

import math
import time

import numpy as np
import numpy.typing as npt

from open_antenna_validation.config import SimulatedConfig, SimulatedSignal
from open_antenna_validation.hardware.base import (
    Availability,
    DeviceInfo,
    GainState,
    GainStep,
    HardwareError,
    IQBlock,
    SdrDriver,
    SwitchCommand,
)

Floats = npt.NDArray[np.float64]

_RATES = [62_500, 125_000, 250_000, 500_000, 1_000_000, 2_000_000]
_STEPS = 40
_ANTENNAS = ["A", "B"]

FT8_SLOT_S = 15.0
FT8_START_S = 0.5
FT8_SYMBOL_S = 0.16
FT8_SYMBOLS = 79
FT8_TONE_HZ = 6.25
SSB_TONES_HZ = (420.0, 780.0, 1_150.0, 1_700.0, 2_300.0)

# 0 dBFS at gain step 0 corresponds to this input level.
FULL_SCALE_DBM = -40.0


def simulated_gain_db(index: int) -> float:
    """Nominal 1 dB per step, compressing near the top like a real front end."""
    return index - 0.02 * max(0, index - 30) ** 2


class SimulatedDriver(SdrDriver):
    name = "simulated"

    def __init__(self, config: SimulatedConfig) -> None:
        self._config = config
        self.realtime = config.realtime
        # Each simulated SDR its own noise; the signals are the same "air" for all.
        self._rng = np.random.default_rng(config.seed + sum(map(ord, config.serial)))
        self._epoch = 0.0
        self._open = False
        self._frequency = 7.1e6
        self._rate = 250_000
        self._ppm = 0.0
        self._bias_tee = False
        self._antenna = ""
        self._gain_index = _STEPS // 2
        self._sample = 0  # absolute counter: signals stay continuous across blocks
        self._started = 0.0
        # (effect sample, antenna) of every switch, oldest first; the first one is the start.
        self._switches: list[tuple[int, str]] = [(0, "A")]

    # --- device ----------------------------------------------------------------

    def availability(self) -> Availability:
        return Availability(available=True, library_version="simulé")

    def detect(self) -> list[DeviceInfo]:
        return [DeviceInfo(driver=self.name, model="Simulateur", serial=self._config.serial)]

    def open(self, serial: str | None = None) -> DeviceInfo:
        if serial and serial != self._config.serial:
            raise HardwareError(f"Appareil simulé {serial} introuvable.")
        self._open = True
        self._restart()
        return self.detect()[0]

    def _restart(self) -> None:
        self._sample = 0
        self._started = time.monotonic()
        # Air time = wall clock at the start + samples: several simulated SDRs opened at
        # the same moment see the same fading, voices and FT8 slots at the same instant.
        self._epoch = time.time() if self._config.realtime else 0.0
        self._switches = [(0, self.effective_antenna(self.detect()[0]))]

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def frequency_range(self) -> tuple[float, float]:
        return (1_000.0, 2_000_000_000.0)

    def set_frequency(self, hz: float) -> None:
        self._frequency = hz

    def frequency(self) -> float:
        return self._frequency

    def set_sample_rate(self, sps: int) -> None:
        if sps not in _RATES:
            raise HardwareError(f"Fréquence d'échantillonnage {sps} non supportée.")
        self._rate = sps
        self._restart()

    def sample_rate(self) -> int:
        return self._rate

    def set_ppm(self, ppm: float) -> None:
        self._ppm = ppm

    def set_bias_tee(self, enabled: bool) -> None:
        self._bias_tee = enabled

    def antennas(self, device: DeviceInfo) -> list[str]:
        return list(_ANTENNAS)

    def set_antenna(self, antenna: str) -> SwitchCommand | None:
        self.check_antenna(self.detect()[0], antenna)
        self._antenna = antenna
        if not self._open:
            return None
        effective = self.effective_antenna(self.detect()[0])
        latency = round(self._config.switch_latency_ms * 1e-3 * self._rate)
        self._switches.append((self._sample + latency, effective))
        # Keep only what may still matter for the next blocks.
        self._switches = self._switches[-8:]
        return SwitchCommand(
            antenna=effective, sample=self._sample, monotonic_ns=time.monotonic_ns()
        )

    def _chosen_antenna(self) -> str:
        return self._antenna

    def gain_steps(self) -> list[GainStep]:
        return [
            GainStep(index=i, label=f"{simulated_gain_db(i):.1f} dB", native={"gain_db": i})
            for i in range(_STEPS)
        ]

    def set_gain_step(self, index: int) -> None:
        self._check_gain_index(index)
        self._gain_index = index

    def get_gain_state(self) -> GainState:
        step = self.gain_steps()[self._gain_index]
        return GainState(step_index=step.index, native=step.native)

    def gain_mode(self) -> str:
        return "gain"

    def sample_counter(self) -> int:
        return self._sample

    def supported_sample_rates(self) -> list[int]:
        return list(_RATES)

    def dbm_offset_db(self, frequency_hz: float) -> float | None:
        return FULL_SCALE_DBM - simulated_gain_db(self._gain_index)

    # --- synthetic air -----------------------------------------------------------

    def _carrier(self, s: SimulatedSignal, off: float, t: Floats) -> npt.NDArray[np.complex128]:
        return np.exp(2j * math.pi * off * t)

    def _ssb(self, s: SimulatedSignal, off: float, t: Floats) -> npt.NDArray[np.complex128]:
        # Syllables at about 4 Hz, pauses between words: the envelope a speech signal has.
        syllable = np.abs(np.sin(2 * math.pi * 2.1 * t + 0.7 * np.sin(2 * math.pi * 0.37 * t)))
        words = (np.sin(2 * math.pi * 0.23 * t + s.offset_hz) > -0.4).astype(np.float64)
        env = syllable * words
        out = np.zeros(len(t), dtype=np.complex128)
        for k, tone in enumerate(SSB_TONES_HZ):
            phase = 2 * math.pi * (off + tone) * t + k * 1.3
            out += np.exp(1j * phase) / len(SSB_TONES_HZ)
        return env * out * 2.0

    def _ft8(self, s: SimulatedSignal, off: float, t: Floats) -> npt.NDArray[np.complex128]:
        slot = np.floor(t / FT8_SLOT_S).astype(np.int64)
        local = t - slot * FT8_SLOT_S - FT8_START_S
        on = (local >= 0) & (local < FT8_SYMBOLS * FT8_SYMBOL_S) & (slot % 2 == 0)
        symbol = np.clip((local / FT8_SYMBOL_S).astype(np.int64), 0, FT8_SYMBOLS - 1)
        # Pseudo-random tone sequence, reproducible per slot and symbol.
        tone = (slot * 7919 + symbol * 104_729 + int(s.offset_hz)) % 8
        phase = 2 * math.pi * (off + tone * FT8_TONE_HZ) * t
        wave: npt.NDArray[np.complex128] = on * np.exp(1j * phase)
        return wave

    def _antenna_track(self, first: int, n: int) -> tuple[npt.NDArray[np.bool_], Floats]:
        """Per sample: True on antenna B, and the glitch in dB after each switch."""
        idx = first + np.arange(n)
        on_b = np.zeros(n, dtype=bool)
        glitch = np.zeros(n)
        tau = max(1e-6, self._config.switch_settle_ms * 1e-3 / 4) * self._rate
        for k, (at, ant) in enumerate(self._switches):
            end = self._switches[k + 1][0] if k + 1 < len(self._switches) else None
            mask = idx >= at if end is None else (idx >= at) & (idx < end)
            on_b[mask] = ant == "B"
            if k > 0:
                since = idx - at
                live = since >= 0
                glitch[live] += self._config.switch_glitch_db * np.exp(-since[live] / tau)
        return on_b, glitch

    def read_iq(self, n: int, timeout_s: float = 2.0) -> IQBlock:
        if not self._open:
            raise HardwareError("Appareil simulé non ouvert.")
        first = self._sample
        t = self._epoch + (first + np.arange(n)) / self._rate
        self._sample += n
        cfg = self._config
        on_b, glitch = self._antenna_track(first, n)

        fading_db = cfg.fading_db / 2 * np.sin(2 * math.pi * t / cfg.fading_period_s)
        air = np.zeros(n, dtype=np.complex128)
        shift = cfg.air_center_hz - self._frequency
        for s in cfg.signals:
            off = s.offset_hz + shift  # where the signal falls relative to the tuning
            if abs(off) >= self._rate / 2:
                continue
            wave = {"carrier": self._carrier, "ssb": self._ssb, "ft8": self._ft8}[s.kind](
                s, off, t)
            air += 10 ** (s.dbfs / 20) * wave
        air *= 10 ** ((fading_db + cfg.signal_offset_db
                       + np.where(on_b, cfg.antenna_b_signal_db, 0.0)) / 20)

        noise_db = (cfg.noise_dbfs + cfg.noise_offset_db
                    + np.where(on_b, cfg.antenna_b_noise_db, 0.0))
        noise_amp = 10 ** (noise_db / 20) / math.sqrt(2)
        air += noise_amp * (self._rng.standard_normal(n) + 1j * self._rng.standard_normal(n))

        gain = simulated_gain_db(self._gain_index) + cfg.gain_error_db
        signal = 10 ** ((gain + glitch) / 20) * air
        out: IQBlock = np.empty(n, dtype=np.complex64)
        # ADC clipping on each rail, as a real converter does.
        out.real = np.clip(signal.real, -1.0, 1.0)
        out.imag = np.clip(signal.imag, -1.0, 1.0)
        if cfg.realtime:
            delay = self._started + self._sample / self._rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return out
