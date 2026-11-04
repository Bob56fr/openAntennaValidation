"""ctypes binding to the SDRplay API 3.x (sdrplay_api.dll / libsdrplay_api.so).

Structures mirror the C headers of API 3.15 (``sdrplay_api*.h``), field for
field and in the same order; default C alignment applies on every platform.
Only what the driver needs is declared.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import sys
from pathlib import Path

from open_antenna_validation.hardware.base import DriverUnavailable

HEADER_VERSION = 3.15
DOWNLOAD_URL = "https://www.sdrplay.com/downloads/"

MAX_DEVICES = 16
MAX_SER_NO_LEN = 64

# Hardware version ids (sdrplay_api.h). RSP2 and RSP2pro share id 2.
HW_RSP1 = 1
HW_RSP1A = 255
HW_RSP2 = 2
HW_RSPDUO = 3
HW_RSPDX = 4
HW_RSP1B = 6
HW_RSPDXR2 = 7

# sdrplay_api_ErrT
SUCCESS = 0
ERR_SERVICE_NOT_RESPONDING = 14

# sdrplay_api_TunerSelectT
TUNER_A = 1

# sdrplay_api_RspDuoModeT
RSPDUO_SINGLE_TUNER = 1

# sdrplay_api_Bw_MHzT (kHz values)
BW_0_200 = 200
BW_0_300 = 300
BW_0_600 = 600
BW_1_536 = 1536

IF_ZERO = 0
LO_AUTO = 1
NORMAL_MIN_GR = 20
AGC_DISABLE = 0

RSP2_ANTENNA_A = 5
RSP2_ANTENNA_B = 6
RSP2_AMPORT_2 = 0  # main 50 ohm ports; AMPORT_1 is the Hi-Z input
RSPDX_ANTENNA = {"A": 0, "B": 1, "C": 2}

# sdrplay_api_ReasonForUpdateT
UPDATE_NONE = 0x00000000
UPDATE_DEV_PPM = 0x00000002
UPDATE_RSP1A_BIAST = 0x00000010
UPDATE_RSP2_BIAST = 0x00000080
UPDATE_RSP2_ANTENNA = 0x00000200
UPDATE_TUNER_GR = 0x00008000
UPDATE_TUNER_FRF = 0x00020000
UPDATE_CTRL_OVERLOAD_ACK = 0x04000000
UPDATE_RSPDUO_BIAST = 0x08000000
# sdrplay_api_ReasonForUpdateExtension1T
UPDATE_EXT1_NONE = 0x00000000
UPDATE_EXT1_RSPDX_BIAST = 0x00000002
UPDATE_EXT1_RSPDX_ANTENNA = 0x00000004

# sdrplay_api_EventT / PowerOverloadCbEventIdT
EVENT_POWER_OVERLOAD_CHANGE = 1
EVENT_DEVICE_REMOVED = 2
OVERLOAD_DETECTED = 0

c_enum = ctypes.c_int


class DeviceT(ctypes.Structure):
    _fields_ = [
        ("SerNo", ctypes.c_char * MAX_SER_NO_LEN),
        ("hwVer", ctypes.c_ubyte),
        ("tuner", c_enum),
        ("rspDuoMode", c_enum),
        ("valid", ctypes.c_ubyte),
        ("rspDuoSampleFreq", ctypes.c_double),
        ("dev", ctypes.c_void_p),
    ]


class FsFreqT(ctypes.Structure):
    _fields_ = [
        ("fsHz", ctypes.c_double),
        ("syncUpdate", ctypes.c_ubyte),
        ("reCal", ctypes.c_ubyte),
    ]


class SyncUpdateT(ctypes.Structure):
    _fields_ = [("sampleNum", ctypes.c_uint), ("period", ctypes.c_uint)]


class ResetFlagsT(ctypes.Structure):
    _fields_ = [
        ("resetGainUpdate", ctypes.c_ubyte),
        ("resetRfUpdate", ctypes.c_ubyte),
        ("resetFsUpdate", ctypes.c_ubyte),
    ]


class Rsp1aParamsT(ctypes.Structure):
    _fields_ = [("rfNotchEnable", ctypes.c_ubyte), ("rfDabNotchEnable", ctypes.c_ubyte)]


class Rsp2ParamsT(ctypes.Structure):
    _fields_ = [("extRefOutputEn", ctypes.c_ubyte)]


class RspDuoParamsT(ctypes.Structure):
    _fields_ = [("extRefOutputEn", ctypes.c_int)]


class RspDxParamsT(ctypes.Structure):
    _fields_ = [
        ("hdrEnable", ctypes.c_ubyte),
        ("biasTEnable", ctypes.c_ubyte),
        ("antennaSel", c_enum),
        ("rfNotchEnable", ctypes.c_ubyte),
        ("rfDabNotchEnable", ctypes.c_ubyte),
    ]


class DevParamsT(ctypes.Structure):
    _fields_ = [
        ("ppm", ctypes.c_double),
        ("fsFreq", FsFreqT),
        ("syncUpdate", SyncUpdateT),
        ("resetFlags", ResetFlagsT),
        ("mode", c_enum),
        ("samplesPerPkt", ctypes.c_uint),
        ("rsp1aParams", Rsp1aParamsT),
        ("rsp2Params", Rsp2ParamsT),
        ("rspDuoParams", RspDuoParamsT),
        ("rspDxParams", RspDxParamsT),
    ]


class GainValuesT(ctypes.Structure):
    _fields_ = [("curr", ctypes.c_float), ("max", ctypes.c_float), ("min", ctypes.c_float)]


class GainT(ctypes.Structure):
    _fields_ = [
        ("gRdB", ctypes.c_int),
        ("LNAstate", ctypes.c_ubyte),
        ("syncUpdate", ctypes.c_ubyte),
        ("minGr", c_enum),
        ("gainVals", GainValuesT),
    ]


class RfFreqT(ctypes.Structure):
    _fields_ = [("rfHz", ctypes.c_double), ("syncUpdate", ctypes.c_ubyte)]


class DcOffsetTunerT(ctypes.Structure):
    _fields_ = [
        ("dcCal", ctypes.c_ubyte),
        ("speedUp", ctypes.c_ubyte),
        ("trackTime", ctypes.c_int),
        ("refreshRateTime", ctypes.c_int),
    ]


class TunerParamsT(ctypes.Structure):
    _fields_ = [
        ("bwType", c_enum),
        ("ifType", c_enum),
        ("loMode", c_enum),
        ("gain", GainT),
        ("rfFreq", RfFreqT),
        ("dcOffsetTuner", DcOffsetTunerT),
    ]


class DcOffsetT(ctypes.Structure):
    _fields_ = [("DCenable", ctypes.c_ubyte), ("IQenable", ctypes.c_ubyte)]


class DecimationT(ctypes.Structure):
    _fields_ = [
        ("enable", ctypes.c_ubyte),
        ("decimationFactor", ctypes.c_ubyte),
        ("wideBandSignal", ctypes.c_ubyte),
    ]


class AgcT(ctypes.Structure):
    _fields_ = [
        ("enable", c_enum),
        ("setPoint_dBfs", ctypes.c_int),
        ("attack_ms", ctypes.c_ushort),
        ("decay_ms", ctypes.c_ushort),
        ("decay_delay_ms", ctypes.c_ushort),
        ("decay_threshold_dB", ctypes.c_ushort),
        ("syncUpdate", ctypes.c_int),
    ]


class ControlParamsT(ctypes.Structure):
    _fields_ = [
        ("dcOffset", DcOffsetT),
        ("decimation", DecimationT),
        ("agc", AgcT),
        ("adsbMode", c_enum),
    ]


class Rsp1aTunerParamsT(ctypes.Structure):
    _fields_ = [("biasTEnable", ctypes.c_ubyte)]


class Rsp2TunerParamsT(ctypes.Structure):
    _fields_ = [
        ("biasTEnable", ctypes.c_ubyte),
        ("amPortSel", c_enum),
        ("antennaSel", c_enum),
        ("rfNotchEnable", ctypes.c_ubyte),
    ]


class RspDuoResetSlaveFlagsT(ctypes.Structure):
    _fields_ = [("resetGainUpdate", ctypes.c_ubyte), ("resetRfUpdate", ctypes.c_ubyte)]


class RspDuoTunerParamsT(ctypes.Structure):
    _fields_ = [
        ("biasTEnable", ctypes.c_ubyte),
        ("tuner1AmPortSel", c_enum),
        ("tuner1AmNotchEnable", ctypes.c_ubyte),
        ("rfNotchEnable", ctypes.c_ubyte),
        ("rfDabNotchEnable", ctypes.c_ubyte),
        ("resetSlaveFlags", RspDuoResetSlaveFlagsT),
    ]


class RspDxTunerParamsT(ctypes.Structure):
    _fields_ = [("hdrBw", c_enum)]


class RxChannelParamsT(ctypes.Structure):
    _fields_ = [
        ("tunerParams", TunerParamsT),
        ("ctrlParams", ControlParamsT),
        ("rsp1aTunerParams", Rsp1aTunerParamsT),
        ("rsp2TunerParams", Rsp2TunerParamsT),
        ("rspDuoTunerParams", RspDuoTunerParamsT),
        ("rspDxTunerParams", RspDxTunerParamsT),
    ]


class DeviceParamsT(ctypes.Structure):
    _fields_ = [
        ("devParams", ctypes.POINTER(DevParamsT)),
        ("rxChannelA", ctypes.POINTER(RxChannelParamsT)),
        ("rxChannelB", ctypes.POINTER(RxChannelParamsT)),
    ]


class StreamCbParamsT(ctypes.Structure):
    _fields_ = [
        ("firstSampleNum", ctypes.c_uint),
        ("grChanged", ctypes.c_int),
        ("rfChanged", ctypes.c_int),
        ("fsChanged", ctypes.c_int),
        ("numSamples", ctypes.c_uint),
    ]


class GainCbParamT(ctypes.Structure):
    _fields_ = [("gRdB", ctypes.c_uint), ("lnaGRdB", ctypes.c_uint), ("currGain", ctypes.c_double)]


class PowerOverloadCbParamT(ctypes.Structure):
    _fields_ = [("powerOverloadChangeType", c_enum)]


class RspDuoModeCbParamT(ctypes.Structure):
    _fields_ = [("modeChangeType", c_enum)]


class EventParamsT(ctypes.Union):
    _fields_ = [
        ("gainParams", GainCbParamT),
        ("powerOverloadParams", PowerOverloadCbParamT),
        ("rspDuoModeParams", RspDuoModeCbParamT),
    ]


StreamCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.POINTER(ctypes.c_short),
    ctypes.POINTER(ctypes.c_short),
    ctypes.POINTER(StreamCbParamsT),
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.c_void_p,
)
EventCallback = ctypes.CFUNCTYPE(
    None, c_enum, c_enum, ctypes.POINTER(EventParamsT), ctypes.c_void_p
)


class CallbackFnsT(ctypes.Structure):
    _fields_ = [
        ("StreamACbFn", StreamCallback),
        ("StreamBCbFn", StreamCallback),
        ("EventCbFn", EventCallback),
    ]


def candidate_paths(configured: str = "") -> list[str]:
    paths = [configured] if configured else []
    if sys.platform == "win32":
        arch = "arm64" if platform.machine().upper() == "ARM64" else "x64"
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        paths.append(str(Path(program_files) / "SDRplay" / "API" / arch / "sdrplay_api.dll"))
        paths.append("sdrplay_api.dll")
    else:
        found = ctypes.util.find_library("sdrplay_api")
        if found:
            paths.append(found)
        paths += [
            "libsdrplay_api.so.3",
            "/usr/local/lib/libsdrplay_api.so.3",
            "/usr/local/lib/libsdrplay_api.so",
        ]
    return paths


def load_library(configured: str = "") -> ctypes.CDLL:
    errors = []
    for path in candidate_paths(configured):
        try:
            lib = ctypes.CDLL(path)
        except OSError as exc:
            errors.append(f"{path} : {exc}")
            continue
        _declare(lib)
        return lib
    raise DriverUnavailable(
        "API SDRplay introuvable. Installez l'API SDRplay 3.x depuis le site du fabricant, "
        "puis relancez le service.",
        help_url=DOWNLOAD_URL,
    ) from OSError("; ".join(errors))


def _declare(lib: ctypes.CDLL) -> None:
    err = c_enum
    handle = ctypes.c_void_p
    signatures: dict[str, tuple[object, list[object]]] = {
        "sdrplay_api_Open": (err, []),
        "sdrplay_api_Close": (err, []),
        "sdrplay_api_ApiVersion": (err, [ctypes.POINTER(ctypes.c_float)]),
        "sdrplay_api_LockDeviceApi": (err, []),
        "sdrplay_api_UnlockDeviceApi": (err, []),
        "sdrplay_api_GetDevices": (
            err,
            [ctypes.POINTER(DeviceT), ctypes.POINTER(ctypes.c_uint), ctypes.c_uint],
        ),
        "sdrplay_api_SelectDevice": (err, [ctypes.POINTER(DeviceT)]),
        "sdrplay_api_ReleaseDevice": (err, [ctypes.POINTER(DeviceT)]),
        "sdrplay_api_GetErrorString": (ctypes.c_char_p, [err]),
        "sdrplay_api_GetDeviceParams": (
            err,
            [handle, ctypes.POINTER(ctypes.POINTER(DeviceParamsT))],
        ),
        "sdrplay_api_Init": (err, [handle, ctypes.POINTER(CallbackFnsT), ctypes.c_void_p]),
        "sdrplay_api_Uninit": (err, [handle]),
        "sdrplay_api_Update": (err, [handle, c_enum, ctypes.c_uint, ctypes.c_uint]),
    }
    for name, (restype, argtypes) in signatures.items():
        func = getattr(lib, name)
        func.restype = restype
        func.argtypes = argtypes
