"""SDRplay driver for HF (RSP2/RSP2pro reference; RSP1A, RSP1B, RSPdx, RSPduo single tuner).

Gain model: the LNA state is fixed for a measurement series (configuration) and
the sweepable steps are the IF gain reductions, GRdB 59 (lowest gain) to 20
(highest). AGC is always disabled. The ADC runs at 2 MS/s in zero-IF mode and
the hardware decimates down to the requested rate.

Every block received from the API is tagged with its absolute sample index, so that
an antenna switch command can be placed in the stream and gaps can be counted
(``firstSampleNum`` of the stream callback).
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any

import numpy as np

from open_antenna_validation.config import SdrplayConfig
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
from open_antenna_validation.hardware.sdrplay import api

log = logging.getLogger(__name__)

ADC_RATE = 2_000_000
DECIMATIONS = (32, 16, 8, 4, 2, 1)
GRDB_MIN = 20
GRDB_MAX = 59
DEFAULT_FREQUENCY_HZ = 7.1e6
FREQUENCY_RANGE_HZ = (1_000.0, 2_000_000_000.0)
# Ring buffer depth, in seconds of delivered samples.
BUFFER_SECONDS = 2.0
_UINT32 = 1 << 32

MODEL_NAMES = {
    api.HW_RSP1: "RSP1",
    api.HW_RSP1A: "RSP1A",
    api.HW_RSP2: "RSP2/RSP2pro",
    api.HW_RSPDUO: "RSPduo",
    api.HW_RSPDX: "RSPdx",
    api.HW_RSP1B: "RSP1B",
    api.HW_RSPDXR2: "RSPdx-R2",
}

# Number of LNA states below 60 MHz on the 50 ohm inputs, from the API 3.15 headers:
# RSPII_NUM_LNA_STATES (RSP2, same count from 0 to 420 MHz), RSPIA_NUM_LNA_STATES_AM,
# RSPDUO_NUM_LNA_STATES_AM, RSPDX_NUM_LNA_STATES. RSP1: API specification.
LNA_STATES_HF = {
    api.HW_RSP1: 4,
    api.HW_RSP1A: 7,
    api.HW_RSP1B: 7,
    api.HW_RSP2: 9,
    api.HW_RSPDUO: 7,
    api.HW_RSPDX: 28,
    api.HW_RSPDXR2: 28,
}

# Selectable 50 ohm antenna inputs, default first (RSP2 Hi-Z input not used: other RF path).
ANTENNAS: dict[int, tuple[str, ...]] = {
    api.HW_RSP2: ("A", "B"),
    api.HW_RSPDX: ("A", "B", "C"),
    api.HW_RSPDXR2: ("A", "B", "C"),
}

WINDOWS_SERVICE = "SDRplayAPIService"
# Unit names used by the Linux API installers (to be confirmed on Debian / RaspOS).
LINUX_SERVICES = ("sdrplay", "sdrplay_api", "sdrplayService")


def model_name(hw_ver: int) -> str:
    return MODEL_NAMES.get(hw_ver, f"RSP inconnu ({hw_ver})")


def bandwidth_for(rate: int) -> int:
    """Widest IF filter (kHz) that still fits in the delivered band."""
    for bw_khz in (api.BW_1_536, api.BW_0_600, api.BW_0_300):
        if rate >= bw_khz * 1000:
            return bw_khz
    return api.BW_0_200


def service_running() -> bool | None:
    """State of the API service; None when it cannot be determined."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["sc", "query", WINDOWS_SERVICE], capture_output=True, text=True, timeout=5
            )
            if out.returncode != 0:
                return False
            return "RUNNING" in out.stdout
        if shutil.which("systemctl") is None:
            return None
        for unit in LINUX_SERVICES:
            out = subprocess.run(
                ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5
            )
            if out.stdout.strip() == "active":
                return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        return None


