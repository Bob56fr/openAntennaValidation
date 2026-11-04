"""Airspy HF+ driver (libairspyhf), for the simultaneous mode.

Gain model: the preamplifier (LNA) is fixed for a measurement series (configuration) and
the sweepable steps are the attenuator steps read from the device (0 to 48 dB by 6 dB on
the HF+ Discovery), lowest gain first. The receiver AGC is off.

Sample rates come from the device (they depend on the firmware): the rate asked before
opening is resolved at open time to the lowest device rate at or above it. Samples arrive
as complex floats scaled by the library; lost USB buffers are reported by the library
(``dropped_samples`` of each transfer) and keep the sample index in step.

The API has no board id getter: the model is reported as "Airspy HF+".
"""

from __future__ import annotations

import ctypes
import logging
import threading
from typing import Any

import numpy as np

from open_antenna_validation.config import AirspyhfConfig
from open_antenna_validation.hardware.airspyhf import api
from open_antenna_validation.hardware.base import (
    Availability,
    DeviceInfo,
    DriverUnavailable,
    GainState,
    GainStep,
    HardwareError,
    IQBlock,
    SdrDriver,
    SwitchCommand,
)
from open_antenna_validation.hardware.stream import SampleRing

log = logging.getLogger(__name__)

MODEL = "Airspy HF+"
MAX_DEVICES = 16
# HF range of the HF+ Discovery (manufacturer data sheet; the VHF range is not used).
FREQUENCY_RANGE_HZ = (500.0, 31_000_000.0)
DEFAULT_FREQUENCY_HZ = 7.1e6


def serial_text(serial: int) -> str:
    return f"{serial:016X}"


