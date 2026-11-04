"""Station service: owns configuration, database and the acquisition workers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from open_antenna_validation import __version__
from open_antenna_validation.acquisition import Acquisition, Event
from open_antenna_validation.config import (
    CONFIG_FILENAME,
    Antenna,
    Config,
    HardwareConfig,
    load_config,
    resolve_path,
    save_config,
)
from open_antenna_validation.demo import ensure_demo_campaigns
from open_antenna_validation.hardware import DeviceInfo, HardwareError, SdrDriver
from open_antenna_validation.hardware import create_driver as default_factory
from open_antenna_validation.hardware.identity import IdentityCheck, record_hardware
from open_antenna_validation.hub import Hub
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

log = logging.getLogger(__name__)

TOPIC_SPECTRUM = "spectrum"
TOPIC_STATUS = "status"
HF_RANGE_HZ = (1_000_000.0, 30_000_000.0)
# Retry after a failure or while no SDR is plugged in: 5 s, doubled up to 1 min.
RESTART_DELAY_S = 5.0
RESTART_MAX_S = 60.0


class Station:
    def __init__(
        self,
        home: Path,
        config: Config,
        db: Database,
        *,
        inline: bool = False,
        driver_factory: Callable[[HardwareConfig], SdrDriver] = default_factory,
    ) -> None:
        self.home = home
        self.config = config
        self.db = db
        self.hub = Hub()
        self.identity: IdentityCheck | None = None
        self.started_ms = now_ms()
        self._inline = inline
        self._factory = driver_factory
        self._lock = threading.Lock()
        self._acq: Acquisition | None = None
        self._running = False
        self._restart: threading.Timer | None = None
        self._retry_s = RESTART_DELAY_S
        # Last known state of the receiver, as reported by the worker.
        self.receiver: dict[str, Any] = {"state": "stopped"}
        self.last_spectrum: Event | None = None

    @classmethod
    def from_home(
        cls,
        home: Path,
        *,
        inline: bool = False,
        driver_factory: Callable[[HardwareConfig], SdrDriver] = default_factory,
    ) -> Station:
        config = load_config(home / CONFIG_FILENAME)
        db = Database(resolve_path(home, config.storage.database))
        db.migrate()
        return cls(home, config, db, inline=inline, driver_factory=driver_factory)

    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_FILENAME

    def save_config(self) -> None:
        save_config(self.config, self.config_path)

    # --- life cycle --------------------------------------------------------------

    def startup(self) -> None:
        self.db.log_event("service_start", {"version": __version__})
        if self.config.demo.enabled and ensure_demo_campaigns(self.db):
            log.info("Campagnes de démonstration créées")
        self._running = True
        self._start_acquisition()

    def shutdown(self) -> None:
        self._running = False
        if self._restart is not None:
            self._restart.cancel()
        with self._lock:
            acq, self._acq = self._acq, None
        if acq is not None:
            acq.stop()
        self.receiver = {**self.receiver, "state": "stopped"}
        self.db.log_event("service_stop", {})

    def _start_acquisition(self) -> None:
        cfg = self.config
        self.receiver = {"state": "starting", "driver": cfg.hardware.driver}
        acq = Acquisition(
            cfg.hardware, cfg.receiver, cfg.spectrum, self._on_event,
            inline=self._inline, driver_factory=self._factory,
        )
        with self._lock:
            self._acq = acq
        acq.start()
        self._publish_status()

    def _schedule_restart(self) -> None:
        if not self._running:
            return
        self._restart = threading.Timer(self._retry_s, self._restart_now)
        self._restart.daemon = True
        self._restart.start()
        self._retry_s = min(RESTART_MAX_S, self._retry_s * 2)

    def _restart_now(self) -> None:
        if not self._running:
            return
        with self._lock:
            acq, self._acq = self._acq, None
        if acq is not None:
            acq.stop()
        self._start_acquisition()

    # --- events from the worker --------------------------------------------------

    def _on_event(self, event: Event) -> None:
        kind = event.get("type")
        if kind == "spectrum":
            self.last_spectrum = event
            self.receiver["antenna"] = event["antenna"]
            self.receiver["center_hz"] = event["center_hz"]
            self.receiver["dropped"] = event["dropped"]
            self.receiver["overload"] = event["overload"]
            self.receiver["clip_alert"] = event["clip_alert"]
            self.hub.publish(TOPIC_SPECTRUM, event)
        elif kind == "opened":
            self._retry_s = RESTART_DELAY_S
            device = DeviceInfo(**event["device"])
            self.identity = record_hardware(self.db, device, event["antenna"], event["gain_mode"])
            self.receiver = {"state": "running", **{k: v for k, v in event.items() if k != "type"},
                             "identity": self.identity.identity,
                             "identity_changed": self.identity.changed}
            log.info("SDR ouvert : %s %s", device.model, device.serial)
            self._publish_status()
        elif kind == "switch":
            self.receiver["antenna"] = event["antenna"]
            self.db.log_event("antenna_switch", {"antenna": event["antenna"],
                                                 "sample": event["sample"]})
            self._publish_status()
        elif kind == "gain":
            self.receiver["gain_index"] = event["gain_index"]
            self.db.log_event("gain_change", {"gain_index": event["gain_index"]})
            self._publish_status()
        elif kind == "error":
            state = "absent" if event.get("absent") else "error"
            changed = (self.receiver.get("state"), self.receiver.get("error")) != (
                state, event["message"])
            self.receiver = {**self.receiver, "state": state, "error": event["message"],
                             "help_url": event.get("help_url")}
            # Retries repeat the same message: only state changes go to the journal.
            if changed:
                self.db.log_event("sdr_absent" if state == "absent" else "error",
                                  {"message": event["message"]})
                log.warning("Acquisition : %s", event["message"])
            self._publish_status()
            if event.get("fatal"):
                self._schedule_restart()
        elif kind == "closed" and self.receiver.get("state") == "running" and self._running:
            self.receiver["state"] = "error"
            self.receiver["error"] = "Acquisition arrêtée de façon inattendue."
            self._publish_status()
            self._schedule_restart()

    def _publish_status(self) -> None:
        self.hub.publish(TOPIC_STATUS, self.hardware_report())

    # --- commands -----------------------------------------------------------------

    def _send(self, name: str, value: Any) -> None:
        with self._lock:
            acq = self._acq
        if acq is None or self.receiver.get("state") != "running":
            raise HardwareError("Le récepteur n'est pas en marche.")
        acq.send(name, value)

    def set_antenna(self, antenna: Antenna) -> None:
        choices = self.receiver.get("antennas") or []
        if antenna and antenna not in choices:
            available = ", ".join(choices) if choices else "aucune sélection possible"
            raise HardwareError(f"Entrée {antenna} indisponible ({available}).")
        self._send("antenna", antenna)
        self.config.hardware.antenna = antenna
        self.save_config()

    def set_frequency(self, hz: float) -> None:
        low, high = HF_RANGE_HZ
        if not low <= hz <= high:
            raise HardwareError("Fréquence hors de la plage HF (1 à 30 MHz).")
        self._send("frequency", hz)
        self.config.receiver.center_frequency_hz = hz
        self.receiver["center_hz"] = hz
        self.save_config()

    def set_gain_step(self, index: int) -> None:
        steps = self.receiver.get("gain_steps") or []
        if not 0 <= index < len(steps):
            raise HardwareError(f"Pas de gain {index} hors plage.")
        self._send("gain", index)
        self.config.hardware.gain_step = index
        self.save_config()

    # --- reports ------------------------------------------------------------------

    def hardware_report(self) -> dict[str, Any]:
        r = self.receiver
        return {
            "state": r.get("state"),
            "driver": self.config.hardware.driver,
            "device": r.get("device"),
            "library": r.get("library"),
            "error": r.get("error"),
            "help_url": r.get("help_url"),
            "antennas": r.get("antennas", []),
            "antenna": r.get("antenna"),
            "gain_steps": r.get("gain_steps", []),
            "gain_index": r.get("gain_index"),
            "sample_rate": r.get("sample_rate"),
            "bandwidth_hz": r.get("bandwidth_hz"),
            "center_hz": r.get("center_hz", self.config.receiver.center_frequency_hz),
            "identity": r.get("identity"),
            "identity_changed": r.get("identity_changed", False),
            "dropped": r.get("dropped", 0),
            "overload": r.get("overload", False),
            "clip_alert": r.get("clip_alert", False),
        }
