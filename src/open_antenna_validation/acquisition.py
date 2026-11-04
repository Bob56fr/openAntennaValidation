"""Acquisition: one worker per SDR reads the IQ stream and publishes spectra.

The worker owns the driver. It runs in its own process (cahier des charges, section 4:
one acquisition process per SDR, so that several SDRs use several cores), or in a
thread for tests. Both variants share the same loop and talk through two queues:

- commands in: ``("antenna", "B")``, ``("frequency", 7.1e6)``, ``("gain", 20)``, ``("stop", None)``;
- events out: plain dicts (picklable), ``type`` = opened, spectrum, switch, error, closed.

The child process stops by itself if the parent process disappears.
"""

from __future__ import annotations

import logging
import multiprocessing
import multiprocessing.process
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Protocol

import numpy as np

from open_antenna_validation import bench, detect
from open_antenna_validation.config import HardwareConfig, ReceiverConfig, SpectrumConfig
from open_antenna_validation.dsp.measure import Spectrum, clipping_ratio, db
from open_antenna_validation.engine import CampaignRunner
from open_antenna_validation.hardware import HardwareError, SdrDriver, create_driver
from open_antenna_validation.timeutil import now_ms

log = logging.getLogger(__name__)

Event = dict[str, Any]
Command = tuple[str, Any]
PARENT_CHECK_S = 1.0
ERROR_RETRY_S = 5.0


class _Queue(Protocol):
    def put(self, item: Any) -> None: ...

    def get(self, block: bool = ..., timeout: float | None = ...) -> Any: ...

    def get_nowait(self) -> Any: ...