class AirspyhfDriver(SdrDriver):
    name = "airspyhf"

    def __init__(self, config: AirspyhfConfig, sample_rate: int = 250_000,
                 library: Any = None) -> None:
        self._config = config
        self._lib: Any = library
        self._lock = threading.RLock()
        self._dev: ctypes.c_void_p | None = None
        self._info_open: DeviceInfo | None = None
        self._streaming = False
        self._callback: Any = None

        self._frequency = DEFAULT_FREQUENCY_HZ
        self._wanted_rate = sample_rate
        self._rate = sample_rate
        self._rates: list[int] = []  # known once a device is open
        self._ppm = 0.0
        self._bias_tee = False
        self._antenna = ""
        self._att_steps: list[float] = list(api.LEGACY_ATT_STEPS_DB)
        self._gain_index = len(self._att_steps) - 1  # no attenuation
        self._ring = SampleRing()

    # --- library ---------------------------------------------------------------------

    def _ensure_lib(self) -> Any:
        with self._lock:
            if self._lib is None:
                self._lib = api.load_library(self._config.library_path)
            return self._lib

    def availability(self) -> Availability:
        try:
            lib = self._ensure_lib()
        except DriverUnavailable as exc:
            return Availability(False, str(exc), exc.help_url)
        return Availability(True, library_version=f"libairspyhf {api.library_version(lib)}")

    def _serials(self) -> list[int]:
        lib = self._ensure_lib()
        serials = (ctypes.c_uint64 * MAX_DEVICES)()
        count = int(lib.airspyhf_list_devices(serials, MAX_DEVICES))
        return [int(serials[i]) for i in range(max(0, min(count, MAX_DEVICES)))]

    def _info(self, serial: int, firmware: str = "") -> DeviceInfo:
        details = {"firmware": firmware} if firmware else {}
        return DeviceInfo(driver=self.name, model=MODEL, serial=serial_text(serial),
                          details=details)

    def detect(self) -> list[DeviceInfo]:
        with self._lock:
            found = [self._info(s) for s in self._serials()]
            current = self._info_open
            if current is not None:
                found = [current if d.serial == current.serial else d for d in found]
                if all(d.serial != current.serial for d in found):
                    found.insert(0, current)
            return found

    def open(self, serial: str | None = None) -> DeviceInfo:
        with self._lock:
            if self._dev is not None:
                raise HardwareError("Un Airspy HF+ est déjà ouvert.")
            serials = self._serials()
            if not serials:
                raise HardwareError("Aucun Airspy HF+ détecté (débranché, ou utilisé par un "
                                    "autre logiciel ?).")
            chosen = serials[0]
            if serial:
                matches = [s for s in serials if serial_text(s) == serial.upper().zfill(16)]
                if not matches:
                    raise HardwareError(f"Airspy HF+ n° {serial} introuvable.")
                chosen = matches[0]
            self.check_antenna(self._info(chosen), self._antenna)
            lib = self._ensure_lib()
            dev = ctypes.c_void_p()
            if lib.airspyhf_open_sn(ctypes.byref(dev), chosen) != api.SUCCESS or not dev.value:
                raise HardwareError(f"Impossible d'ouvrir l'Airspy HF+ n° {serial_text(chosen)} "
                                    "(utilisé par un autre logiciel ?).")
            self._dev = dev
            try:
                info = self._info(chosen, self._firmware())
                self._configure()
                self._start()
            except BaseException:
                self._release()
                raise
            self._info_open = info
            return info

    def _firmware(self) -> str:
        if not hasattr(self._lib, "airspyhf_version_string_read"):
            return ""
        text = ctypes.create_string_buffer(api.VERSION_STRING_SIZE)
        if self._lib.airspyhf_version_string_read(self._dev, text, api.VERSION_STRING_SIZE) != 0:
            return ""
        return str(text.value.decode(errors="replace"))

    def _check(self, result: int, what: str) -> None:
        if result != api.SUCCESS:
            raise HardwareError(f"Airspy HF+ : échec de {what} (code {result}).")

    def _read_rates(self) -> list[int]:
        count = ctypes.c_uint32(0)
        self._check(self._lib.airspyhf_get_samplerates(self._dev, ctypes.byref(count), 0),
                    "la lecture des fréquences d'échantillonnage")
        table = (ctypes.c_uint32 * int(count.value))()
        if count.value:
            self._check(self._lib.airspyhf_get_samplerates(self._dev, table, count.value),
                        "la lecture des fréquences d'échantillonnage")
        return sorted(int(r) for r in table)

    def _read_att_steps(self) -> list[float]:
        if not hasattr(self._lib, "airspyhf_get_att_steps"):
            return list(api.LEGACY_ATT_STEPS_DB)
        count = ctypes.c_uint32(0)
        if self._lib.airspyhf_get_att_steps(self._dev, ctypes.byref(count), 0) != api.SUCCESS \
                or not count.value:
            return list(api.LEGACY_ATT_STEPS_DB)
        table = (ctypes.c_float * int(count.value))()
        if self._lib.airspyhf_get_att_steps(self._dev, table, count.value) != api.SUCCESS:
            return list(api.LEGACY_ATT_STEPS_DB)
        return sorted(float(a) for a in table)

    def _configure(self) -> None:
        lib, dev = self._lib, self._dev
        self._rates = self._read_rates()
        if not self._rates:
            raise HardwareError("Airspy HF+ : aucune fréquence d'échantillonnage annoncée.")
        self._rate = self.pick_sample_rate(self._wanted_rate)
        self._check(lib.airspyhf_set_samplerate(dev, self._rate), "la fréquence d'échantillonnage")
        steps = self._read_att_steps()
        if steps != self._att_steps:
            self._att_steps = steps
            self._gain_index = min(self._gain_index, len(steps) - 1)
        self._check(lib.airspyhf_set_hf_agc(dev, 0), "l'arrêt de l'AGC")
        self._check(lib.airspyhf_set_hf_lna(dev, int(self._config.lna)), "le réglage du préampli")
        self._apply_gain()
        self._apply_ppm()
        self._apply_bias_tee()
        self._check(lib.airspyhf_set_freq(dev, round(self._frequency)), "l'accord")

    def close(self) -> None:
        with self._lock:
            self._release()

    def _release(self) -> None:
        self._stop()
        if self._dev is not None and self._lib is not None:
            self._lib.airspyhf_close(self._dev)
        self._dev = None
        self._info_open = None
        self._ring.close()

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    # --- tuning ----------------------------------------------------------------------

    def frequency_range(self) -> tuple[float, float]:
        return FREQUENCY_RANGE_HZ

    def set_frequency(self, hz: float) -> None:
        low, high = FREQUENCY_RANGE_HZ
        if not low <= hz <= high:
            raise HardwareError(f"Fréquence {hz:.0f} Hz hors de la plage HF de l'Airspy HF+.")
        with self._lock:
            self._frequency = float(hz)
            if self._dev is not None:
                self._check(self._lib.airspyhf_set_freq(self._dev, round(hz)), "l'accord")

    def frequency(self) -> float:
        return self._frequency

    def set_sample_rate(self, sps: int) -> None:
        with self._lock:
            if self._dev is None:
                # Resolved against the device's own list when it is opened.
                self._wanted_rate = self._rate = sps
                return
            if sps not in self._rates:
                raise HardwareError(f"Fréquence d'échantillonnage {sps} non prise en charge par "
                                    "cet Airspy HF+.")
            self._wanted_rate = self._rate = sps
            self._stop()
            self._check(self._lib.airspyhf_set_samplerate(self._dev, sps),
                        "la fréquence d'échantillonnage")
            self._start()

    def sample_rate(self) -> int:
        return self._rate

    def supported_sample_rates(self) -> list[int]:
        return list(self._rates)

    def set_ppm(self, ppm: float) -> None:
        with self._lock:
            self._ppm = float(ppm)
            if self._dev is not None:
                self._apply_ppm()

    def _apply_ppm(self) -> None:
        # Calibration in parts per billion; the flash copy is left untouched.
        self._check(self._lib.airspyhf_set_calibration(self._dev, round(self._ppm * 1000)),
                    "la correction de fréquence")

    def set_bias_tee(self, enabled: bool) -> None:
        with self._lock:
            self._bias_tee = enabled
            if self._dev is not None:
                self._apply_bias_tee()

    def _apply_bias_tee(self) -> None:
        if hasattr(self._lib, "airspyhf_set_bias_tee"):
            result = self._lib.airspyhf_set_bias_tee(self._dev, int(self._bias_tee))
            if self._bias_tee:
                self._check(result, "l'alimentation d'antenne")
        elif self._bias_tee:
            raise HardwareError("Cette version de libairspyhf ne commande pas l'alimentation "
                                "d'antenne (bias-tee).")

    # --- inputs (one only) -------------------------------------------------------------

    def antennas(self, device: DeviceInfo) -> list[str]:
        return []

    def set_antenna(self, antenna: str) -> SwitchCommand | None:
        self._antenna = antenna
        if antenna and self._info_open is not None:
            self.check_antenna(self._info_open, antenna)
        return None

    def _chosen_antenna(self) -> str:
        return self._antenna

    # --- gain ------------------------------------------------------------------------

    def _att(self, index: int) -> float:
        return self._att_steps[len(self._att_steps) - 1 - index]

    def gain_steps(self) -> list[GainStep]:
        lna = int(self._config.lna)
        return [GainStep(index=i, label=f"LNA {lna}, atténuation {self._att(i):g} dB",
                         native={"lna": lna, "att_db": self._att(i)})
                for i in range(len(self._att_steps))]

    def set_gain_step(self, index: int) -> None:
        self._check_gain_index(index)
        with self._lock:
            self._gain_index = index
            if self._dev is not None:
                self._apply_gain()

    def _apply_gain(self) -> None:
        att = self._att(self._gain_index)
        if hasattr(self._lib, "airspyhf_set_att"):
            self._check(self._lib.airspyhf_set_att(self._dev, att), "le réglage de l'atténuateur")
        else:
            index = api.LEGACY_ATT_STEPS_DB.index(att) if att in api.LEGACY_ATT_STEPS_DB else 0
            self._check(self._lib.airspyhf_set_hf_att(self._dev, index),
                        "le réglage de l'atténuateur")

    def get_gain_state(self) -> GainState:
        step = self.gain_steps()[self._gain_index]
        return GainState(step_index=step.index, native=step.native)

    def gain_mode(self) -> str:
        return f"lna{int(self._config.lna)}-att"

    # --- streaming -------------------------------------------------------------------

    def _start(self) -> None:
        self._ring.reset()
        # Keep a reference: the library calls it from its own thread.
        self._callback = api.SampleCallback(self._on_transfer)
        self._check(self._lib.airspyhf_start(self._dev, self._callback, None), "la mise en route")
        self._streaming = True

    def _on_transfer(self, transfer: Any) -> int:
        try:
            t = transfer.contents
            count = int(t.sample_count)
            if count > 0 and self._streaming:
                floats = np.ctypeslib.as_array(t.samples, shape=(2 * count,))
                block: IQBlock = floats.view(np.complex64).copy()
                self._ring.push(block, self._rate, gap=int(t.dropped_samples))
        except Exception:  # never let an exception cross into the library thread
            log.exception("Bloc Airspy HF+ ignoré")
        return 0

    def _stop(self) -> None:
        if self._streaming and self._dev is not None:
            self._streaming = False
            self._lib.airspyhf_stop(self._dev)
        self._streaming = False

    def sample_counter(self) -> int:
        return self._ring.counter()

    def read_iq(self, n: int, timeout_s: float = 2.0) -> IQBlock:
        if not self._streaming:
            raise HardwareError("Airspy HF+ non ouvert.")
        return self._ring.read(n, timeout_s, "Pas d'échantillons de l'Airspy HF+ (délai dépassé).")

    def dropped_samples(self) -> int:
        return self._ring.dropped
