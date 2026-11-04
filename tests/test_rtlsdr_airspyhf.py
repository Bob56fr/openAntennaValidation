"""RTL-SDR Blog V4 and Airspy HF+ drivers, driven through stand-ins of their libraries."""

from __future__ import annotations

import numpy as np
import pytest

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation import installation as inst
from open_antenna_validation.config import (
    AirspyhfConfig,
    Config,
    HardwareConfig,
    RtlsdrConfig,
    SdrSlot,
)
from open_antenna_validation.hardware import HardwareError, create_driver
from open_antenna_validation.hardware.airspyhf import AirspyhfDriver
from open_antenna_validation.hardware.rtlsdr import RtlsdrDriver
from open_antenna_validation.hardware.rtlsdr.driver import (
    SAMPLE_RATES,
    to_complex,
    transfer_bytes,
)
from open_antenna_validation.storage import Database
from tests.fake_sdr_libs import V4, FakeAirspyLib, FakeRtlLib

AIRSPY_SN = 0x3B52AB5DAC5A7E91


def rtl(devices: list[tuple[str, str, str]] | None = None, rate: int = 250_000
        ) -> tuple[RtlsdrDriver, FakeRtlLib]:
    lib = FakeRtlLib(devices if devices is not None else [(*V4, "00000101")])
    return RtlsdrDriver(RtlsdrConfig(), sample_rate=rate, library=lib), lib


def airspy(lna: bool = False, drop_every: int = 0, rate: int = 250_000
           ) -> tuple[AirspyhfDriver, FakeAirspyLib]:
    lib = FakeAirspyLib([AIRSPY_SN], drop_every=drop_every)
    return AirspyhfDriver(AirspyhfConfig(lna=lna), sample_rate=rate, library=lib), lib


# --- RTL-SDR -------------------------------------------------------------------------------


def test_rtl_bytes_to_full_scale_complex() -> None:
    block = to_complex(np.array([0, 255, 255, 0, 128, 127, 7], dtype=np.uint8))
    assert block.dtype == np.complex64 and len(block) == 3  # odd trailing byte ignored
    assert block[0] == pytest.approx(-1 + 1j) and block[1] == pytest.approx(1 - 1j)
    assert abs(block[2]) < 0.01


def test_rtl_transfer_size_is_a_multiple_of_16384() -> None:
    for rate in SAMPLE_RATES:
        size = transfer_bytes(rate)
        assert size % 16384 == 0 and size >= 2 * rate * 0.03


def test_rtl_streams_counts_and_closes() -> None:
    driver, lib = rtl()
    assert driver.availability().available
    driver.set_gain_step(len(driver.gain_steps()) - 1)
    info = driver.open("00000101")
    try:
        assert info.model == "RTL-SDR Blog V4" and info.serial == "00000101"
        # Gain table from the library, sorted, manual mode, digital AGC off.
        assert [s.native["gain_tenth_db"] for s in driver.gain_steps()] == [0, 9, 254, 496]
        assert lib.state["gain"] == 496 and lib.state["manual"] == 1 and lib.state["agc"] == 0
        assert lib.state["rate"] == 250_000 and lib.state["freq"] == 7_100_000
        assert lib.state["buf_len"] == transfer_bytes(250_000)
        block = driver.read_iq(20_000)
        assert len(block) == 20_000 and driver.sample_counter() >= 20_000
        assert np.allclose(block, block[0]) and block[0] == pytest.approx(0.5 + 0.25j, abs=0.01)
        driver.set_ppm(0.4)  # rounds to 0: unchanged for librtlsdr (-2), not an error
        driver.set_ppm(3)
        assert lib.state["corr"] == 3
        driver.set_frequency(28_500_000)
        assert lib.state["freq"] == 28_500_000
        with pytest.raises(HardwareError, match="28,8 MHz"):
            driver.set_frequency(29_000_000)
        assert driver.detect()[0].serial == "00000101"
    finally:
        driver.close()
    assert lib.state["open"] is None and not driver.is_open
    with pytest.raises(HardwareError, match="non ouverte"):
        driver.read_iq(10)


def test_rtl_refuses_other_dongles_and_duplicate_serials() -> None:
    driver, _ = rtl([("Realtek", "RTL2838UHIDIR", "00000001")])
    assert driver.detect()[0].model == "RTL-SDR (RTL2838UHIDIR)"
    with pytest.raises(HardwareError, match="seule la RTL-SDR Blog V4"):
        driver.open()
    driver, _ = rtl([(*V4, "00000001"), (*V4, "00000001")])
    assert all("rtl_eeprom" in d.details["warning"] for d in driver.detect())
    with pytest.raises(HardwareError, match="numéro unique"):
        driver.open("00000001")
    with pytest.raises(HardwareError, match="introuvable"):
        driver.open("12345678")


def test_rtl_has_one_input_and_rejects_others() -> None:
    driver, _ = rtl()
    info = driver.detect()[0]
    assert driver.antennas(info) == [] and driver.effective_antenna(info) == ""
    driver.set_antenna("B")
    with pytest.raises(HardwareError, match="Entrée B indisponible"):
        driver.open()


def test_rtl_sample_rate_choice() -> None:
    driver, _ = rtl()
    assert driver.pick_sample_rate(250_000) == 250_000
    assert driver.pick_sample_rate(300_000) == 1_024_000
    assert driver.pick_sample_rate(3_000_000) == 2_400_000
    with pytest.raises(HardwareError):
        driver.set_sample_rate(500_000)


# --- Airspy HF+ ----------------------------------------------------------------------------


