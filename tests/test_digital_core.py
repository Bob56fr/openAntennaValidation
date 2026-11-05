"""Digital modes (P10): messages, geography, demodulator, guided measurement, tracks."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from open_antenna_validation.config import DigitalConfig
from open_antenna_validation.digital import demod, measure, messages, synth, wsjtx
from open_antenna_validation.digital.modes import AUDIO_RATE, MODES, slot_parity
from open_antenna_validation.digital.tracks import SlotMessage, TrackBook
from open_antenna_validation.storage import Database

FT8 = MODES["ft8"]
CTY = """France:                   14:  27:  EU:   46.00:    -2.00:    -1.0:  F:
    F,HW,TM,=F4FET/LH;
Germany:                  14:  28:  EU:   51.00:   -10.00:    -1.0:  DL:
    DA,DL,DM;
"""


# --- messages ---------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "call", "grid", "end"), [
    ("CQ F4ABC JN18", "F4ABC", "JN18", False),
    ("CQ DX DL1ABC JO62", "DL1ABC", "JO62", False),
    ("F4ABC DL1ABC JO62", "DL1ABC", "JO62", False),
    ("DL1ABC F4ABC -12", "F4ABC", "", False),
    ("DL1ABC F4ABC R JN18", "F4ABC", "JN18", False),
    ("DL1ABC F4ABC RR73", "F4ABC", "", True),
    ("F4ABC DL1ABC 73", "DL1ABC", "", True),
    ("TNX 73 GL", "", "", False),
])
def test_ft_sender(text: str, call: str, grid: str, end: bool) -> None:
    s = messages.parse_ft(text)
    assert (s.call, s.grid, s.end_of_qso) == (call, grid, end)


def test_hashed_and_wspr_senders() -> None:
    s = messages.parse_ft("F4ABC <PJ4/K1ABC> -10")
    assert s.call == "PJ4/K1ABC" and s.hashed and not s.unresolved
    assert messages.parse_ft("F4ABC <...> RR73").unresolved
    w = messages.parse_wspr("F4ABC JN18 30")
    assert (w.call, w.grid, w.power_dbm) == ("F4ABC", "JN18", 30)
    w = messages.parse_wspr("<PJ4/K1ABC> FK52UD 37")
    assert w.call == "PJ4/K1ABC" and w.grid == "FK52UD" and w.hashed


def test_geography_and_propagation() -> None:
    lat, lon = messages.grid_to_latlon("JN18EU")
    assert lat == pytest.approx(48.85, abs=0.05) and lon == pytest.approx(2.375, abs=0.05)
    d, az = messages.distance_azimuth(*messages.grid_to_latlon("JN18"),
                                      *messages.grid_to_latlon("FN42"))
    assert 5300 < d < 5700 and 280 < az < 300
    assert messages.propagation_class(30, 60, 400, 1000, 3800) == "ground"
    assert messages.propagation_class(700, 60, 400, 1000, 3800) == "skip"
    assert messages.propagation_class(5000, 60, 400, 1000, 3800) == "dxn"
    # Single hop at 300 km: about 30° at 1000 km, low at 3500 km, none beyond the hop.
    assert messages.takeoff_angle(1000, 300) == pytest.approx(28.5, abs=1.5)
    assert messages.takeoff_angle(8000, 300) is None


def test_country_file() -> None:
    cty = messages.CountryFile(CTY)
    assert cty.lookup("F4ABC").name == "France"  # type: ignore[union-attr]
    assert cty.lookup("DL1ABC/P").name == "Germany"  # type: ignore[union-attr]
    assert cty.lookup("F/DL5SE").name == "France"  # type: ignore[union-attr]
    assert cty.lookup("F4FET/LH").name == "France"  # type: ignore[union-attr]
    assert cty.lookup("F4ABC").lon == 2.0  # type: ignore[union-attr]  # east positive
    assert cty.lookup("ZZ9ZZ") is None


# --- decoder output ---------------------------------------------------------------------


def test_decoder_output_parsing() -> None:
    out = wsjtx.parse_jt9("000015 -12  0.1 1200 ~  CQ F4ABC JN18\n"
                          "000015   3 -0.4 2957 ~  CQ 6L6LRW AP30          ?\n"
                          "000015  -5  0.2  640 +  F4ABC DL1ABC RR73       a2\n"
                          "<DecodeFinished>   0   3        0\n")
    assert [(d.message, d.snr_db, d.audio_hz, d.flag) for d in out] == [
        ("CQ F4ABC JN18", -12, 1200, ""), ("CQ 6L6LRW AP30", 3, 2957, "?"),
        ("F4ABC DL1ABC RR73", -5, 640, "a2")]
    w = wsjtx.parse_wsprd("1200 -22  0.1  14.097082  0  F4ABC JN18 30 \n", 14.0956e6)
    assert w[0].message == "F4ABC JN18 30" and w[0].audio_hz == pytest.approx(1482.0)
    ft8 = ("Channel symbols (79 tones):\n  Sync               Data\n"
           "3140652 00000000100522503101052252027 3140652 65436755344372064376615313244 "
           "3140652\n")
    assert len(wsjtx.parse_symbols(ft8, FT8) or []) == 79


# --- signal processing ------------------------------------------------------------------


def test_guided_level_is_accurate_near_the_threshold() -> None:
    rng = np.random.default_rng(5)
    for name, snr in (("ft8", -20), ("wspr", -24)):
        mode = MODES[name]
        tones = list(rng.integers(0, mode.tones, mode.symbols))
        n = int(mode.slot_s * AUDIO_RATE)
        amp = math.sqrt(2 * 2500 / (AUDIO_RATE / 2) * 10 ** (snr / 10))
        w = synth.waveform(tones, mode, 1300.0, amp)
        audio = rng.normal(0, 1.0, n)
        start = int((mode.start_s + 0.2) * AUDIO_RATE)
        audio[start : start + len(w)] += w[: n - start]
        level = measure.guided_level(audio, mode, tones, 1300.0, 0.2)
        assert level is not None and level.level_db is not None
        assert level.level_db == pytest.approx(10 * math.log10(amp**2), abs=1.0)


def test_usb_demodulator_keeps_frequency_and_level() -> None:
    rate, sdr_hz, dial = 250_000.0, 7.050e6, 7.074e6
    d = demod.UsbDemodulator(rate, dial + demod.AUDIO_CENTER_HZ - sdr_hz)
    n = int(4 * rate)
    t = np.arange(n) / rate
    tone_offset = dial - sdr_hz + 1000.0  # audio 1000 Hz, -40 dBFS
    iq = 0.01 * np.exp(2j * np.pi * tone_offset * t)
    chunk = 25_000
    for i in range(0, n, chunk):
        d.push(iq[i : i + chunk], i)
    d.push(np.zeros(2 * d.n, dtype=complex), n)
    audio = demod.to_audio(d.take(0, n), d.out_rate, d.residual_hz, 4.0)
    spec = np.abs(np.fft.rfft(audio[AUDIO_RATE : 3 * AUDIO_RATE])) ** 2
    assert np.argmax(spec) * AUDIO_RATE / (2 * AUDIO_RATE) == pytest.approx(1000.0, abs=1.0)
    power_db = 10 * np.log10(np.mean(audio[AUDIO_RATE : 3 * AUDIO_RATE] ** 2))
    assert power_db + demod.IQ_TO_AUDIO_DB == pytest.approx(-40.0, abs=0.2)


# --- tracks -----------------------------------------------------------------------------


def _book(db: Database) -> tuple[TrackBook, int]:
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO campaigns (uuid, reception_mode, measure_mode, low_hz, high_hz, state,"
            " created_ms, n_channels) VALUES ('c1', 'simultaneous', 'fixed', 7e6, 7.2e6,"
            " 'running', 0, 2)")
        cid = int(cur.lastrowid or 0)
    cfg = DigitalConfig()
    return TrackBook(db, cid, FT8, cfg, "JN18", "40 m", messages.CountryFile(CTY)), cid


def _msg(text: str, hz: float, measured: bool = True) -> SlotMessage:
    return SlotMessage(text, messages.parse_ft(text), hz, measured)


def _slot(k: int) -> int:
    return 1_700_000_010_000 - 1_700_000_010_000 % 15_000 + k * 15_000


def test_track_follows_a_qso_and_validates(db: Database) -> None:
    book, cid = _book(db)
    seq = ["CQ DL1ABC JO62", "F4XYZ DL1ABC JO62", "F4XYZ DL1ABC R-10", "F4XYZ DL1ABC RR73"]
    for i, text in enumerate(seq):
        book.update(_slot(2 * i), [_msg(text, 1200.0 + i)])  # same parity, small drift
    rows = db.query("SELECT * FROM tracks WHERE campaign_id = ?", (cid,))
    assert len(rows) == 1
    t = rows[0]
    assert t["state"] == "validated" and t["callsign"] == "DL1ABC" and t["locator"] == "JO62"
    assert t["propagation_class"] == "skip" and 700 < t["distance_km"] < 950
    assert t["takeoff_deg"] is not None and t["n_measures"] == 4
    # Silence: the track closes, still validated.
    book.update(_slot(30), [])
    assert db.query("SELECT closed FROM tracks")[0]["closed"] == 1


def test_track_rejections(db: Database) -> None:
    book, _ = _book(db)
    book.update(_slot(0), [_msg("CQ DL1ABC JO62", 1200.0)])
    book.update(_slot(1), [_msg("F4XYZ DL1ABC -05", 1200.0)])        # other parity
    book.update(_slot(4), [_msg("CQ DL2XYZ JO40", 800.0)])
    book.update(_slot(6), [_msg("F4XYZ DL2XYZ -07", 900.0)])         # frequency jump
    book.update(_slot(8), [_msg("CQ F4AAA JN18", 1500.0), _msg("CQ F4BBB JN18", 1530.0)])
    book.update(_slot(9), [_msg("F4XYZ <...> RR73", 2000.0)])
    reasons: dict[str, set[str]] = {}
    for r in db.query("SELECT callsign, reject_reason FROM tracks WHERE state = 'rejected'"):
        reasons.setdefault(r["callsign"], set()).add(r["reject_reason"])
    assert "parity_change" in reasons["DL1ABC"]
    assert "frequency_jump" in reasons["DL2XYZ"]
    assert reasons["<...>"] == {"unresolved_hash"}
    # The country gives an approximate position without locator.
    approx = db.query("SELECT approximate, country FROM tracks WHERE callsign = 'DL1ABC' "
                      "ORDER BY id DESC LIMIT 1")[0]
    assert approx["country"] == "Germany"


def test_overlap_rejects_both(db: Database) -> None:
    book, _ = _book(db)
    a, b = _msg("CQ F4AAA JN18", 1500.0), _msg("CQ F4BBB JN18", 1530.0)
    a.overlap = b.overlap = True
    book.update(_slot(0), [a, b])
    states = {r["callsign"]: (r["state"], r["reject_reason"]) for r in db.query(
        "SELECT callsign, state, reject_reason FROM tracks")}
    assert states == {"F4AAA": ("rejected", "overlap"), "F4BBB": ("rejected", "overlap")}


def test_slot_parity() -> None:
    assert slot_parity(_slot(0), FT8) != slot_parity(_slot(1), FT8)


@pytest.mark.skipif(not wsjtx.find_tools().has("jt9"), reason="WSJT-X absent")
def test_wsjtx_round_trip(tmp_path: Path) -> None:
    """Our FT8 synthesis, decoded by the real jt9 at -16 dB (2500 Hz)."""
    tools = wsjtx.find_tools()
    tones = wsjtx.encode(tools, FT8, "CQ F4ABC JN18", tmp_path)
    if tones is None:
        pytest.skip("ft8code absent")
    rng = np.random.default_rng(3)
    audio = rng.normal(0, 1.0, int(15 * AUDIO_RATE))
    amp = math.sqrt(2 * 2500 / (AUDIO_RATE / 2) * 10 ** (-16 / 10))
    w = synth.waveform(tones, FT8, 1200.0, amp)
    audio[6000 : 6000 + len(w)] += w
    out = wsjtx.decode(tools, FT8, audio, _slot(0), 7.074e6, tmp_path)
    assert [(d.message, d.audio_hz) for d in out] == [("CQ F4ABC JN18", 1200.0)]
    assert out[0].snr_db == pytest.approx(-16, abs=1.5)
