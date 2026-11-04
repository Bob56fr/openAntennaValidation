from __future__ import annotations

import numpy as np
import pytest

from open_antenna_validation.config import SdrplayConfig, SimulatedConfig, SimulatedSignal
from open_antenna_validation.dsp.measure import Spectrum, db
from open_antenna_validation.hardware import DeviceInfo, HardwareError, hardware_identity
from open_antenna_validation.hardware.identity import record_hardware
from open_antenna_validation.hardware.sdrplay import SdrplayDriver, api
from open_antenna_validation.hardware.sdrplay.driver import LNA_STATES_HF, bandwidth_for, model_name
from open_antenna_validation.hardware.simulated import SimulatedDriver
from open_antenna_validation.storage import Database

RATE = 250_000


def sim(**overrides: object) -> SimulatedDriver:
    config = SimulatedConfig(realtime=False, fading_db=0.0, **overrides)  # type: ignore[arg-type]
    driver = SimulatedDriver(config)
    driver.set_sample_rate(RATE)
    driver.open()
    return driver


def test_simulated_driver_reads_iq_and_counts_samples() -> None:
    driver = sim()
    assert driver.sample_counter() == 0
    block = driver.read_iq(4096)
    assert block.dtype == np.complex64 and len(block) == 4096
    assert driver.sample_counter() == 4096
    assert driver.dropped_samples() == 0
    assert driver.effective_antenna(driver.detect()[0]) == "A"


