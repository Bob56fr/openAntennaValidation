"""RTL-SDR Blog V4 driver (librtlsdr, RTL-SDR Blog branch), for the simultaneous mode.

HF on the V4: below 28.8 MHz the library switches to the HF input and upconverts by
28.8 MHz on its own (tuner_r82xx.c); at and above, the VHF path is used, with another
gain. The tunable range is therefore kept below 28.8 MHz so that a campaign never
straddles the two paths. Other RTL-SDR dongles only receive HF by direct sampling, with
no gain control: they are listed but refused (decision D19).

Gain model: manual tuner gain, steps read from the library (R828D table, tenths of dB),
RTL2832 digital AGC off. Samples are unsigned 8 bit I/Q, mapped to full scale 1.0.
librtlsdr gives no count of lost samples: only buffer overruns on our side are counted.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from typing import Any

import numpy as np

from open_antenna_validation.config import RtlsdrConfig
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
from open_antenna_validation.hardware.rtlsdr import api
from open_antenna_validation.hardware.stream import SampleRing

log = logging.getLogger(__name__)

# Rates without sample loss (rtl-sdr.h: 225001-300000 and 900001-3200000 Hz, loss expected
# above 2.4 MS/s); the usual values of SDR software.
SAMPLE_RATES = (250_000, 1_024_000, 1_400_000, 1_800_000, 1_920_000, 2_048_000, 2_400_000)
HF_PATH_LIMIT_HZ = 28_800_000
FREQUENCY_RANGE_HZ = (500_000.0, float(HF_PATH_LIMIT_HZ - 1))
DEFAULT_FREQUENCY_HZ = 7.1e6
# R820T/R828D gain table of librtlsdr.c (tenths of dB); replaced by the library's at open.
R82XX_GAINS = (0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254, 280, 297,
               328, 338, 364, 372, 386, 402, 421, 434, 439, 445, 480, 496)
# USB transfer size: multiple of 16384 bytes (rtl-sdr.h), about 30 ms or more per block.
TRANSFER_BYTES = 16384
BLOCK_SECONDS = 0.03
V4_MODEL = "RTL-SDR Blog V4"


def model_name(manufacturer: str, product: str) -> str:
    if (manufacturer, product) == (api.V4_MANUFACTURER, api.V4_PRODUCT):
        return V4_MODEL
    return f"RTL-SDR ({product or 'modèle inconnu'})"


def transfer_bytes(rate: int) -> int:
    wanted = int(rate * BLOCK_SECONDS) * 2
    return max(TRANSFER_BYTES, -(-wanted // TRANSFER_BYTES) * TRANSFER_BYTES)


def to_complex(raw: np.ndarray[Any, np.dtype[np.uint8]]) -> IQBlock:
    """Unsigned 8 bit interleaved I/Q to complex64; codes 0 and 255 give -1.0 and +1.0."""
    n = len(raw) // 2
    pairs = raw[: 2 * n].astype(np.float32)
    pairs -= np.float32(127.5)
    pairs *= np.float32(1 / 127.5)
    block: IQBlock = pairs.view(np.complex64).copy()
    return block


class RtlsdrDriver(SdrDriver):
    name = "rtlsdr"

    def __init__(self, config: RtlsdrConfig, sample_rate: int = 250_000,
                 library: Any = None) -> None:
        self._config = config
        self._lib: Any = library
        self._lock = threading.RLock()
        self._dev: ctypes.c_void_p | None = None
        self._info_open: DeviceInfo | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._callback: Any = None

        self._frequency = DEFAULT_FREQUENCY_HZ
        self._rate = sample_rate if sample_rate in SAMPLE_RATES else SAMPLE_RATES[0]
        self._ppm = 0
        self._bias_tee = False
        self._antenna = ""
        self._gains: list[int] = list(R82XX_GAINS)
        self._gain_index = len(self._gains) // 2
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
        branch = ("branche RTL-SDR Blog" if api.is_blog_branch(lib)
                  else "branche osmocom : vérifiez qu'elle gère la V4")
        return Availability(True, library_version=f"librtlsdr ({branch})")

    def _list(self) -> list[tuple[int, DeviceInfo]]:
        lib = self._ensure_lib()
        found = []
        for index in range(int(lib.rtlsdr_get_device_count())):
            m, p, s = (ctypes.create_string_buffer(api.USB_STRING_SIZE) for _ in range(3))
            if lib.rtlsdr_get_device_usb_strings(index, m, p, s) != 0:
                continue  # in use by another program: its strings cannot be read
            manufacturer, product, serial = (b.value.decode(errors="replace") for b in (m, p, s))
            found.append((index, DeviceInfo(
                driver=self.name, model=model_name(manufacturer, product), serial=serial,
                details={"manufacturer": manufacturer, "product": product})))
        serials = [info.serial for _, info in found]
        return [(i, info if serials.count(info.serial) == 1 else DeviceInfo(
                    info.driver, info.model, info.serial,
                    {**info.details, "warning": "numéro de série en double : programmez un "
                     "numéro unique sur chaque clé avec rtl_eeprom -s"}))
                for i, info in found]

    def detect(self) -> list[DeviceInfo]:
        with self._lock:
            found = [info for _, info in self._list()]
            current = self._info_open
            if current is not None and all(d.serial != current.serial for d in found):
                found.insert(0, current)
            return found

    def open(self, serial: str | None = None) -> DeviceInfo:
        with self._lock:
            if self._dev is not None:
                raise HardwareError("Une RTL-SDR est déjà ouverte.")
            devices = self._list()
            if not devices:
                raise HardwareError("Aucune RTL-SDR détectée (débranchée, utilisée par un autre "
                                    "logiciel, ou pilote WinUSB absent : voir Zadig ?).")
            if serial:
                devices = [(i, d) for i, d in devices if d.serial == serial]
                if not devices:
                    raise HardwareError(f"RTL-SDR n° {serial} introuvable.")
                if len(devices) > 1:
                    raise HardwareError(
                        f"{len(devices)} RTL-SDR portent le numéro {serial} : programmez un "
                        "numéro unique sur chaque clé (rtl_eeprom -s), puis rebranchez-les.")
            index, info = devices[0]
            if info.model != V4_MODEL:
                raise HardwareError(
                    f"{info.model} n° {info.serial} : seule la RTL-SDR Blog V4 est prise en "
                    "charge. Les autres clés ne reçoivent les ondes courtes qu'en "
                    "échantillonnage direct, sans réglage de gain.")
            self.check_antenna(info, self._antenna)
            lib = self._ensure_lib()
            dev = ctypes.c_void_p()
            if lib.rtlsdr_open(ctypes.byref(dev), index) != 0 or not dev.value:
                raise HardwareError(f"Impossible d'ouvrir la RTL-SDR n° {info.serial} "
                                    "(utilisée par un autre logiciel ?).")
            self._dev = dev
            try:
                self._configure()
                self._start()
            except BaseException:
                self._release()
                raise
            self._info_open = info
            return info

    def _check(self, result: int, what: str) -> None:
        if result != 0:
            raise HardwareError(f"RTL-SDR : échec de {what} (code {result}).")

    def _configure(self) -> None:
        lib, dev = self._lib, self._dev
        count = int(lib.rtlsdr_get_tuner_gains(dev, None))
        if count > 0:
            table = (ctypes.c_int * count)()
            if lib.rtlsdr_get_tuner_gains(dev, table) == count:
                gains = sorted(int(g) for g in table)
                if gains != self._gains:
                    self._gain_index = min(self._gain_index, len(gains) - 1)
                    self._gains = gains
        self._check(lib.rtlsdr_set_sample_rate(dev, self._rate), "la fréquence d'échantillonnage")
        if hasattr(lib, "rtlsdr_set_tuner_bandwidth"):
            lib.rtlsdr_set_tuner_bandwidth(dev, 0)  # automatic, from the sample rate
        self._check(lib.rtlsdr_set_tuner_gain_mode(dev, 1), "le gain manuel")
        self._check(lib.rtlsdr_set_agc_mode(dev, 0), "l'arrêt de l'AGC numérique")
        self._apply_gain()
        self._apply_ppm()
        self._apply_bias_tee()
        self._check(lib.rtlsdr_set_center_freq(dev, round(self._frequency)), "l'accord")

    def close(self) -> None:
        with self._lock:
            self._release()

    def _release(self) -> None:
        self._stop()
        if self._dev is not None and self._lib is not None:
            self._lib.rtlsdr_close(self._dev)
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
            raise HardwareError(
                f"Fréquence d'accord {hz / 1e6:.3f} MHz hors de la plage HF de la RTL-SDR V4 "
                f"({low / 1e6:.1f} à 28,8 MHz exclus : au-delà, la clé change d'entrée).")
        with self._lock:
            self._frequency = float(hz)
            if self._dev is not None:
                self._check(self._lib.rtlsdr_set_center_freq(self._dev, round(hz)),
                            "l'accord")

    def frequency(self) -> float:
        return self._frequency

    def set_sample_rate(self, sps: int) -> None:
        if sps not in SAMPLE_RATES:
            raise HardwareError(f"Fréquence d'échantillonnage {sps} non prise en charge par la "
                                "RTL-SDR.")
        with self._lock:
            self._rate = sps
            if self._dev is not None:
                self._stop()
                self._check(self._lib.rtlsdr_set_sample_rate(self._dev, sps),
                            "la fréquence d'échantillonnage")
                self._start()

    def sample_rate(self) -> int:
        return self._rate

    def supported_sample_rates(self) -> list[int]:
        return list(SAMPLE_RATES)

    def set_ppm(self, ppm: float) -> None:
        with self._lock:
            self._ppm = round(ppm)
            if self._dev is not None:
                self._apply_ppm()

    def _apply_ppm(self) -> None:
        result = self._lib.rtlsdr_set_freq_correction(self._dev, self._ppm)
        if result not in (0, api.CORRECTION_UNCHANGED):
            self._check(result, "la correction de fréquence")

    def set_bias_tee(self, enabled: bool) -> None:
        with self._lock:
            self._bias_tee = enabled
            if self._dev is not None:
                self._apply_bias_tee()

    def _apply_bias_tee(self) -> None:
        if hasattr(self._lib, "rtlsdr_set_bias_tee"):
            self._lib.rtlsdr_set_bias_tee(self._dev, int(self._bias_tee))
        elif self._bias_tee:
            raise HardwareError("Cette version de librtlsdr ne commande pas l'alimentation "
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

    def gain_steps(self) -> list[GainStep]:
        return [GainStep(index=i, label=f"Gain {g / 10:.1f} dB", native={"gain_tenth_db": g})
                for i, g in enumerate(self._gains)]

    def set_gain_step(self, index: int) -> None:
        self._check_gain_index(index)
        with self._lock:
            self._gain_index = index
            if self._dev is not None:
                self._apply_gain()

    def _apply_gain(self) -> None:
        self._check(self._lib.rtlsdr_set_tuner_gain(self._dev, self._gains[self._gain_index]),
                    "le réglage du gain")

    def get_gain_state(self) -> GainState:
        step = self.gain_steps()[self._gain_index]
        return GainState(step_index=step.index, native=step.native)

    def gain_mode(self) -> str:
        return "manual"

    # --- streaming -------------------------------------------------------------------

    def _start(self) -> None:
        self._ring.reset()
        self._check(self._lib.rtlsdr_reset_buffer(self._dev), "la remise à zéro du flux")
        self._stopping = False
        # Keep a reference: the library calls it from the reading thread.
        self._callback = api.ReadAsyncCallback(self._on_samples)
        self._thread = threading.Thread(target=self._run, args=(transfer_bytes(self._rate),),
                                        name="rtlsdr-read", daemon=True)
        self._thread.start()

    def _run(self, chunk: int) -> None:
        result = self._lib.rtlsdr_read_async(self._dev, self._callback, None, 0, chunk)
        if not self._stopping:
            log.error("Flux RTL-SDR interrompu (code %s)", result)
            self._ring.fail("Flux de la RTL-SDR interrompu (clé débranchée ?).")

    def _on_samples(self, buf: Any, length: int, ctx: Any) -> None:
        if length < 2 or self._stopping:
            return
        raw = np.ctypeslib.as_array(buf, shape=(int(length),))
        self._ring.push(to_complex(raw), self._rate)

    def _stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stopping = True
        self._lib.rtlsdr_cancel_async(self._dev)
        thread.join(timeout=5.0)
        self._thread = None
        if thread.is_alive():
            # The library may still call back: keep the callback alive.
            log.error("Le fil de lecture RTL-SDR ne s'est pas arrêté")
        else:
            self._callback = None

    def sample_counter(self) -> int:
        return self._ring.counter()

    def read_iq(self, n: int, timeout_s: float = 2.0) -> IQBlock:
        if self._thread is None:
            raise HardwareError("RTL-SDR non ouverte.")
        return self._ring.read(n, timeout_s, "Pas d'échantillons de la RTL-SDR (délai dépassé).")

    def dropped_samples(self) -> int:
        return self._ring.dropped
