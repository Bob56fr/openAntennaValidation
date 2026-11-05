"""Channel symbols of the simulated digital stations, from the WSJT-X encoders.

Run where WSJT-X provides ft8code, ft4code and wsprcode (Debian package wsjtx):
    python3 scripts/gen_sim_tones.py > src/open_antenna_validation/digital/sim_stations.json
The callsigns end in SIM: fictitious stations of the simulator only.
"""

import json
import subprocess
import sys

sys.path.insert(0, "src")
from open_antenna_validation.digital import wsjtx  # noqa: E402
from open_antenna_validation.digital.modes import MODES  # noqa: E402

PARTNER = "F4SIM"
# call, locator, audio Hz (FT8, FT4, WSPR), SNR in 2500 Hz (dB), slot parity
STATIONS = [
    ("F6SIM", "JN19", 640, 900, 1440, -2, 0),
    ("F5SIM", "JN03", 1010, 1250, 1455, -6, 1),
    ("G4SIM", "IO91", 1180, 1600, 1470, -8, 0),
    ("DL1SIM", "JO62", 1350, 1950, 1485, -7, 1),
    ("I2SIM", "JN45", 1520, 2300, 1500, -9, 0),
    ("EA7SIM", "IM87", 1690, 2650, 1515, -10, 1),
    ("LA9SIM", "JP99", 1860, 550, 1530, -12, 0),
    ("SV1SIM", "KM18", 2030, 2450, 1545, -13, 1),
    ("K1SIM", "FN42", 2200, 1100, 1560, -14, 0),
    ("PY2SIM", "GG66", 2370, 1800, 1575, -16, 1),
    ("ZS6SIM", "KG33", 2540, 2100, 1590, -16, 0),
    ("JA1SIM", "PM95", 2710, 700, 1425, -18, 1),
]


def messages(call: str, grid: str) -> list[str]:
    return [f"CQ {call} {grid}", f"{PARTNER} {call} {grid}", f"{PARTNER} {call} -12",
            f"{PARTNER} {call} RR73"]


tools = wsjtx.find_tools()
out: dict[str, object] = {"partner": PARTNER, "stations": [], "tones": {}}
for call, grid, f8, f4, fw, snr, parity in STATIONS:
    out["stations"].append({"call": call, "grid": grid, "audio_hz": {"ft8": f8, "ft4": f4,
                            "wspr": fw}, "snr_db": snr, "parity": parity})
for name in ("ft8", "ft4", "wspr"):
    mode = MODES[name]
    table = {}
    for call, grid, *_ in STATIONS:
        texts = [f"{call} {grid} 37"] if name == "wspr" else messages(call, grid)
        for text in texts:
            tones = wsjtx.encode(tools, mode, text, __import__("pathlib").Path("/tmp/oav-enc"))
            if tones is None:
                sys.exit(f"{name}: encodeur absent ou message refusé : {text}")
            table[text] = "".join(map(str, tones))
    out["tones"][name] = table
json.dump(out, sys.stdout, indent=1, sort_keys=True)
print()