def test_simulated_carrier_level_and_antenna_b_offset() -> None:
    carrier = SimulatedSignal(offset_hz=-62_000.0, kind="carrier", dbfs=-60.0)
    driver = sim(signals=[carrier], antenna_b_signal_db=-4.0, antenna_b_noise_db=2.0)
    driver.set_gain_step(0)
    spec = Spectrum(4096, RATE)

    def measure() -> tuple[float, float]:
        powers = spec.frames(driver.read_iq(4096 * 20)).mean(axis=0)
        level = float(db(spec.band_power(powers, -62_000, 200)))
        return level, float(db(spec.noise_floor_bin(powers)))

    level_a, floor_a = measure()
    assert level_a == pytest.approx(-60.0, abs=0.3)
    driver.set_antenna("B")
    driver.read_iq(RATE // 10)  # past the switch latency and glitch
    level_b, floor_b = measure()
    assert level_b - level_a == pytest.approx(-4.0, abs=0.3)
    assert floor_b - floor_a == pytest.approx(2.0, abs=0.3)


def test_simulated_switch_has_latency_and_glitch() -> None:
    driver = sim(signals=[], switch_latency_ms=2.0, switch_glitch_db=10.0, switch_settle_ms=1.0,
                 antenna_b_noise_db=0.0)
    driver.read_iq(1000)
    cmd = driver.set_antenna("B")
    assert cmd is not None and cmd.antenna == "B" and cmd.sample == 1000
    block = driver.read_iq(2000)
    power = np.abs(block) ** 2
    latency = int(0.002 * RATE)  # 500 samples
    before = float(db(power[:latency - 10].mean()))
    glitch = float(db(power[latency : latency + 20].mean()))
    after = float(db(power[latency + 400 :].mean()))
    assert glitch - before > 6.0
    assert after - before == pytest.approx(0.0, abs=0.5)


def test_simulated_set_antenna_before_open_returns_none() -> None:
    driver = SimulatedDriver(SimulatedConfig(realtime=False))
    assert driver.set_antenna("B") is None
    driver.open()
    assert driver.effective_antenna(driver.detect()[0]) == "B"
    with pytest.raises(HardwareError):
        driver.set_antenna("C")


def test_simulated_ft8_only_in_even_slots() -> None:
    ft8 = SimulatedSignal(offset_hz=10_000.0, kind="ft8", dbfs=-50.0)
    driver = sim(signals=[ft8], noise_dbfs=-120.0)
    driver.set_gain_step(0)
    rate = 62_500
    driver.set_sample_rate(rate)  # restarts the stream at t = 0
    on = driver.read_iq(rate * 5)  # 0-5 s: slot 0 transmits
    driver.read_iq(rate * 10)
    off = driver.read_iq(rate * 5)  # 15-20 s: slot 1 is silent
    assert float(db(np.mean(np.abs(on) ** 2))) > float(db(np.mean(np.abs(off) ** 2))) + 30


def test_gain_step_out_of_range() -> None:
    driver = SimulatedDriver(SimulatedConfig())
    with pytest.raises(HardwareError):
        driver.set_gain_step(len(driver.gain_steps()))


def test_sdrplay_gain_steps_without_library() -> None:
    driver = SdrplayDriver(SdrplayConfig(lna_state=3))
    steps = driver.gain_steps()
    assert len(steps) == 40
    assert steps[0].native == {"lna_state": 3, "grdb": 59}  # lowest gain first
    assert steps[-1].native == {"lna_state": 3, "grdb": 20}
    assert driver.gain_mode() == "lna3-grdb"


def test_sdrplay_rates_bandwidth_and_frequency() -> None:
    driver = SdrplayDriver(SdrplayConfig())
    assert 250_000 in driver.supported_sample_rates()
    assert driver.frequency() == pytest.approx(7.1e6)
    assert driver.instant_bandwidth() == 200_000  # IF filter at 250 kS/s
    with pytest.raises(HardwareError):
        driver.set_sample_rate(240_000)
    with pytest.raises(HardwareError):
        driver.set_frequency(10.0)


def test_sdrplay_helpers() -> None:
    assert model_name(api.HW_RSP2) == "RSP2/RSP2pro"
    assert bandwidth_for(250_000) == api.BW_0_200
    assert bandwidth_for(2_000_000) == api.BW_1_536
    # RSPII_NUM_LNA_STATES in sdrplay_api_rsp2.h
    assert LNA_STATES_HF[api.HW_RSP2] == 9


def test_sdrplay_set_antenna_closed_returns_none() -> None:
    driver = SdrplayDriver(SdrplayConfig())
    assert driver.set_antenna("B") is None


def test_sdrplay_missing_library_reports_unavailable() -> None:
    driver = SdrplayDriver(SdrplayConfig(library_path="/nonexistent/libsdrplay_api.so"))
    if driver.availability().available:
        pytest.skip("API SDRplay installée sur cette machine")
    availability = driver.availability()
    assert "SDRplay" in availability.message
    assert availability.help_url


def test_ctypes_layout_matches_c_alignment() -> None:
    assert api.DeviceT.dev.offset % 8 == 0
    assert api.GainT.minGr.offset == 8
    assert api.DevParamsT.fsFreq.offset == 8


def test_hardware_identity_change(db: Database) -> None:
    a = DeviceInfo(driver="sdrplay", model="RSP2/RSP2pro", serial="111")
    b = DeviceInfo(driver="sdrplay", model="RSP2/RSP2pro", serial="222")
    first = record_hardware(db, a, "A", "lna0-grdb")
    assert not first.changed
    assert first.identity == "sdrplay:RSP2/RSP2pro:111:antA:lna0-grdb"
    assert first.identity == hardware_identity(a, "A", "lna0-grdb")
    assert not record_hardware(db, a, "A", "lna0-grdb").changed
    assert record_hardware(db, a, "A", "lna4-grdb").changed
    assert record_hardware(db, b, "A", "lna4-grdb").changed
    assert len(db.query("SELECT type FROM events WHERE type = 'hardware_change'")) == 2


def rsp(hw_ver: int) -> DeviceInfo:
    return DeviceInfo(driver="sdrplay", model=model_name(hw_ver), serial="1",
                      details={"hw_ver": str(hw_ver)})


def test_sdrplay_antennas_by_model() -> None:
    driver = SdrplayDriver(SdrplayConfig())
    assert driver.antennas(rsp(api.HW_RSP2)) == ["A", "B"]
    assert driver.antennas(rsp(api.HW_RSPDX)) == ["A", "B", "C"]
    assert driver.antennas(rsp(api.HW_RSP1A)) == []
    assert driver.effective_antenna(rsp(api.HW_RSP2)) == "A"
    with pytest.raises(HardwareError):
        driver.check_antenna(rsp(api.HW_RSP2), "C")


@pytest.mark.hardware
def test_real_sdrplay_reads_both_inputs() -> None:
    driver = SdrplayDriver(SdrplayConfig())
    assert driver.availability().available, driver.availability().message
    assert driver.detect(), "aucun SDRplay branché"
    try:
        driver.open()
        driver.set_frequency(7.1e6)
        for antenna in ("A", "B"):
            cmd = driver.set_antenna(antenna)
            assert cmd is not None
            driver.read_iq(50_000)
            block = driver.read_iq(25_000)
            assert block.dtype == np.complex64
            assert 0 < float(np.mean(np.abs(block) ** 2)) < 1
        assert driver.sample_counter() > 0
    finally:
        driver.shutdown()
