"""ctypes binding to libairspyhf (airspyhf.h 1.8.1, github.com/airspy/airspyhf).

Only what the driver needs is declared. airspyhf_set_att / airspyhf_get_att_steps are
recent (1.8); older libraries only have the legacy airspyhf_set_hf_att (index 0..8, 6 dB
steps), used as a fallback.
"""

from __future__ import annotations

import ctypes
from typing import Any

from open_antenna_validation.hardware import libload

DOWNLOAD_URL = "https://github.com/airspy/airspyhf/releases"

SUCCESS = 0
VERSION_STRING_SIZE = 64  # MAX_VERSION_STRING_SIZE
# Legacy attenuator (airspyhf.h, airspyhf_set_hf_att): index 0..8, 0..48 dB by 6 dB.
LEGACY_ATT_STEPS_DB = tuple(6.0 * i for i in range(9))


class LibVersionT(ctypes.Structure):
    _fields_ = [
        ("major_version", ctypes.c_uint32),
        ("minor_version", ctypes.c_uint32),
        ("revision", ctypes.c_uint32),
    ]


class TransferT(ctypes.Structure):
    """airspyhf_transfer_t; samples are airspyhf_complex_float_t (re, im floats)."""

    _fields_ = [
        ("device", ctypes.c_void_p),
        ("ctx", ctypes.c_void_p),
        ("samples", ctypes.POINTER(ctypes.c_float)),
        ("sample_count", ctypes.c_int),
        ("dropped_samples", ctypes.c_uint64),
    ]


SampleCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(TransferT))

_DEV = ctypes.c_void_p
_SIGNATURES: dict[str, tuple[object, list[object]]] = {
    "airspyhf_lib_version": (None, [ctypes.POINTER(LibVersionT)]),
    "airspyhf_list_devices": (ctypes.c_int, [ctypes.POINTER(ctypes.c_uint64), ctypes.c_int]),
    "airspyhf_open_sn": (ctypes.c_int, [ctypes.POINTER(_DEV), ctypes.c_uint64]),
    "airspyhf_close": (ctypes.c_int, [_DEV]),
    "airspyhf_start": (ctypes.c_int, [_DEV, SampleCallback, ctypes.c_void_p]),
    "airspyhf_stop": (ctypes.c_int, [_DEV]),
    "airspyhf_set_freq": (ctypes.c_int, [_DEV, ctypes.c_uint32]),
    "airspyhf_get_samplerates": (
        ctypes.c_int, [_DEV, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]),
    "airspyhf_set_samplerate": (ctypes.c_int, [_DEV, ctypes.c_uint32]),
    "airspyhf_set_att": (ctypes.c_int, [_DEV, ctypes.c_float]),
    "airspyhf_get_att_steps": (ctypes.c_int, [_DEV, ctypes.c_void_p, ctypes.c_uint32]),
    "airspyhf_set_hf_att": (ctypes.c_int, [_DEV, ctypes.c_uint8]),
    "airspyhf_set_hf_agc": (ctypes.c_int, [_DEV, ctypes.c_uint8]),
    "airspyhf_set_hf_lna": (ctypes.c_int, [_DEV, ctypes.c_uint8]),
    "airspyhf_set_bias_tee": (ctypes.c_int, [_DEV, ctypes.c_int8]),
    "airspyhf_set_calibration": (ctypes.c_int, [_DEV, ctypes.c_int32]),
    "airspyhf_version_string_read": (ctypes.c_int, [_DEV, ctypes.c_char_p, ctypes.c_uint8]),
}
_OPTIONAL = frozenset({"airspyhf_set_att", "airspyhf_get_att_steps", "airspyhf_set_bias_tee",
                       "airspyhf_version_string_read"})


def _declare(lib: ctypes.CDLL) -> None:
    libload.declare(lib, _SIGNATURES, _OPTIONAL)


def library_version(lib: Any) -> str:
    version = LibVersionT()
    lib.airspyhf_lib_version(ctypes.byref(version))
    return f"{version.major_version}.{version.minor_version}.{version.revision}"


def load_library(configured: str = "") -> ctypes.CDLL:
    paths = libload.candidate_paths(
        configured, ["airspyhf.dll", "libairspyhf.dll"], "airspyhf",
        ["libairspyhf.so.0", "libairspyhf.so", "/usr/local/lib/libairspyhf.so.0",
         "/usr/local/lib/libairspyhf.so", "libairspyhf.dylib",
         "/opt/homebrew/lib/libairspyhf.dylib", "/usr/local/lib/libairspyhf.dylib"])
    return libload.load_first(
        paths, _declare,
        "Bibliothèque libairspyhf introuvable. Installez-la (paquet libairspyhf ou version du "
        "fabricant), ou indiquez son chemin dans la configuration "
        "(hardware.airspyhf.library_path).",
        DOWNLOAD_URL)
