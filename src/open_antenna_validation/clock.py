"""Offset of the station clock (cahier des charges, section 16): the FT8/WSPR slots and the
alignment of several SDRs need a synchronised clock. A minimal SNTP client (RFC 4330),
standard library only; the service checks every ``ntp_interval_s`` and logs changes.
"""

from __future__ import annotations

import socket
import struct
import time

NTP_EPOCH_OFFSET = 2_208_988_800  # seconds from 1900-01-01 to 1970-01-01
TIMEOUT_S = 3.0


def _to_unix(seconds: int, fraction: int) -> float:
    return seconds - NTP_EPOCH_OFFSET + fraction / 2**32


def sntp_offset(server: str, timeout_s: float = TIMEOUT_S, port: int = 123) -> float:
    """Clock offset in seconds (positive: the station is late), from one SNTP exchange."""
    request = b"\x1b" + 47 * b"\0"  # LI 0, version 3, mode 3 (client)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_s)
        t1 = time.time()
        sock.sendto(request, (server, port))
        data, _ = sock.recvfrom(512)
        t4 = time.time()
    if len(data) < 48:
        raise OSError("réponse NTP trop courte")
    mode = data[0] & 0x7
    stratum = data[1]
    if mode not in (4, 5) or stratum == 0:
        raise OSError("réponse NTP refusée par le serveur")
    rx_s, rx_f, tx_s, tx_f = struct.unpack("!IIII", data[32:48])
    t2, t3 = _to_unix(rx_s, rx_f), _to_unix(tx_s, tx_f)
    return ((t2 - t1) + (t3 - t4)) / 2


def check(server: str, tolerance_s: float, now_ms: int, port: int = 123) -> dict[str, object]:
    """State shown on the dashboard: offset, verdict, or the reason it is unknown."""
    try:
        offset = sntp_offset(server, port=port)
    except OSError as exc:
        return {"server": server, "checked_ms": now_ms, "offset_s": None, "ok": None,
                "message": f"Horloge non vérifiée ({server} injoignable : {exc})."}
    ok = abs(offset) <= tolerance_s
    way = "de retard" if offset > 0 else "d'avance"
    amount = f"{abs(offset):.1f}".replace(".", ",")
    message = "" if ok else (
        f"L'horloge de la station a {amount} s {way} : les créneaux FT8/WSPR et "
        "l'alignement des SDR en souffrent. Activez la synchronisation de l'heure (NTP) "
        "du système.")
    return {"server": server, "checked_ms": now_ms, "offset_s": round(offset, 3), "ok": ok,
            "message": message}
