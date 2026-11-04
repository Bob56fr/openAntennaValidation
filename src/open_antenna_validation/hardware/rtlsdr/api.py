"""ctypes binding to librtlsdr (rtl-sdr.h of the RTL-SDR Blog branch, also osmocom).

Only what the driver needs is declared. ``rtlsdr_check_dongle_model`` is exported by the
RTL-SDR Blog branch only: its presence tells which branch is loaded.
"""

from __future__ import annotations

import ctypes

from open_antenna_validation.hardware import libload

DOWNLOAD_URL = "https://github.com/rtlsdrblog/rtl-sdr-blog/releases"

# USB strings of the RTL-SDR Blog V4 (librtlsdr.c, rtlsdr_check_dongle_model calls).
V4_MANUFACTURER = "RTLSDRBlog"
V4_PRODUCT = "Blog V4"
USB_STRING_SIZE = 256  # rtlsdr_get_device_usb_strings: "space for up to 256 bytes"

# enum rtlsdr_tuner
TUNER_R828D = 6
# rtlsdr_set_freq_correction: same value as before (not an error).
CORRECTION_UNCHANGED = -2

ReadAsyncCallback = ctypes.CFUNCTYPE(
    None, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint32, ctypes.c_void_p)

_DEV = ctypes.c_void_p
_SIGNATURES: dict[str, tuple[object, list[object]]] = {
    "rtlsdr_get_device_count": (ctypes.c_uint32, []),
    "rtlsdr_get_device_usb_strings": (
        ctypes.c_int, [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]),
    "rtlsdr_open": (ctypes.c_int, [ctypes.POINTER(_DEV), ctypes.c_uint32]),
    "rtlsdr_close": (ctypes.c_int, [_DEV]),
    "rtlsdr_get_tuner_type": (ctypes.c_int, [_DEV]),
    "rtlsdr_set_center_freq": (ctypes.c_int, [_DEV, ctypes.c_uint32]),
    "rtlsdr_set_freq_correction": (ctypes.c_int, [_DEV, ctypes.c_int]),
    "rtlsdr_get_tuner_gains": (ctypes.c_int, [_DEV, ctypes.POINTER(ctypes.c_int)]),
    "rtlsdr_set_tuner_gain": (ctypes.c_int, [_DEV, ctypes.c_int]),
    "rtlsdr_set_tuner_gain_mode": (ctypes.c_int, [_DEV, ctypes.c_int]),
    "rtlsdr_set_tuner_bandwidth": (ctypes.c_int, [_DEV, ctypes.c_uint32]),
    "rtlsdr_set_sample_rate": (ctypes.c_int, [_DEV, ctypes.c_uint32]),
    "rtlsdr_get_sample_rate": (ctypes.c_uint32, [_DEV]),
    "rtlsdr_set_agc_mode": (ctypes.c_int, [_DEV, ctypes.c_int]),
    "rtlsdr_reset_buffer": (ctypes.c_int, [_DEV]),
    "rtlsdr_read_async": (
        ctypes.c_int, [_DEV, ReadAsyncCallback, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.c_uint32]),
    "rtlsdr_cancel_async": (ctypes.c_int, [_DEV]),
    "rtlsdr_set_bias_tee": (ctypes.c_int, [_DEV, ctypes.c_int]),
    "rtlsdr_check_dongle_model": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p,
                                                 ctypes.c_char_p]),
}
_OPTIONAL = frozenset({"rtlsdr_set_bias_tee", "rtlsdr_check_dongle_model",
                       "rtlsdr_set_tuner_bandwidth"})


def _declare(lib: ctypes.CDLL) -> None:
    libload.declare(lib, _SIGNATURES, _OPTIONAL)


def is_blog_branch(lib: object) -> bool:
    return hasattr(lib, "rtlsdr_check_dongle_model")


def load_library(configured: str = "") -> ctypes.CDLL:
    paths = libload.candidate_paths(
        configured, ["rtlsdr.dll", "librtlsdr.dll"], "rtlsdr",
        ["librtlsdr.so.0", "librtlsdr.so", "/usr/local/lib/librtlsdr.so.0",
         "/usr/local/lib/librtlsdr.so", "librtlsdr.dylib", "/opt/homebrew/lib/librtlsdr.dylib",
         "/usr/local/lib/librtlsdr.dylib"])
    return libload.load_first(
        paths, _declare,
        "Bibliothèque librtlsdr introuvable. Installez la version de la branche RTL-SDR Blog "
        "(nécessaire au convertisseur HF de la V4), ou indiquez son chemin dans la "
        "configuration (hardware.rtlsdr.library_path).",
        DOWNLOAD_URL)
