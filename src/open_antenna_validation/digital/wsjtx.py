"""The WSJT-X programs used as decoders and encoders (decision D22).

Nothing of WSJT-X is linked or redistributed: its programs are run on audio files, as
WSJT-X itself runs them. Interfaces checked on WSJT-X 2.7.0 (Debian package ``wsjtx``,
Windows installer):

- ``jt9 -8|-5 -p <slot s> -d <depth> -a <dir> -t <dir> <file.wav>``: one line per decode,
  ``HHMMSS SNR DT FREQ ~ MESSAGE`` (FT4: ``+`` instead of ``~``), then ``<DecodeFinished>``;
- ``wsprd -f <dial MHz> -a <dir> <yymmdd_hhmm.wav>``: ``HHMM SNR DT FREQ_MHz DRIFT MESSAGE``;
- ``ft8code``, ``ft4code``, ``wsprcode "<message>"``: the channel symbols, after a line
  ``Channel symbols``. The Windows installer ships ``ft8code`` only: FT4 and WSPR messages
  are then measured on the tones seen in the audio (``measure.hard_tones``).

Audio files: mono, 16 bits, 12 kHz.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from open_antenna_validation.digital.modes import AUDIO_RATE, Mode

TIMEOUT_S = {"ft8": 30.0, "ft4": 20.0, "wspr": 90.0}
# Audio files are written at this peak level (16-bit full scale = 1).
WAV_PEAK = 0.5
_JT9_LINE = re.compile(r"^\s*(\d{4,6})\s+(-?\d+)\s+(-?\d+\.\d)\s+(\d+)\s+\S\s+(.*)$")
_WSPRD_LINE = re.compile(r"^\s*(\d{4})\s+(-?\d+)\s+(-?\d+\.\d)\s+(\d+\.\d+)\s+(-?\d+)\s+(.*?)\s*$")
# Flags that jt9 appends after the message: a priori decodes (a1…a7) and "?" (doubtful).
_AP_FLAG = re.compile(r"\s+(a\d|\?)\s*$")
_WINDOWS_DIRS = (r"C:\WSJT\wsjtx\bin", r"C:\Program Files\wsjtx\bin",
                 r"C:\Program Files\WSJT-X\bin")
_TOOL_NAMES = ("jt9", "wsprd", "ft8code", "ft4code", "wsprcode")


@dataclass
class Tools:
    bin_dir: str = ""
    programs: dict[str, str] = field(default_factory=dict)
    cty_path: str = ""
    version: str = ""

    def has(self, name: str) -> bool:
        return name in self.programs

    def decoder_for(self, mode: Mode) -> str | None:
        return self.programs.get("wsprd" if mode.name == "wspr" else "jt9")

    def encoder_for(self, mode: Mode) -> str | None:
        return self.programs.get({"ft8": "ft8code", "ft4": "ft4code", "wspr": "wsprcode"}
                                 [mode.name])


@dataclass(frozen=True)
class Decoded:
    snr_db: float
    dt_s: float
    audio_hz: float
    message: str
    drift_hz: float = 0.0
    flag: str = ""  # jt9: a1..a7 (decoded with a priori information), "?" (doubtful)

    @property
    def a_priori(self) -> bool:
        return self.flag.startswith("a")


def _exe(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" else name


def find_tools(configured: str = "") -> Tools:
    """WSJT-X programs in ``configured`` (its ``bin`` directory), else the usual places."""
    dirs = [configured] if configured else []
    if sys.platform == "win32":
        dirs += list(_WINDOWS_DIRS)
    else:
        dirs += ["/usr/bin", "/usr/local/bin"]
    for d in dirs:
        if d and os.path.isfile(os.path.join(d, _exe("jt9"))):
            programs = {n: os.path.join(d, _exe(n)) for n in _TOOL_NAMES
                        if os.path.isfile(os.path.join(d, _exe(n)))}
            return Tools(d, programs, _cty_near(d))
    # Not found in a directory: the PATH (Linux packages, a custom installation).
    found = {n: p for n in _TOOL_NAMES if (p := shutil.which(n))}
    if "jt9" in found:
        return Tools(os.path.dirname(found["jt9"]), found, _cty_near(os.path.dirname(found["jt9"])))
    return Tools()


def _cty_near(bin_dir: str) -> str:
    for candidate in (Path(bin_dir).parent / "share" / "wsjtx" / "cty.dat",
                      Path("/usr/share/wsjtx/cty.dat")):
        if candidate.is_file():
            return str(candidate)
    return ""


def write_wav(path: Path, audio: npt.NDArray[np.floating]) -> None:
    pcm = np.clip(np.asarray(audio, dtype=float) * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_RATE)
        w.writeframes(pcm.tobytes())


def scale_for_wav(audio: npt.NDArray[np.floating]) -> npt.NDArray[np.float64]:
    """Fixed headroom: the decoders only need the shape, the levels are measured apart."""
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return np.asarray(audio, dtype=float) * (WAV_PEAK / peak if peak > 0 else 1.0)


def _run(args: list[str], cwd: Path, timeout_s: float) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout_s,
                          creationflags=flags, check=False)
    return done.stdout


def decode(tools: Tools, mode: Mode, audio: npt.NDArray[np.floating], slot_ms: int,
           dial_hz: float, workdir: Path, depth: int = 3) -> list[Decoded]:
    """Decode the audio of one slot; [] when the decoder is missing or fails."""
    program = tools.decoder_for(mode)
    if program is None:
        return []
    workdir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_name(slot_ms, mode)
    path = workdir / f"{stamp}.wav"
    write_wav(path, scale_for_wav(audio))
    try:
        if mode.name == "wspr":
            out = _run([program, "-f", f"{dial_hz / 1e6:.6f}", "-a", str(workdir), str(path)],
                       workdir, TIMEOUT_S[mode.name])
            return parse_wsprd(out, dial_hz)
        flag = "-8" if mode.name == "ft8" else "-5"
        out = _run([program, flag, "-p", f"{mode.slot_s:g}", "-d", str(depth),
                    "-a", str(workdir), "-t", str(workdir), str(path)],
                   workdir, TIMEOUT_S[mode.name])
        return parse_jt9(out)
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        path.unlink(missing_ok=True)


def _utc_name(slot_ms: int, mode: Mode) -> str:
    import datetime as _dt

    t = _dt.datetime.fromtimestamp(slot_ms / 1000, tz=_dt.UTC)
    # wsprd reads the date and time from the name (yymmdd_hhmm), jt9 the time (hhmmss).
    return t.strftime("%y%m%d_%H%M") if mode.name == "wspr" else t.strftime("%y%m%d_%H%M%S")


def parse_jt9(text: str) -> list[Decoded]:
    found = []
    for line in text.splitlines():
        m = _JT9_LINE.match(line)
        if not m:
            continue
        message = m.group(5).rstrip()
        flag = _AP_FLAG.search(message)
        if flag:
            message = message[: flag.start()].rstrip()
        found.append(Decoded(float(m.group(2)), float(m.group(3)), float(m.group(4)),
                             " ".join(message.split()), flag=flag.group(1) if flag else ""))
    return found


def parse_wsprd(text: str, dial_hz: float) -> list[Decoded]:
    found = []
    for line in text.splitlines():
        m = _WSPRD_LINE.match(line)
        if not m:
            continue
        audio_hz = float(m.group(4)) * 1e6 - dial_hz
        found.append(Decoded(float(m.group(2)), float(m.group(3)), round(audio_hz, 1),
                             " ".join(m.group(6).split()), drift_hz=float(m.group(5))))
    return found


def encode(tools: Tools, mode: Mode, message: str, workdir: Path) -> list[int] | None:
    """Channel symbols of ``message``, or None without encoder (or unknown message)."""
    program = tools.encoder_for(mode)
    if program is None:
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        out = _run([program, message], workdir, 10.0)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_symbols(out, mode)


def parse_symbols(text: str, mode: Mode) -> list[int] | None:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("Channel symbols"):
            continue
        digits: list[int] = []
        for row in lines[i + 1 :]:
            if not row.strip():
                if digits:
                    break
                continue
            if re.search(r"[A-Za-z]", row) and not re.fullmatch(r"[\sR0-9]*", row.strip()):
                if digits:
                    break
                continue  # header row (Sync / Data)
            digits += [int(c) for c in row if c.isdigit()]
            if len(digits) >= mode.symbols:
                break
        if len(digits) == mode.symbols and max(digits) < mode.tones:
            return digits
    return None
