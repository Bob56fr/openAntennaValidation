"""Station service: owns configuration, database and the acquisition workers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from open_antenna_validation import __version__
from open_antenna_validation import campaign_manager as cm
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
from open_antenna_validation.qualification import QualificationError, check_step, store_step
from open_antenna_validation.storage import Database
from open_antenna_validation.timeutil import now_ms

log = logging.getLogger(__name__)

TOPIC_SPECTRUM = "spectrum"
TOPIC_STATUS = "status"
TOPIC_QUALIFICATION = "qualification"
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
        # Bench step in progress (the worker is busy), and the last bench failure.
        self.bench: dict[str, Any] = {"busy": False}
        # Campaign in progress (state running or interrupted), if any.
        self.campaign: cm.Running | None = None
        self._campaign_lock = threading.RLock()

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
        self._resume_campaign()
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

    # --- campaigns ------------------------------------------------------------------

    def _resume_campaign(self) -> None:
        """After a restart: go on with a campaign still in progress, or close it."""
        run = cm.load_running(self.db)
        if run is None:
            return
        last = self.db.query("SELECT MAX(ts_ms) FROM measurements WHERE campaign_id = ?",
                             (run.id,))[0][0]
        if now_ms() >= run.planned_end_ms:
            end = min(run.planned_end_ms, int(last or run.planned_end_ms))
            cm.finish(self.db, run, "done", end)
            return
        self.campaign = run
        # The station was off: the gap is an interruption, closed when the engine restarts.
        self._interrupt(int(last) if last else now_ms(), "Service arrêté")

    def _interrupt(self, ts: int, reason: str) -> None:
        run = self.campaign
        if run is None or run.interrupted_since is not None:
            return
        run.interrupted_since = ts
        run.engine_started = False
        cm.switch_activity(self.db, run, "interrupted", ts, reason)
        with self.db.transaction() as conn:
            conn.execute("UPDATE campaigns SET state = 'interrupted' WHERE id = ?", (run.id,))

    def _start_engine(self) -> None:
        run = self.campaign
        if run is None or self.receiver.get("state") != "running":
            return
        if run.auto and run.last_survey_ms is None:
            # First start, or after a restart: survey the band before measuring.
            self._start_survey()
        else:
            self._send("campaign_start", cm.engine_spec(run, self.config))
        if run.interrupted_since is not None:
            run.interrupted_since = None
            run.calm = False
            run.last_present_ms = now_ms()
            cm.switch_activity(self.db, run, "active", now_ms())
            with self.db.transaction() as conn:
                conn.execute("UPDATE campaigns SET state = 'running' WHERE id = ?", (run.id,))
        # Last: whoever sees the engine started sees the campaign running in the database.
        run.engine_started = True

    def _start_survey(self) -> None:
        run = self.campaign
        if run is None:
            return
        run.surveying = True
        self._send("survey", cm.survey_request(run, self.config))
        self._publish_status()

    def _on_survey(self, event: Event) -> None:
        with self._campaign_lock:
            run = self.campaign
            if run is None or event.get("campaign") != run.uuid:
                return
            run.surveying = False
            ts = max(now_ms(), (run.last_ts or 0) + int(run.params["period_s"] * 1000))
            if event["type"] == "survey_result":
                summary = cm.apply_survey(self.db, run, event["result"], ts)
                log.info("Relevé de la bande : %s", summary)
            else:
                run.last_survey_ms = ts  # retried at the next re-evaluation
                self.db.log_event("error", {"survey": run.uuid, "message": event["message"]})
            if self.receiver.get("state") == "running":
                self._send("campaign_start", cm.engine_spec(run, self.config))
        self._publish_status()

    def start_campaign(self, request: dict[str, Any]) -> str:
        with self._campaign_lock:
            if self.bench.get("busy"):
                raise cm.CampaignError("Une mesure du banc est en cours.")
            uuid = cm.create_campaign(self.db, self.config, self.receiver, request)
            self.campaign = cm.load_running(self.db)
            self._start_engine()
        self._publish_status()
        return uuid

    def stop_campaign(self) -> None:
        with self._campaign_lock:
            run = self.campaign
            if run is None:
                raise cm.CampaignError("Aucune campagne en cours.")
            if run.engine_started and self.receiver.get("state") == "running":
                self._send("campaign_stop", None)
            cm.finish(self.db, run, "abandoned", now_ms())
            self.campaign = None
        self._publish_status()

    def _on_measurement(self, event: Event) -> None:
        with self._campaign_lock:
            run = self.campaign
            if run is None or event.get("campaign") != run.uuid:
                return
            calm_before = run.calm
            cm.store_measurements(self.db, self.config, run, event)
            ts = int(event["ts_ms"])
            if ts + int(event["period_ms"]) >= run.planned_end_ms:
                if self.receiver.get("state") == "running":
                    self._send("campaign_stop", None)
                # Closed in the database first: whoever sees no campaign sees it finished.
                cm.finish(self.db, run, "done", run.planned_end_ms)
                self.campaign = None
                log.info("Campagne %s terminée", run.uuid)
            elif run.auto and not run.surveying and run.last_survey_ms is not None and (
                ts - run.last_survey_ms >= 1000 * (self.config.campaign.calm_probe_s if run.calm
                                                   else self.config.campaign.reevaluate_s)):
                self._start_survey()
            elif run.calm != calm_before and self.receiver.get("state") == "running":
                # Calm band: slower measurements (and back to normal when it wakes up).
                self._send("campaign_start", cm.engine_spec(run, self.config))
        self._publish_status()

    def campaign_report(self) -> dict[str, Any] | None:
        run = self.campaign
        if run is None:
            return None
        row = self.db.query("SELECT title, start_ms, planned_end_ms, state FROM campaigns"
                            " WHERE id = ?", (run.id,))[0]
        last = self.db.query("SELECT MAX(ts_ms) FROM measurements WHERE campaign_id = ?",
                             (run.id,))[0][0]
        return {"uuid": run.uuid, "title": row["title"], "state": row["state"],
                "start_ms": row["start_ms"], "planned_end_ms": row["planned_end_ms"],
                "last_ms": last, "calm": run.calm, "aa": bool(run.params.get("aa")),
                "signals": len([s for s in run.signals if s["class"] != "noise"]),
                "rate_hz": run.params["rate_hz"], "auto": run.auto,
                "surveying": run.surveying, "last_survey_ms": run.last_survey_ms}

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
            if self.campaign is not None:
                try:
                    self._start_engine()
                except HardwareError:
                    log.exception("Reprise de la campagne")
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
        elif kind == "measurement":
            self._on_measurement(event)
        elif kind in ("survey_result", "survey_error"):
            self._on_survey(event)
        elif kind == "campaign_error":
            log.error("Campagne : %s", event["message"])
            self.db.log_event("error", {"campaign": event["campaign"],
                                        "message": event["message"]})
            if self.campaign is not None:
                self._interrupt(now_ms(), event["message"])
            self._publish_status()
        elif kind in ("bench_result", "bench_error"):
            if kind == "bench_result":
                store_step(self.db, event["run"], event["step"], event["result"])
                self.bench = {"busy": False, "run": event["run"], "step": event["step"]}
            else:
                self.bench = {"busy": False, "run": event["run"], "step": event["step"],
                              "error": event["message"]}
                self.db.log_event("error", {"bench": event["step"], "message": event["message"]})
            self.hub.publish(TOPIC_QUALIFICATION, self.bench)
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
            self._interrupt(now_ms(), event["message"])
            self._publish_status()
            if event.get("fatal"):
                self._schedule_restart()
        elif kind == "closed" and self.receiver.get("state") == "running" and self._running:
            self._interrupt(now_ms(), "Acquisition arrêtée")
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

    def _no_campaign(self) -> None:
        if self.campaign is not None:
            raise HardwareError("Une campagne est en cours : le récepteur est réservé à la mesure.")

    def set_antenna(self, antenna: Antenna) -> None:
        self._no_campaign()
        choices = self.receiver.get("antennas") or []
        if antenna and antenna not in choices:
            available = ", ".join(choices) if choices else "aucune sélection possible"
            raise HardwareError(f"Entrée {antenna} indisponible ({available}).")
        self._send("antenna", antenna)
        self.config.hardware.antenna = antenna
        self.save_config()

    def set_frequency(self, hz: float) -> None:
        self._no_campaign()
        low, high = HF_RANGE_HZ
        if not low <= hz <= high:
            raise HardwareError("Fréquence hors de la plage HF (1 à 30 MHz).")
        self._send("frequency", hz)
        self.config.receiver.center_frequency_hz = hz
        self.receiver["center_hz"] = hz
        self.save_config()

    def set_gain_step(self, index: int) -> None:
        self._no_campaign()
        steps = self.receiver.get("gain_steps") or []
        if not 0 <= index < len(steps):
            raise HardwareError(f"Pas de gain {index} hors plage.")
        self._send("gain", index)
        self.config.hardware.gain_step = index
        self.save_config()

    def start_bench_step(self, run: str, step: str) -> None:
        """Ask the worker for one bench step; the result arrives as an event."""
        definition = check_step(self.db, run, step)
        self._no_campaign()
        if self.bench.get("busy"):
            raise QualificationError("Une mesure du banc est déjà en cours.")
        q = self.config.qualification
        request = {
            "run": run, "step": step, "measures": list(definition.measures),
            "switches": q.switches, "dwell_ms": q.dwell_ms, "levels_seconds": q.levels_seconds,
            "subbands": q.subbands, "fft_size": self.config.spectrum.fft_size,
            "tolerance_db": q.tolerance_db, "step_min_db": q.step_min_db,
        }
        self._send("bench", request)
        self.bench = {"busy": True, "run": run, "step": step, "since_ms": now_ms()}
        self.hub.publish(TOPIC_QUALIFICATION, self.bench)

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
            "campaign": self.campaign_report(),
        }
