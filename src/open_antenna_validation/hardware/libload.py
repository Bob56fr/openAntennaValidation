"""Loading of a vendor shared library by ctypes, from a list of candidate paths."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from collections.abc import Callable
from pathlib import Path

from open_antenna_validation.hardware.base import DriverUnavailable


def candidate_paths(configured: str, windows: list[str], unix_name: str,
                    unix: list[str]) -> list[str]:
    """``configured`` first, then the platform's usual names and places."""
    paths = [configured] if configured else []
    if sys.platform == "win32":
        for name in windows:
            found = ctypes.util.find_library(name)  # searches PATH on Windows
            if found:
                paths.append(found)
        paths += windows
    else:
        found = ctypes.util.find_library(unix_name)
        if found:
            paths.append(found)
        paths += unix
    return list(dict.fromkeys(paths))


def load_first(paths: list[str], declare: Callable[[ctypes.CDLL], None], message: str,
               help_url: str | None) -> ctypes.CDLL:
    errors = []
    for path in paths:
        try:
            if sys.platform == "win32" and os.path.isabs(path) and os.path.isfile(path):
                # Dependencies (libusb-1.0.dll) usually sit next to the library.
                os.add_dll_directory(str(Path(path).parent))
            lib = ctypes.CDLL(path)
        except OSError as exc:
            errors.append(f"{path} : {exc}")
            continue
        declare(lib)
        return lib
    raise DriverUnavailable(message, help_url=help_url) from OSError("; ".join(errors))


def declare(lib: ctypes.CDLL, signatures: dict[str, tuple[object, list[object]]],
            optional: frozenset[str] = frozenset()) -> None:
    """Set restype/argtypes; a missing function is an error unless listed as optional."""
    for name, (restype, argtypes) in signatures.items():
        if name in optional and not hasattr(lib, name):
            continue
        func = getattr(lib, name)
        func.restype = restype
        func.argtypes = argtypes
