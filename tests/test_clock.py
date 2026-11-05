"""Clock offset by SNTP, against a local stand-in server."""

from __future__ import annotations

import socket
import struct
import threading
import time

from open_antenna_validation import clock


def _server(offset_s: float) -> tuple[str, int, threading.Thread]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(5)

    def reply() -> None:
        _data, addr = sock.recvfrom(512)
        now = time.time() + offset_s + clock.NTP_EPOCH_OFFSET
        sec, frac = int(now), int((now % 1) * 2**32)
        packet = bytes([0x24, 2]) + bytes(30) + struct.pack("!IIII", sec, frac, sec, frac)
        sock.sendto(packet, addr)
        sock.close()

    t = threading.Thread(target=reply, daemon=True)
    t.start()
    return "127.0.0.1", sock.getsockname()[1], t


def test_sntp_offset_and_verdict() -> None:
    host, port, t = _server(-2.5)
    state = clock.check(host, 0.5, 0, port=port)
    t.join()
    assert state["ok"] is False and abs(float(state["offset_s"]) + 2.5) < 0.1  # type: ignore[arg-type]
    assert "2,5 s d'avance" in str(state["message"])


def test_unreachable_server_is_not_an_error() -> None:
    state = clock.check("127.0.0.1", 0.5, 0)  # nothing listens on port 123
    assert state["ok"] is None and "non vérifiée" in str(state["message"])