class SdrplayDriver(SdrDriver):
    name = "sdrplay"

    def __init__(self, config: SdrplayConfig, sample_rate: int = 250_000) -> None:
        self._config = config
        self._lib: ctypes.CDLL | None = None
        self._api_open = False
        self._api_version: float | None = None
        self._lock = threading.RLock()

        self._device: api.DeviceT | None = None
        self._params: Any = None  # ctypes.POINTER(api.DeviceParamsT)
        self._streaming = False
        self._callbacks: api.CallbackFnsT | None = None

        self._frequency = DEFAULT_FREQUENCY_HZ
        self._rate = sample_rate
        self._ppm = 0.0
        self._bias_tee = False
        self._antenna = ""
        self._gain_index = (GRDB_MAX - GRDB_MIN) // 2
        self._overload = False

        # Buffered blocks with the absolute index of their first sample.
        self._buffer: deque[tuple[int, IQBlock]] = deque()
        self._buffered = 0
        self._next_index = 0  # absolute index of the next sample the API will deliver
        self._last_first: int | None = None  # firstSampleNum expected next (uint32)
        self._dropped = 0
        self._data_ready = threading.Condition(threading.Lock())

    # --- library -----------------------------------------------------------------

    def _ensure_api(self) -> ctypes.CDLL:
        with self._lock:
            if self._lib is None:
                self._lib = api.load_library(self._config.library_path)
            if not self._api_open:
                err = self._lib.sdrplay_api_Open()
                if err != api.SUCCESS:
                    running = service_running()
                    if err == api.ERR_SERVICE_NOT_RESPONDING or running is False:
                        raise DriverUnavailable(
                            "Le service de l'API SDRplay ne répond pas. Vérifiez qu'il est "
                            "installé et démarré, puis relancez la station.",
                            help_url=api.DOWNLOAD_URL,
                        )
                    raise HardwareError(f"Ouverture de l'API SDRplay : {self._error(err)}")
                self._api_open = True
                version = ctypes.c_float()
                if self._lib.sdrplay_api_ApiVersion(ctypes.byref(version)) == api.SUCCESS:
                    self._api_version = round(version.value, 2)
                self._check_version()
            return self._lib

    def _check_version(self) -> None:
        v = self._api_version
        if v is None or not 3.0 <= v < 4.0:
            self._close_api()
            raise DriverUnavailable(
                f"Version de l'API SDRplay non supportée ({v}). La version 3.x est requise.",
                help_url=api.DOWNLOAD_URL,
            )
        if abs(v - api.HEADER_VERSION) > 0.001:
            log.warning(
                "API SDRplay %.2f, liaison écrite pour %.2f : à valider sur ce matériel",
                v,
                api.HEADER_VERSION,
            )

    def _close_api(self) -> None:
        if self._lib is not None and self._api_open:
            self._lib.sdrplay_api_Close()
        self._api_open = False

    def _error(self, err: int) -> str:
        if self._lib is None:
            return str(err)
        text = self._lib.sdrplay_api_GetErrorString(err)
        return f"{text.decode(errors='replace') if text else 'erreur'} ({err})"

    def _call(self, func_name: str, *args: object) -> None:
        lib = self._ensure_api()
        err = getattr(lib, func_name)(*args)
        if err != api.SUCCESS:
            raise HardwareError(f"{func_name} : {self._error(err)}")

    def availability(self) -> Availability:
        try:
            self._ensure_api()
        except DriverUnavailable as exc:
            return Availability(available=False, message=str(exc), help_url=exc.help_url)
        except HardwareError as exc:
            return Availability(available=False, message=str(exc))
        return Availability(available=True, library_version=f"API SDRplay {self._api_version}")

    # --- devices -----------------------------------------------------------------

    def _list_devices(self) -> list[api.DeviceT]:
        lib = self._ensure_api()
        devices = (api.DeviceT * api.MAX_DEVICES)()
        count = ctypes.c_uint(0)
        self._call("sdrplay_api_LockDeviceApi")
        try:
            err = lib.sdrplay_api_GetDevices(devices, ctypes.byref(count), api.MAX_DEVICES)
        finally:
            lib.sdrplay_api_UnlockDeviceApi()
        if err != api.SUCCESS:
            raise HardwareError(f"Liste des appareils SDRplay : {self._error(err)}")
        return [devices[i] for i in range(count.value)]

    @staticmethod
    def _info(device: api.DeviceT) -> DeviceInfo:
        return DeviceInfo(
            driver=SdrplayDriver.name,
            model=model_name(device.hwVer),
            serial=device.SerNo.decode(errors="replace"),
            details={"hw_ver": str(device.hwVer)},
        )

    def detect(self) -> list[DeviceInfo]:
        with self._lock:
            found = [self._info(d) for d in self._list_devices()]
            if self._device is not None:
                # A selected device is no longer listed as available: report it anyway.
                current = self._info(self._device)
                if all(d.serial != current.serial for d in found):
                    found.insert(0, current)
            return found

    def open(self, serial: str | None = None) -> DeviceInfo:
        with self._lock:
            if self._device is not None:
                raise HardwareError("Un appareil SDRplay est déjà ouvert.")
            devices = self._list_devices()
            if not devices:
                raise HardwareError(
                    "Aucun SDRplay détecté (débranché, ou utilisé par un autre logiciel ?)."
                )
            chosen = devices[0]
            if serial:
                matches = [d for d in devices if d.SerNo.decode(errors="replace") == serial]
                if not matches:
                    raise HardwareError(f"SDRplay {serial} introuvable.")
                chosen = matches[0]
            self._check_lna_state(chosen.hwVer)
            self.check_antenna(self._info(chosen), self._antenna)
            if chosen.hwVer == api.HW_RSPDUO:
                chosen.tuner = api.TUNER_A
                chosen.rspDuoMode = api.RSPDUO_SINGLE_TUNER
                chosen.rspDuoSampleFreq = 0.0
            self._call("sdrplay_api_LockDeviceApi")
            try:
                self._call("sdrplay_api_SelectDevice", ctypes.byref(chosen))
            finally:
                self._ensure_api().sdrplay_api_UnlockDeviceApi()
            self._device = chosen
            try:
                params = ctypes.POINTER(api.DeviceParamsT)()
                self._call("sdrplay_api_GetDeviceParams", chosen.dev, ctypes.byref(params))
                self._params = params
                self._configure()
                self._start()
            except BaseException:
                self._release()
                raise
            return self._info(chosen)

    def close(self) -> None:
        with self._lock:
            self._release()

    def _release(self) -> None:
        lib = self._lib
        if lib is not None and self._device is not None:
            if self._streaming:
                lib.sdrplay_api_Uninit(self._device.dev)
                self._streaming = False
            lib.sdrplay_api_ReleaseDevice(ctypes.byref(self._device))
        self._device = None
        self._params = None
        self._callbacks = None
        with self._data_ready:
            self._buffer.clear()
            self._buffered = 0
            self._data_ready.notify_all()

    def shutdown(self) -> None:
        with self._lock:
            self._release()
            self._close_api()

    @property
    def is_open(self) -> bool:
        return self._device is not None

    def _check_lna_state(self, hw_ver: int) -> None:
        states = LNA_STATES_HF.get(hw_ver)
        if states is not None and self._config.lna_state >= states:
            raise HardwareError(
                f"État LNA {self._config.lna_state} invalide pour {model_name(hw_ver)} "
                f"en HF (0 à {states - 1})."
            )

    # --- configuration -----------------------------------------------------------

    def _channel(self) -> Any:
        return self._params.contents.rxChannelA.contents

    def _dev_params(self) -> Any:
        return self._params.contents.devParams.contents

    def _configure(self) -> None:
        assert self._device is not None
        hw = self._device.hwVer
        ch = self._channel()
        dev = self._dev_params()
        dev.fsFreq.fsHz = float(ADC_RATE)
        dev.ppm = self._ppm
        tuner = ch.tunerParams
        tuner.bwType = bandwidth_for(self._rate)
        tuner.ifType = api.IF_ZERO
        tuner.loMode = api.LO_AUTO
        tuner.rfFreq.rfHz = self._frequency
        tuner.gain.LNAstate = self._config.lna_state
        tuner.gain.gRdB = self._grdb(self._gain_index)
        tuner.gain.minGr = api.NORMAL_MIN_GR
        ctrl = ch.ctrlParams
        ctrl.agc.enable = api.AGC_DISABLE
        decimation = ADC_RATE // self._rate
        ctrl.decimation.enable = 1 if decimation > 1 else 0
        ctrl.decimation.decimationFactor = decimation
        ctrl.decimation.wideBandSignal = 0
        if hw == api.HW_RSP2:
            # 50 ohm inputs A/B (AMPORT_2, the API default), not the Hi-Z input.
            ch.rsp2TunerParams.amPortSel = api.RSP2_AMPORT_2
        self._apply_antenna()
        self._apply_bias_tee()

    def _apply_antenna(self) -> int:
        """Set the antenna field for this model; returns its update reason (-1: RSPdx ext1)."""
        assert self._device is not None
        hw = self._device.hwVer
        antenna = self.effective_antenna(self._info(self._device))
        if hw == api.HW_RSP2:
            self._channel().rsp2TunerParams.antennaSel = (
                api.RSP2_ANTENNA_B if antenna == "B" else api.RSP2_ANTENNA_A
            )
            return api.UPDATE_RSP2_ANTENNA
        if hw in (api.HW_RSPDX, api.HW_RSPDXR2):
            self._dev_params().rspDxParams.antennaSel = api.RSPDX_ANTENNA[antenna]
            return -1
        return 0

    def antennas(self, device: DeviceInfo) -> list[str]:
        hw = int(device.details.get("hw_ver", "0"))
        return list(ANTENNAS.get(hw, ()))

    def set_antenna(self, antenna: str) -> SwitchCommand | None:
        with self._lock:
            if self._device is not None:
                self.check_antenna(self._info(self._device), antenna)
            self._antenna = antenna
            if self._params is None or self._device is None:
                return None
            reason = self._apply_antenna()
            with self._data_ready:
                position = self._next_index
            stamp = time.monotonic_ns()
            if reason == -1:
                self._update(api.UPDATE_NONE, api.UPDATE_EXT1_RSPDX_ANTENNA)
            elif reason:
                self._update(reason)
            return SwitchCommand(
                antenna=self.effective_antenna(self._info(self._device)),
                sample=position,
                monotonic_ns=stamp,
            )

    def _chosen_antenna(self) -> str:
        return self._antenna

    def _apply_bias_tee(self) -> int:
        """Set the bias-tee field for this model; returns its update reason (-1: RSPdx ext1)."""
        assert self._device is not None
        hw = self._device.hwVer
        value = 1 if self._bias_tee else 0
        if hw in (api.HW_RSP1A, api.HW_RSP1B):
            self._channel().rsp1aTunerParams.biasTEnable = value
            return api.UPDATE_RSP1A_BIAST
        if hw == api.HW_RSP2:
            self._channel().rsp2TunerParams.biasTEnable = value
            return api.UPDATE_RSP2_BIAST
        if hw == api.HW_RSPDUO:
            self._channel().rspDuoTunerParams.biasTEnable = value
            return api.UPDATE_RSPDUO_BIAST
        if hw in (api.HW_RSPDX, api.HW_RSPDXR2):
            self._dev_params().rspDxParams.biasTEnable = value
            return -1  # extension flag, see set_bias_tee()
        if self._bias_tee:
            raise HardwareError(f"Pas de bias-tee sur {model_name(hw)}.")
        return 0

    def _update(self, reason: int, ext1: int = api.UPDATE_EXT1_NONE) -> None:
        if self._streaming and self._device is not None:
            self._call("sdrplay_api_Update", self._device.dev, api.TUNER_A, reason, ext1)

    def frequency_range(self) -> tuple[float, float]:
        return FREQUENCY_RANGE_HZ

    def set_frequency(self, hz: float) -> None:
        low, high = FREQUENCY_RANGE_HZ
        if not low <= hz <= high:
            raise HardwareError(f"Fréquence {hz:.0f} Hz hors de la plage du SDRplay.")
        with self._lock:
            self._frequency = float(hz)
            if self._params is not None:
                self._channel().tunerParams.rfFreq.rfHz = self._frequency
                self._update(api.UPDATE_TUNER_FRF)

    def frequency(self) -> float:
        return self._frequency

    def set_sample_rate(self, sps: int) -> None:
        if sps not in self.supported_sample_rates():
            raise HardwareError(f"Fréquence d'échantillonnage {sps} non supportée par le SDRplay.")
        with self._lock:
            self._rate = sps
            if self._streaming:
                # Decimation and IF bandwidth change together: restart the stream.
                self._stop()
                self._configure()
                self._start()

    def sample_rate(self) -> int:
        return self._rate

    def instant_bandwidth(self) -> float:
        # The IF filter is the narrowest limit at low rates (200 kHz at 250 kS/s).
        return float(min(bandwidth_for(self._rate) * 1000, 0.8 * self._rate))

    def set_ppm(self, ppm: float) -> None:
        with self._lock:
            self._ppm = float(ppm)
            if self._params is not None:
                self._dev_params().ppm = self._ppm
                self._update(api.UPDATE_DEV_PPM)

    def set_bias_tee(self, enabled: bool) -> None:
        with self._lock:
            self._bias_tee = enabled
            if self._params is not None:
                reason = self._apply_bias_tee()
                if reason == -1:
                    self._update(api.UPDATE_NONE, api.UPDATE_EXT1_RSPDX_BIAST)
                elif reason:
                    self._update(reason)

    # --- gain --------------------------------------------------------------------

    @staticmethod
    def _grdb(index: int) -> int:
        return GRDB_MAX - index

    def gain_steps(self) -> list[GainStep]:
        lna = self._config.lna_state
        return [
            GainStep(
                index=i,
                label=f"LNA {lna}, GR {self._grdb(i)} dB",
                native={"lna_state": lna, "grdb": self._grdb(i)},
            )
            for i in range(GRDB_MAX - GRDB_MIN + 1)
        ]

    def set_gain_step(self, index: int) -> None:
        self._check_gain_index(index)
        with self._lock:
            self._gain_index = index
            if self._params is not None:
                self._channel().tunerParams.gain.gRdB = self._grdb(index)
                self._update(api.UPDATE_TUNER_GR)

    def get_gain_state(self) -> GainState:
        step = self.gain_steps()[self._gain_index]
        return GainState(step_index=step.index, native=step.native)

    def gain_mode(self) -> str:
        return f"lna{self._config.lna_state}-grdb"

    def overload(self) -> bool:
        return self._overload

    # dBm display in HF needs the HF LNA gain-reduction tables (API specification, not
    # the headers): not available yet, dbm_offset_db() keeps the base class None.

    def supported_sample_rates(self) -> list[int]:
        return [ADC_RATE // d for d in DECIMATIONS]

    # --- streaming ---------------------------------------------------------------

    def _start(self) -> None:
        assert self._device is not None
        self._overload = False
        self._dropped = 0
        self._last_first = None
        with self._data_ready:
            self._buffer.clear()
            self._buffered = 0
            self._next_index = 0
        callbacks = api.CallbackFnsT()
        callbacks.StreamACbFn = api.StreamCallback(self._on_stream)
        callbacks.StreamBCbFn = api.StreamCallback(self._on_stream)
        callbacks.EventCbFn = api.EventCallback(self._on_event)
        # Keep a reference: the library calls these from its own thread.
        self._callbacks = callbacks
        self._call("sdrplay_api_Init", self._device.dev, ctypes.byref(callbacks), None)
        self._streaming = True

    def _stop(self) -> None:
        if self._streaming and self._device is not None:
            self._call("sdrplay_api_Uninit", self._device.dev)
        self._streaming = False

    def _on_stream(
        self, xi: Any, xq: Any, params: Any, num_samples: int, reset: int, context: Any
    ) -> None:
        if num_samples == 0:
            return
        first = int(params.contents.firstSampleNum) if params else None
        i = np.ctypeslib.as_array(xi, shape=(num_samples,))
        q = np.ctypeslib.as_array(xq, shape=(num_samples,))
        block: IQBlock = np.empty(num_samples, dtype=np.complex64)
        block.real = i
        block.imag = q
        block *= np.float32(1 / 32768)
        limit = int(self._rate * BUFFER_SECONDS)
        with self._data_ready:
            if first is not None and self._last_first is not None and not reset:
                gap = (first - self._last_first) % _UINT32
                if 0 < gap < _UINT32 // 2:
                    # Samples the API counted but never delivered: keep the index in step.
                    self._dropped += gap
                    self._next_index += gap
            if first is not None:
                self._last_first = (first + num_samples) % _UINT32
            self._buffer.append((self._next_index, block))
            self._next_index += num_samples
            self._buffered += num_samples
            while self._buffered > limit and len(self._buffer) > 1:
                _, dropped = self._buffer.popleft()
                self._buffered -= len(dropped)
                self._dropped += len(dropped)
            self._data_ready.notify_all()

    def _on_event(self, event_id: int, tuner: int, params: Any, context: Any) -> None:
        if event_id == api.EVENT_POWER_OVERLOAD_CHANGE:
            change = params.contents.powerOverloadParams.powerOverloadChangeType
            self._overload = change == api.OVERLOAD_DETECTED
            # The API expects every overload message to be acknowledged.
            if self._lib is not None and self._device is not None:
                self._lib.sdrplay_api_Update(
                    self._device.dev, tuner, api.UPDATE_CTRL_OVERLOAD_ACK, api.UPDATE_EXT1_NONE
                )
        elif event_id == api.EVENT_DEVICE_REMOVED:
            log.error("SDRplay débranché")

    def sample_counter(self) -> int:
        with self._data_ready:
            return self._buffer[0][0] if self._buffer else self._next_index

    def read_iq(self, n: int, timeout_s: float = 2.0) -> IQBlock:
        if not self._streaming:
            raise HardwareError("SDRplay non ouvert.")
        with self._data_ready:
            if not self._data_ready.wait_for(lambda: self._buffered >= n, timeout=timeout_s):
                raise HardwareError("Pas d'échantillons du SDRplay (délai dépassé).")
            out = np.empty(n, dtype=np.complex64)
            filled = 0
            while filled < n:
                start, block = self._buffer[0]
                take = min(len(block), n - filled)
                out[filled : filled + take] = block[:take]
                filled += take
                if take == len(block):
                    self._buffer.popleft()
                else:
                    self._buffer[0] = (start + take, block[take:])
            self._buffered -= n
            return out

    def dropped_samples(self) -> int:
        return self._dropped
