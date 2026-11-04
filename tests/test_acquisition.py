from __future__ import annotations

import queue
import threading
from typing import Any

import pytest

from open_antenna_validation.acquisition import Acquisition, run_acquisition, wait_for
from open_antenna_validation.config import Config, HardwareConfig
from open_antenna_validation.hardware import HardwareError, SdrDriver, create_driver
from open_antenna_validation.service import Station


def sim_config() -> Config:
    config = Config()
    config.hardware.driver = "simulated"
    config.hardware.simulated.realtime = False
    config.spectrum.fps = 10.0
    return config


def test_worker_reports_opened_then_spectra_and_switch() -> None:
    config = sim_config()
    events: queue.Queue[dict[str, Any]] = queue.Queue()
    commands: queue.Queue[tuple[str, Any]] = queue.Queue()
    stop = threading.Event()
    worker = threading.Thread(
        target=run_acquisition,
        args=(config.hardware, config.receiver, config.spectrum, commands, events,
              lambda: not stop.is_set()),
        daemon=True,
    )
    worker.start()
    try:
        opened = events.get(timeout=5)
        assert opened["type"] == "opened"
        assert opened["antennas"] == ["A", "B"]
        assert opened["antenna"] == "A"
        spectrum = next(e for e in iter(lambda: events.get(timeout=5), None)
                        if e["type"] == "spectrum")
        assert len(spectrum["bins_dbfs"]) == config.spectrum.display_points
        assert spectrum["f_start_hz"] == pytest.approx(7.1e6 - 125_000)
        assert spectrum["floor_dbfs"] < -60
        commands.put(("antenna", "B"))
        switch = next(e for e in iter(lambda: events.get(timeout=5), None) if e["type"] == "switch")
        assert switch["antenna"] == "B" and switch["sample"] > 0
        later = next(e for e in iter(lambda: events.get(timeout=5), None)
                     if e["type"] == "spectrum")
        assert later["antenna"] == "B"
        commands.put(("stop", None))
        worker.join(5)
        assert not worker.is_alive()
    finally:
        stop.set()


def test_worker_reports_unavailable_driver() -> None:
    def broken(_: HardwareConfig) -> SdrDriver:
        driver = create_driver(sim_config().hardware)

        def fail(serial: str | None = None) -> Any:
            raise HardwareError("Aucun SDR détecté.")

        driver.open = fail  # type: ignore[method-assign]
        return driver

    config = sim_config()
    events: queue.Queue[dict[str, Any]] = queue.Queue()
    run_acquisition(config.hardware, config.receiver, config.spectrum, queue.Queue(), events,
                    lambda: True, broken)
    event = events.get(timeout=1)
    assert event == {"type": "error", "fatal": True, "message": "Aucun SDR détecté.",
                     "help_url": None}


def test_child_process_runs_and_stops() -> None:
    config = sim_config()
    received: list[dict[str, Any]] = []
    acq = Acquisition(config.hardware, config.receiver, config.spectrum, received.append)
    acq.start()
    try:
        assert wait_for(lambda: any(e["type"] == "spectrum" for e in received), 30)
    finally:
        acq.stop()
    assert not acq.running
    assert received[0]["type"] == "opened"


def test_station_records_identity_and_switch(station: Station) -> None:
    station.startup()
    assert wait_for(lambda: station.receiver.get("state") == "running", 5)
    assert station.identity is not None
    assert station.identity.identity == "simulated:Simulateur:SIM0001:antA:gain"
    station.set_antenna("B")
    assert wait_for(lambda: station.receiver.get("antenna") == "B", 5)
    rows = station.db.query("SELECT detail_json FROM events WHERE type = 'antenna_switch'")
    assert len(rows) == 1
    assert station.config.hardware.antenna == "B"
    with pytest.raises(HardwareError):
        station.set_antenna("C")
    with pytest.raises(HardwareError):
        station.set_frequency(145e6)
    station.set_frequency(6.66e6)
    assert wait_for(lambda: (station.last_spectrum or {}).get("center_hz") == 6.66e6, 5)