def test_airspy_resolves_rate_gain_and_streams() -> None:
    driver, lib = airspy()
    assert driver.availability().library_version == "libairspyhf 1.8.1"
    assert driver.pick_sample_rate(250_000) == 250_000  # rates unknown before open
    info = driver.open(f"{AIRSPY_SN:x}")
    try:
        assert info.serial == "3B52AB5DAC5A7E91" and info.details["firmware"] == "R3.0.7-CD"
        assert driver.sample_rate() == 256_000 and lib.state["rate"] == 256_000
        steps = driver.gain_steps()
        assert [s.native["att_db"] for s in steps][:2] == [48.0, 42.0]
        assert driver.get_gain_state().native == {"lna": 0, "att_db": 0.0}
        assert lib.state["agc"] == 0 and lib.state["lna"] == 0 and lib.state["att"] == 0.0
        driver.set_gain_step(0)
        assert lib.state["att"] == 48.0
        driver.set_ppm(-1.5)
        assert lib.state["ppb"] == -1500
        block = driver.read_iq(10_000)
        assert block[0] == pytest.approx(0.25 - 0.125j) and driver.dropped_samples() == 0
        with pytest.raises(HardwareError):
            driver.set_sample_rate(250_000)
        driver.set_sample_rate(768_000)  # restarts the stream at the new rate
        assert lib.state["rate"] == 768_000 and len(driver.read_iq(1000)) == 1000
    finally:
        driver.close()
    assert lib.state["open"] is None


def test_airspy_lost_buffers_keep_the_index_in_step() -> None:
    driver, _ = airspy(drop_every=3)
    driver.open()
    try:
        read = 0
        for _ in range(6):
            driver.read_iq(4096)
            read += 4096
        assert driver.dropped_samples() >= 4096
        # The next sample's index counts what was read plus what was lost upstream.
        assert driver.sample_counter() >= read + 4096
    finally:
        driver.close()


def test_airspy_gain_mode_follows_lna_and_unknown_serial() -> None:
    driver, _ = airspy(lna=True)
    assert driver.gain_mode() == "lna1-att"
    assert driver.gain_steps()[0].label == "LNA 1, atténuation 48 dB"
    with pytest.raises(HardwareError, match="introuvable"):
        driver.open("0000000000000001")


# --- configuration and campaigns -------------------------------------------------------------


def test_driver_factory_and_mixed_slots() -> None:
    hw = HardwareConfig(driver="rtlsdr", others=[SdrSlot(serial="X1", driver="airspyhf"),
                                                 SdrSlot(serial="R2")])
    assert isinstance(create_driver(hw), RtlsdrDriver)
    assert hw.for_slot(hw.others[0]).driver == "airspyhf"
    assert hw.for_slot(hw.others[1]).driver == "rtlsdr"
    assert isinstance(create_driver(hw.for_slot(hw.others[0])), AirspyhfDriver)


def test_tuning_stays_in_every_sdr_range(db: Database) -> None:
    v4 = [500_000.0, 28_799_999.0]
    sdrs = [{"state": "running", "bandwidth_hz": 1_920_000.0, "frequency_range": v4,
             "device": {"serial": s, "model": "RTL-SDR Blog V4"}} for s in ("A1", "A2")]
    assert cm.tuning_range(sdrs) == (500_000.0, 28_799_999.0)
    # Fixed frequencies above 28.8 MHz: the tuning moves below while they stay in the band.
    signals = [{"freq_hz": 29_000_000.0, "width_hz": 200.0}]
    tune = cm.dc_free_tuning(signals, 29_000_000.0, 1_920_000.0, cm.tuning_range(sdrs))
    assert tune <= 28_799_999.0
    with pytest.raises(cm.CampaignError, match="hors de sa plage"):
        cm.dc_free_tuning([{"freq_hz": 29_900_000.0, "width_hz": 200.0}], 29_900_000.0,
                          1_920_000.0, cm.tuning_range(sdrs))
    # Automatic band 28.5-29.5 MHz: centre 29 MHz, tuned just below 28.8 MHz.
    a, b = (inst.save_antenna(db, {"type": "dipole", "name": n}) for n in "AB")
    request = {"reception": "simultaneous", "mode": "auto", "low_hz": 28.5e6, "high_hz": 29.5e6,
               "title": "10 m", "duration_s": 3600, "channels": [
                   {"antenna_id": a, "serial": "A1"}, {"antenna_id": b, "serial": "A2"}]}
    uuid = cm.create_campaign(db, Config(), sdrs[0], request, devices=sdrs)
    params = db.query("SELECT params_json FROM campaigns WHERE uuid = ?", (uuid,))[0]
    assert '"sdr_hz": 28799999.0' in params["params_json"]


# --- real hardware (pytest -m hardware) ------------------------------------------------------


def _check_real(driver: RtlsdrDriver | AirspyhfDriver, what: str) -> None:
    status = driver.availability()
    if not status.available:
        pytest.skip(status.message)
    devices = driver.detect()
    if not devices:
        pytest.skip(f"aucun {what} branché")
    try:
        driver.set_sample_rate(driver.pick_sample_rate(250_000))
        driver.open(devices[0].serial)
        driver.set_frequency(7.1e6)
        driver.read_iq(driver.sample_rate() // 2)  # settling
        block = driver.read_iq(driver.sample_rate() // 4)
        assert block.dtype == np.complex64
        power = float(np.mean(np.abs(block) ** 2))
        assert 0 < power < 1, power
        assert driver.sample_counter() > 0
    finally:
        driver.shutdown()


@pytest.mark.hardware
def test_real_rtlsdr_v4_reads_hf() -> None:
    _check_real(RtlsdrDriver(RtlsdrConfig()), "RTL-SDR")


@pytest.mark.hardware
def test_real_airspyhf_reads_hf() -> None:
    _check_real(AirspyhfDriver(AirspyhfConfig()), "Airspy HF+")
