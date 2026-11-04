"""Stand-ins for librtlsdr and libairspyhf, following their C contracts (rtl-sdr.h,
airspyhf.h and the library sources) closely enough to drive the real drivers without
hardware. Arguments arrive as the drivers build them: ctypes.byref() objects expose the
target as ``_obj``, string buffers and arrays are written in place.
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Any

import numpy as np

from open_antenna_validation.hardware.airspyhf import api as airspy_api

V4 = ("RTLSDRBlog", "Blog V4")


class FakeRtlLib:
    """librtlsdr with dongles given as (manufacturer, product, serial)."""

    rtlsdr_check_dongle_model = None  # exported by the RTL-SDR Blog branch only

    def __init__(self, devices: list[tuple[str, str, str]], tone: complex = 0.5 + 0.25j) -> None:
        self.devices = devices
        self.tone = tone
        self.state: dict[str, Any] = {"open": None, "corr": 0}
        self._cancel = threading.Event()
        self.blocks = 0

    def rtlsdr_get_device_count(self) -> int:
        return len(self.devices)

    def rtlsdr_get_device_usb_strings(self, index: int, m: Any, p: Any, s: Any) -> int:
        m.value, p.value, s.value = (x.encode() for x in self.devices[index])
        return 0

    def rtlsdr_open(self, ref: Any, index: int) -> int:
        ref._obj.value = 0x1000 + index
        self.state["open"] = index
        return 0

    def rtlsdr_close(self, dev: Any) -> int:
        self.state["open"] = None
        return 0

    def rtlsdr_get_tuner_gains(self, dev: Any, gains: Any) -> int:
        table = [496, 0, 254, 9]  # unsorted: the driver sorts
        if gains is None:
            return len(table)
        for i, g in enumerate(table):
            gains[i] = g
        return len(table)

    def _set(name: str) -> Any:  # type: ignore[misc]
        def setter(self: FakeRtlLib, dev: Any, value: int) -> int:
            self.state[name] = value
            return 0
        return setter

    rtlsdr_set_tuner_gain = _set("gain")
    rtlsdr_set_tuner_gain_mode = _set("manual")
    rtlsdr_set_tuner_bandwidth = _set("bandwidth")
    rtlsdr_set_sample_rate = _set("rate")
    rtlsdr_set_agc_mode = _set("agc")
    rtlsdr_set_center_freq = _set("freq")
    rtlsdr_set_bias_tee = _set("bias_tee")

    def rtlsdr_set_freq_correction(self, dev: Any, ppm: int) -> int:
        if ppm == self.state["corr"]:
            return -2  # librtlsdr.c: unchanged value
        self.state["corr"] = ppm
        return 0

    def rtlsdr_reset_buffer(self, dev: Any) -> int:
        return 0

    def rtlsdr_read_async(self, dev: Any, cb: Any, ctx: Any, buf_num: int, buf_len: int) -> int:
        self.state["buf_len"] = buf_len
        self._cancel.clear()
        n = buf_len // 2
        iq = np.empty(2 * n, dtype=np.float32)
        iq[0::2], iq[1::2] = self.tone.real, self.tone.imag
        raw = np.clip(np.round(iq * 127.5 + 127.5), 0, 255).astype(np.uint8)
        buf = (ctypes.c_ubyte * buf_len).from_buffer_copy(raw.tobytes())
        while not self._cancel.is_set():
            cb(ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), buf_len, None)
            self.blocks += 1
            time.sleep(0.002)
        return 0

    def rtlsdr_cancel_async(self, dev: Any) -> int:
        self._cancel.set()
        return 0


class FakeAirspyLib:
    """libairspyhf with devices given by serial number."""

    def __init__(self, serials: list[int], rates: tuple[int, ...] = (912_000, 768_000, 456_000,
                 384_000, 256_000, 192_000), drop_every: int = 0) -> None:
        self.serials = serials
        self.rates = rates
        self.drop_every = drop_every  # report a lost buffer every N transfers (0: never)
        self.state: dict[str, Any] = {"open": None}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.transfers = 0

    def airspyhf_lib_version(self, ref: Any) -> None:
        version = ref._obj
        version.major_version, version.minor_version, version.revision = 1, 8, 1

    def airspyhf_list_devices(self, serials: Any, count: int) -> int:
        for i, s in enumerate(self.serials[:count]):
            serials[i] = s
        return len(self.serials)

    def airspyhf_open_sn(self, ref: Any, serial: int) -> int:
        if serial not in self.serials:
            return -1
        ref._obj.value = 0x2000
        self.state["open"] = serial
        return 0

    def airspyhf_close(self, dev: Any) -> int:
        self.state["open"] = None
        return 0

    def airspyhf_version_string_read(self, dev: Any, text: Any, length: int) -> int:
        text.value = b"R3.0.7-CD"
        return 0

    @staticmethod
    def _table(buf: Any, values: list[Any], length: int) -> int:
        if length == 0:
            buf._obj.value = len(values)
            return 0
        if length > len(values):
            return -1
        for i in range(length):
            buf[i] = values[i]
        return 0

    def airspyhf_get_samplerates(self, dev: Any, buf: Any, length: int) -> int:
        return self._table(buf, list(self.rates), length)

    def airspyhf_get_att_steps(self, dev: Any, buf: Any, length: int) -> int:
        return self._table(buf, [6.0 * i for i in range(9)], length)

    def _set(name: str) -> Any:  # type: ignore[misc]
        def setter(self: FakeAirspyLib, dev: Any, value: Any) -> int:
            self.state[name] = value
            return 0
        return setter

    airspyhf_set_samplerate = _set("rate")
    airspyhf_set_att = _set("att")
    airspyhf_set_hf_att = _set("hf_att")
    airspyhf_set_hf_agc = _set("agc")
    airspyhf_set_hf_lna = _set("lna")
    airspyhf_set_bias_tee = _set("bias_tee")
    airspyhf_set_calibration = _set("ppb")
    airspyhf_set_freq = _set("freq")

    def airspyhf_start(self, dev: Any, cb: Any, ctx: Any) -> int:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(cb,), daemon=True)
        self._thread.start()
        return 0

    def _run(self, cb: Any) -> None:
        count = 4096  # SAMPLES_TO_TRANSFER
        floats = (ctypes.c_float * (2 * count))(*([0.25, -0.125] * count))
        while not self._stop.is_set():
            self.transfers += 1
            dropped = count if self.drop_every and self.transfers % self.drop_every == 0 else 0
            transfer = airspy_api.TransferT(
                device=None, ctx=None, samples=ctypes.cast(floats, ctypes.POINTER(ctypes.c_float)),
                sample_count=count, dropped_samples=dropped)
            cb(ctypes.pointer(transfer))
            time.sleep(0.002)

    def airspyhf_stop(self, dev: Any) -> int:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        return 0
