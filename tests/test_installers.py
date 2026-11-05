"""Installer scripts: encodings, PowerShell syntax, and the Python snippets they embed."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from open_antenna_validation.config import CONFIG_FILENAME, Config, load_config, save_config

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "install" / "windows"
LINUX = ROOT / "install" / "linux"


def ps1_blocks() -> list[str]:
    """Python code of the single-quoted here-strings of install.ps1, in order."""
    text = (WINDOWS / "install.ps1").read_text(encoding="utf-8-sig")
    return re.findall(r"@'\r?\n(.*?)\r?\n'@", text, re.S)


def sh_blocks() -> list[str]:
    text = (LINUX / "install.sh").read_text(encoding="utf-8")
    return re.findall(r"<<'PY'\n(.*?)\nPY\n", text, re.S)


def run_snippet(code: str, home: Path, *args: str) -> str:
    env = {**os.environ, "OAV_HOME": str(home)}
    done = subprocess.run([sys.executable, "-", *args], input=code, text=True,
                          capture_output=True, env=env, check=True)
    return done.stdout.strip()


def test_script_encodings() -> None:
    for path in WINDOWS.glob("*.ps1"):
        # Windows PowerShell 5.1 reads a file without BOM in the ANSI code page: the French
        # messages would be garbled.
        assert path.read_bytes().startswith(b"\xef\xbb\xbf"), path.name
    for path in WINDOWS.glob("*.cmd"):
        path.read_bytes().decode("ascii")  # cmd.exe code page: ASCII only
    for path in LINUX.glob("*.sh"):
        assert b"\r\n" not in path.read_bytes(), path.name
    for path in [*WINDOWS.iterdir(), *LINUX.glob("*.sh")]:
        # A "\b" written through Python once became a backspace in a Windows path.
        assert not any(c < 9 or 13 < c < 32 for c in path.read_bytes()), path.name


def test_pinned_downloads_have_a_sha256() -> None:
    text = (WINDOWS / "install.ps1").read_text(encoding="utf-8-sig")
    hashes = re.findall(r'Sha256 = "([^"]*)"', text)
    assert len(hashes) == 2 and all(re.fullmatch(r"[0-9A-F]{64}", h) for h in hashes)


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"),
                    reason="Windows PowerShell absent")
def test_powershell_scripts_parse() -> None:
    for path in WINDOWS.glob("*.ps1"):
        command = (
            "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{path}', [ref]$null, [ref]$e); $e.Count"
        )
        out = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == "0", (path.name, out)


def _station(home: Path, **paths: str) -> Path:
    config = Config()
    config.hardware.driver = "airspyhf"
    config.hardware.rtlsdr.library_path = paths.get("rtlsdr", "")
    config.hardware.airspyhf.library_path = paths.get("airspyhf", "")
    config.hardware.sdrplay.library_path = paths.get("sdrplay", "")
    path = home / CONFIG_FILENAME
    save_config(config, path)
    return path


def test_windows_snippets_read_drivers_and_write_paths(tmp_path: Path) -> None:
    reader, writer = ps1_blocks()
    # Imported from Linux: the Linux paths are not Windows libraries of this station.
    path = _station(tmp_path, rtlsdr="/usr/local/lib/librtlsdr.so.0",
                    sdrplay="/usr/local/lib/libsdrplay_api.so")
    assert run_snippet(reader, tmp_path, str(path)) == "airspyhf"
    run_snippet(writer, tmp_path, "8661", "-", r"C:\oav\lib\airspyhf\airspyhf.dll")
    config = load_config(path)
    assert config.web.port == 8661
    assert config.hardware.airspyhf.library_path == r"C:\oav\lib\airspyhf\airspyhf.dll"
    assert config.hardware.rtlsdr.library_path == "" and config.hardware.sdrplay.library_path == ""
    # A library installed by an earlier run stays part of the station.
    _station(tmp_path, rtlsdr=r"C:\oav\lib\rtlsdr\rtlsdr.dll")
    assert run_snippet(reader, tmp_path, str(path)) == "airspyhf,rtlsdr"


def test_linux_snippets_read_drivers_and_write_paths(tmp_path: Path) -> None:
    reader, writer = sh_blocks()
    path = _station(tmp_path, rtlsdr=r"C:\oav\lib\rtlsdr\rtlsdr.dll",
                    airspyhf="/usr/local/lib/libairspyhf.so.1.8.1")
    assert run_snippet(reader, tmp_path, str(path)) == "airspyhf"
    run_snippet(writer, tmp_path, "8662", "", "")
    config = load_config(path)
    assert config.web.port == 8662
    assert config.hardware.airspyhf.library_path == "/usr/local/lib/libairspyhf.so.1.8.1"
    assert config.hardware.rtlsdr.library_path == ""