def _display(powers_db: np.ndarray, points: int) -> list[float]:
    """Reduce FFT bins to ``points`` values by max-hold, rounded for a light payload."""
    size = len(powers_db)
    group = max(1, size // points)
    trimmed = powers_db[: (size // group) * group].reshape(-1, group).max(axis=1)
    return [round(float(v), 1) for v in trimmed]


def run_acquisition(
    hardware: HardwareConfig,
    receiver: ReceiverConfig,
    spectrum_cfg: SpectrumConfig,
    commands: _Queue,
    events: _Queue,
    keep_running: Callable[[], bool],
    driver_factory: Callable[[HardwareConfig], SdrDriver] = create_driver,
) -> None:
    """Worker loop. Returns after a ``stop`` command or when ``keep_running()`` is False."""
    driver = driver_factory(hardware)
    try:
        availability = driver.availability()
        if not availability.available:
            events.put({"type": "error", "fatal": True, "message": availability.message,
                        "help_url": availability.help_url})
            return
        found = driver.detect()
        if not found or (hardware.serial and all(d.serial != hardware.serial for d in found)):
            # Not an error: the station keeps serving its data and retries later.
            wanted = f" n° {hardware.serial}" if hardware.serial else ""
            events.put({"type": "error", "fatal": True, "absent": True, "help_url": None,
                        "message": f"Aucun SDR{wanted} branché."})
            driver.shutdown()
            return
        driver.set_sample_rate(driver.pick_sample_rate(hardware.sample_rate))
        device = driver.open(hardware.serial or None)
        driver.set_ppm(hardware.ppm)
        driver.set_bias_tee(hardware.bias_tee)
        driver.set_frequency(receiver.center_frequency_hz + receiver.dc_offset_hz)
    except HardwareError as exc:
        events.put({"type": "error", "fatal": True, "message": str(exc),
                    "help_url": getattr(exc, "help_url", None)})
        driver.shutdown()
        return

    center = receiver.center_frequency_hz
    rate = driver.sample_rate()
    events.put({
        "type": "opened",
        "device": asdict(device),
        "library": availability.library_version,
        "antennas": driver.antennas(device),
        "antenna": driver.effective_antenna(device),
        "gain_steps": [s.label for s in driver.gain_steps()],
        "gain_index": driver.get_gain_state().step_index,
        "gain_mode": driver.gain_mode(),
        "sample_rate": rate,
        "bandwidth_hz": driver.instant_bandwidth(),
        "frequency_range": list(driver.frequency_range()),
        "center_hz": center,
    })

    spec = Spectrum(spectrum_cfg.fft_size, rate)
    group = max(1, spec.size // spectrum_cfg.display_points)
    block = max(spectrum_cfg.fft_size, int(rate * spectrum_cfg.block_ms / 1000))
    period = 1.0 / spectrum_cfg.fps
    acc = np.zeros(spec.size)
    acc_frames = 0
    clip = 0.0
    last_emit = time.monotonic()
    antenna = driver.effective_antenna(device)
    runner: CampaignRunner | None = None

    try:
        while keep_running():
            try:
                while True:
                    name, value = commands.get_nowait()
                    if name == "stop":
                        return
                    if name == "antenna":
                        cmd = driver.set_antenna(value)
                        antenna = driver.effective_antenna(device)
                        acc[:] = 0
                        acc_frames = 0
                        events.put({"type": "switch", "antenna": antenna, "ts_ms": now_ms(),
                                    "sample": cmd.sample if cmd else None})
                    elif name == "frequency":
                        center = float(value)
                        driver.set_frequency(center + receiver.dc_offset_hz)
                        acc[:] = 0
                        acc_frames = 0
                    elif name == "gain":
                        driver.set_gain_step(int(value))
                        events.put({"type": "gain", "gain_index": int(value), "ts_ms": now_ms()})
                    elif name == "campaign_start":
                        try:
                            driver.set_frequency(float(value["sdr_hz"]))
                            runner = CampaignRunner(driver, value, now_ms())
                            center = float(value["center_hz"])
                            events.put({"type": "campaign_started", "campaign": value["campaign"],
                                        "dwell_ms": runner.dwell * 1000 / rate,
                                        "blank_ms": runner.blank * 1000 / rate,
                                        "fft_size": runner.fft})
                        except (ValueError, HardwareError) as exc:
                            runner = None
                            events.put({"type": "campaign_error", "campaign": value["campaign"],
                                        "message": str(exc)})
                    elif name == "survey":
                        runner = None  # the survey replaces the measurement while it runs
                        events.put(run_survey(driver, value))
                    elif name == "campaign_stop":
                        runner = None
                        driver.set_antenna(antenna)
                        spec = Spectrum(spectrum_cfg.fft_size, rate)
                        acc[:] = 0
                        acc_frames = 0
                        events.put({"type": "campaign_stopped"})
                    elif name == "bench":
                        events.put(run_bench_step(driver, value))
                        # Back to the input the operator had chosen, fresh spectrum.
                        driver.set_antenna(antenna)
                        spec = Spectrum(spectrum_cfg.fft_size, rate)
                        acc[:] = 0
                        acc_frames = 0
                        last_emit = time.monotonic()
            except queue.Empty:
                pass

            if runner is not None:
                for event in runner.cycle():
                    events.put(event)
                continue

            iq = driver.read_iq(block)
            clip = max(clip, clipping_ratio(iq, spectrum_cfg.clip_threshold))
            frames = spec.frames(iq)
            if len(frames):
                acc += frames.sum(axis=0)
                acc_frames += len(frames)
            now = time.monotonic()
            if now - last_emit >= period and acc_frames:
                mean = acc / acc_frames
                floor_bin = spec.noise_floor_bin(mean)
                sdr_hz = driver.frequency()
                events.put({
                    "type": "spectrum",
                    "ts_ms": now_ms(),
                    "antenna": antenna,
                    "center_hz": center,
                    "f_start_hz": sdr_hz - rate / 2,
                    "f_step_hz": rate / spec.size * group,
                    "bandwidth_hz": driver.instant_bandwidth(),
                    "bins_dbfs": _display(np.asarray(db(mean)), spectrum_cfg.display_points),
                    "floor_dbfs": round(float(db(floor_bin)), 1),
                    "clip_ratio": clip,
                    "clip_alert": clip >= spectrum_cfg.clip_alert_ratio,
                    "overload": driver.overload(),
                    "dropped": driver.dropped_samples(),
                    "frames": acc_frames,
                })
                acc[:] = 0
                acc_frames = 0
                clip = 0.0
                last_emit = now
    except HardwareError as exc:
        events.put({"type": "error", "fatal": True, "message": str(exc), "help_url": None})
    finally:
        driver.shutdown()
        events.put({"type": "closed"})


def run_survey(driver: SdrDriver, request: dict[str, Any]) -> Event:
    """Survey of the band of an automatic campaign, then detection (in the worker)."""
    try:
        driver.set_frequency(float(request["sdr_hz"]))
        raw = detect.survey(driver, float(request["seconds"]), int(request["fft_size"]),
                            inputs=request.get("inputs"))
        result = detect.detect(
            raw["grams"], raw["rate"], raw["fft_size"], raw["sdr_hz"],
            low_hz=float(request["low_hz"]), high_hz=float(request["high_hz"]),
            usable_hz=driver.instant_bandwidth(), detect_db=float(request["detect_db"]),
            class_width_hz=request.get("class_width_hz"))
    except HardwareError as exc:
        return {"type": "survey_error", "campaign": request["campaign"], "message": str(exc)}
    return {"type": "survey_result", "campaign": request["campaign"], "result": result}


def run_bench_step(driver: SdrDriver, request: dict[str, Any]) -> Event:
    """One qualification bench step, in the worker (it owns the driver)."""
    result: dict[str, Any] = {}
    try:
        if "levels" in request["measures"]:
            result["levels"] = bench.measure_levels(
                driver, request["levels_seconds"], request["fft_size"], request["subbands"])
        if "switching" in request["measures"]:
            raw = bench.measure_switching(driver, request["switches"], request["dwell_ms"] / 1000)
            result["switching"] = bench.analyze_switching(
                raw["power"], raw["rate"], raw["dwell"], raw["commands"], raw["inputs"],
                step_min_db=request["step_min_db"], tolerance_db=request["tolerance_db"])
            result["switching"]["dropped"] = raw["dropped"]
    except HardwareError as exc:
        return {"type": "bench_error", "run": request["run"], "step": request["step"],
                "message": str(exc)}
    return {"type": "bench_result", "run": request["run"], "step": request["step"],
            "result": result}


def _child_main(hardware: dict[str, Any], receiver: dict[str, Any], spectrum: dict[str, Any],
                commands: Any, events: Any) -> None:
    parent = multiprocessing.parent_process()

    def keep_running() -> bool:
        return parent is None or parent.is_alive()

    run_acquisition(
        HardwareConfig.model_validate(hardware),
        ReceiverConfig.model_validate(receiver),
        SpectrumConfig.model_validate(spectrum),
        commands,
        events,
        keep_running,
    )
    if not keep_running():
        # Nobody reads the queues any more: exiting must not wait for them to drain,
        # or the orphan would keep the SDR forever.
        events.cancel_join_thread()
        commands.cancel_join_thread()


class Acquisition:
    """Parent-side handle of one worker: start, send commands, receive events."""

    def __init__(
        self,
        hardware: HardwareConfig,
        receiver: ReceiverConfig,
        spectrum: SpectrumConfig,
        on_event: Callable[[Event], None],
        *,
        inline: bool = False,
        driver_factory: Callable[[HardwareConfig], SdrDriver] = create_driver,
    ) -> None:
        self._configs = (hardware, receiver, spectrum)
        self._on_event = on_event
        self._inline = inline
        self._factory = driver_factory
        self._commands: Any = None
        self._events: Any = None
        self._worker: threading.Thread | multiprocessing.process.BaseProcess | None = None
        self._reader: threading.Thread | None = None
        self._stopping = threading.Event()

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        hardware, receiver, spectrum = self._configs
        if self._inline:
            self._commands, self._events = queue.Queue(), queue.Queue()
            self._worker = threading.Thread(
                target=run_acquisition,
                args=(hardware, receiver, spectrum, self._commands, self._events,
                      lambda: not self._stopping.is_set(), self._factory),
                name="acquisition", daemon=True,
            )
        else:
            ctx = multiprocessing.get_context("spawn")
            self._commands, self._events = ctx.Queue(), ctx.Queue()
            self._worker = ctx.Process(
                target=_child_main,
                args=(hardware.model_dump(), receiver.model_dump(), spectrum.model_dump(),
                      self._commands, self._events),
                name="oav-acquisition", daemon=True,
            )
        self._worker.start()
        self._reader = threading.Thread(target=self._read_events, name="acq-events", daemon=True)
        self._reader.start()

    def _read_events(self) -> None:
        while True:
            try:
                event = self._events.get(timeout=PARENT_CHECK_S)
            except queue.Empty:
                if not self.running:
                    return
                continue
            try:
                self._on_event(event)
            except Exception:
                log.exception("Traitement d'un événement d'acquisition")
            if event.get("type") == "closed":
                return

    def send(self, name: str, value: Any = None) -> None:
        if self._commands is None or not self.running:
            raise HardwareError("Acquisition arrêtée.")
        self._commands.put((name, value))

    def stop(self, timeout_s: float = 5.0) -> None:
        if self._worker is None:
            return
        self._stopping.set()
        if self.running:
            self._commands.put(("stop", None))
        self._worker.join(timeout_s)
        worker = self._worker
        if isinstance(worker, multiprocessing.process.BaseProcess) and worker.is_alive():
            worker.terminate()
            self._worker.join(1.0)
        if self._reader is not None:
            self._reader.join(timeout_s)
        self._worker = None


def wait_for(predicate: Callable[[], bool], timeout_s: float, step_s: float = 0.02) -> bool:
    """Poll ``predicate`` until it is true or the delay expires (tests and CLI)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return predicate()


__all__ = ["Acquisition", "Event", "run_acquisition", "wait_for"]
