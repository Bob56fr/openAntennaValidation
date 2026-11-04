"""Station service: owns configuration, database and the acquisition workers."""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid as uuidlib
from collections.abc import Callable
from dataclasses import dataclass, field
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
    SdrSlot,
    load_config,
    resolve_path,
    save_config,
)
from open_antenna_validation.demo import ensure_demo_campaigns
from open_antenna_validation.hardware import DeviceInfo, HardwareError, SdrDriver
from open_antenna_validation.hardware import create_driver as default_factory
from open_antenna_validation.hardware.identity import IdentityCheck, record_hardware
from open_antenna_validation.hub import Hub
from open_antenna_validation.publish import Publisher
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
# A load test stays valid this long for launching a simultaneous campaign.
LOAD_VALID_MS = 3_600_000
# Simultaneous mode: the common start instant leaves this time for the commands to arrive.
ALIGN_LEAD_MS = 500


@dataclass
class _Device:
    """Another SDR of the station (simultaneous mode), with its own acquisition worker."""

    slot: SdrSlot
    acq: Acquisition | None = None
    receiver: dict[str, Any] = field(default_factory=lambda: {"state": "stopped"})
    identity: IdentityCheck | None = None
    restart: threading.Timer | None = None
    retry_s: float = RESTART_DELAY_S


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
        # The other SDRs (simultaneous mode), and the load test in progress or the last one.
        self._others = [_Device(slot) for slot in config.hardware.others]
        self.load_test: dict[str, Any] | None = None
        # Publication on the WordPress site (idle while disabled in the configuration).
        self.publisher = Publisher(db, config, self._station_status)

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
        for dev in self._others:
            self._start_other(dev)
        self.publisher.start()

    def shutdown(self) -> None:
        self._running = False
        self.publisher.stop()
        if self._restart is not None:
            self._restart.cancel()
        with self._lock:
            acq, self._acq = self._acq, None
            others = [(d, d.acq) for d in self._others]
            for d in self._others:
                d.acq = None
        if acq is not None:
            acq.stop()
        for d, other in others:
            if d.restart is not None:
                d.restart.cancel()
            if other is not None:
                other.stop()
            d.receiver = {**d.receiver, "state": "stopped"}
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

    def _start_other(self, dev: _Device) -> None:
        cfg = self.config
        dev.receiver = {"state": "starting", "driver": cfg.hardware.driver,
                        "device": {"serial": dev.slot.serial}}
        acq = Acquisition(
            cfg.hardware.for_slot(dev.slot), cfg.receiver, cfg.spectrum,
            lambda event: self._on_other_event(dev, event),
            inline=self._inline, driver_factory=self._factory,
        )
        with self._lock:
            dev.acq = acq
        acq.start()

    def _restart_other(self, dev: _Device) -> None:
        if not self._running:
            return
        with self._lock:
            acq, dev.acq = dev.acq, None
        if acq is not None:
            acq.stop()
        self._start_other(dev)

    def _schedule_other_restart(self, dev: _Device) -> None:
        if not self._running:
            return
        dev.restart = threading.Timer(dev.retry_s, self._restart_other, (dev,))
        dev.restart.daemon = True
        dev.restart.start()
        dev.retry_s = min(RESTART_MAX_S, dev.retry_s * 2)

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
            cm.finish(self.db, run, "done", end, self.config)
            return
        self.campaign = run
        # The station was off: the gap is an interruption, closed when the engine restarts.
        self._interrupt(int(last) if last else now_ms(), "Service arrêté")

    def _interrupt(self, ts: int, reason: str, serial: str | None = None) -> None:
        """Interruption of the campaign (``serial``: the SDR that failed, None: the main one)."""
        run = self.campaign
        if run is None or run.interrupted_since is not None:
            return
        if serial is not None and serial not in self._run_serials(run):
            return  # an SDR the campaign does not use
        run.interrupted_since = ts
        run.engine_started = False
        cm.switch_activity(self.db, run, "interrupted", ts, reason)
        with self.db.transaction() as conn:
            conn.execute("UPDATE campaigns SET state = 'interrupted' WHERE id = ?", (run.id,))
        # The other SDRs stop too: measurements are only meaningful together.
        self._stop_engines(run)

    def _run_serials(self, run: cm.Running) -> list[str]:
        return run.devices if run.simultaneous else [self._primary_serial()]

    def _ready(self, run: cm.Running) -> bool:
        return all(self._device_state(s) == "running" for s in self._run_serials(run))

    def _start_engine(self) -> None:
        run = self.campaign
        if run is None or not self._ready(run):
            return
        if run.auto and run.last_survey_ms is None:
            # First start, or after a restart: survey the band before measuring.
            self._start_survey()
        else:
            self._start_engines(run)
        if run.interrupted_since is not None:
            run.interrupted_since = None
            run.calm = False
            run.last_present_ms = now_ms()
            cm.switch_activity(self.db, run, "active", now_ms())
            with self.db.transaction() as conn:
                conn.execute("UPDATE campaigns SET state = 'running' WHERE id = ?", (run.id,))
        # Last: whoever sees the engine started sees the campaign running in the database.
        run.engine_started = True

    def _start_engines(self, run: cm.Running) -> None:
        """(Re)start the measurement on every SDR of the campaign, on one time grid."""
        if not run.simultaneous:
            self._send("campaign_start", cm.engine_spec(run, self.config))
            return
        start = max(cm.engine_start_ms(run), now_ms() + ALIGN_LEAD_MS)
        for k, serial in enumerate(run.devices):
            self._send_to(serial, "campaign_start", cm.engine_spec(run, self.config, k, start))

    def _stop_engines(self, run: cm.Running, only: str | None = None) -> None:
        for serial in self._run_serials(run):
            if only is not None and serial != only:
                continue
            if self._device_state(serial) == "running":
                try:
                    self._send_to(serial, "campaign_stop", None)
                except HardwareError:
                    log.warning("Arrêt de la mesure sur %s", serial)

    def _start_survey(self) -> None:
        run = self.campaign
        if run is None:
            return
        run.surveying = True
        request = cm.survey_request(run, self.config)
        if run.simultaneous:
            # The first SDR surveys the band on its own input; the others wait.
            for serial in run.devices[1:]:
                self._stop_engines(run, serial)
            self._send_to(run.devices[0], "survey", request)
        else:
            self._send("survey", request)
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
            if self._ready(run):
                self._start_engines(run)
        self._publish_status()

    def start_campaign(self, request: dict[str, Any]) -> str:
        with self._campaign_lock:
            if self.bench.get("busy"):
                raise cm.CampaignError("Une mesure du banc est en cours.")
            if (self.load_test or {}).get("state") == "running":
                raise cm.CampaignError("Le test de charge est en cours.")
            if request.get("reception") == "simultaneous":
                self._check_load(request)
                load = self.load_test or {}
                request = {**request, "load_test": {
                    k: load.get(k) for k in ("verdict", "message", "loads", "station_load",
                                             "cores", "ended_ms")}}
            free = shutil.disk_usage(self.db.path.parent).free
            uuid = cm.create_campaign(self.db, self.config, self.receiver, request,
                                      devices=self.devices_report(), free_bytes=free)
            self.campaign = cm.load_running(self.db)
            self._start_engine()
        self._publish_status()
        return uuid

    def stop_campaign(self) -> None:
        with self._campaign_lock:
            run = self.campaign
            if run is None:
                raise cm.CampaignError("Aucune campagne en cours.")
            if run.engine_started:
                self._stop_engines(run)
            cm.finish(self.db, run, "abandoned", now_ms(), self.config)
            self.publisher.trigger()
            self.campaign = None
        self._publish_status()

    def _on_measurement(self, event: Event) -> None:
        load = self.load_test
        if load is not None and event.get("campaign") == load["id"]:
            self._collect_load(event)
            return
        with self._campaign_lock:
            run = self.campaign
            if run is None or event.get("campaign") != run.uuid:
                return
            ts = int(event["ts_ms"])
            period = int(event["period_ms"])
            if ts >= run.planned_end_ms:
                return  # an SDR a little ahead of the others, past the end
            calm_before = run.calm
            cm.store_measurements(self.db, self.config, run, event)
            if run.simultaneous and ts + period >= run.planned_end_ms:
                self._stop_engines(run, event.get("serial"))  # this SDR is done
            done = cm.all_reported(run) if run.simultaneous else ts
            if done + period >= run.planned_end_ms:
                self._stop_engines(run)
                # Closed in the database first: whoever sees no campaign sees it finished.
                cm.finish(self.db, run, "done", run.planned_end_ms, self.config)
                self.publisher.trigger()
                self.campaign = None
                log.info("Campagne %s terminée", run.uuid)
            elif run.auto and not run.surveying and run.last_survey_ms is not None and (
                ts - run.last_survey_ms >= 1000 * (self.config.campaign.calm_probe_s if run.calm
                                                   else self.config.campaign.reevaluate_s)):
                self._start_survey()
            elif run.resync and self._ready(run):
                run.resync = False
                self.db.log_event("resync", {"campaign": run.uuid,
                                             "alignment_ms": run.align_ms[-3:]})
                self._start_engines(run)
            elif run.calm != calm_before and self._ready(run):
                # Calm band: slower measurements (and back to normal when it wakes up).
                self._start_engines(run)
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
                "surveying": run.surveying, "last_survey_ms": run.last_survey_ms,
                "reception": run.reception, "devices": run.devices,
                "alignment_ms": _percentiles(run.align_ms)}

    # --- load test ------------------------------------------------------------------

    def start_load_test(self, serials: list[str] | None = None) -> dict[str, Any]:
        """Run the measurement on every SDR at full load (most signals, shortest period)
        for a while, and judge whether the computer keeps up."""
        with self._campaign_lock:
            if self.campaign is not None:
                raise cm.CampaignError("Une campagne est en cours.")
            if self.bench.get("busy"):
                raise cm.CampaignError("Une mesure du banc est en cours.")
            if (self.load_test or {}).get("state") == "running":
                raise cm.CampaignError("Le test de charge est déjà en cours.")
            devices = [d for d in self.devices_report() if d.get("state") == "running"
                       and (not serials or d["serial"] in serials)]
            if not devices:
                raise cm.CampaignError("Aucun SDR en marche pour le test de charge.")
            seconds = self.config.campaign.load_test_s
            test_id = f"loadtest-{uuidlib.uuid4()}"
            self.load_test = {"id": test_id, "state": "running", "started_ms": now_ms(),
                              "seconds": seconds, "serials": [d["serial"] for d in devices],
                              "samples": {d["serial"]: [] for d in devices}}
            for d in devices:
                self._send_to(d["serial"], "campaign_start",
                              cm.load_test_spec(self.config, d, test_id))
            timer = threading.Timer(seconds, self._finish_load_test, (test_id,))
            timer.daemon = True
            timer.start()
        self._publish_status()
        return self.load_report() or {}

    def _collect_load(self, event: Event) -> None:
        load = self.load_test
        if load is None or load["state"] != "running":
            return
        samples = load["samples"].get(event.get("serial"))
        if samples is not None:
            samples.append((float(event.get("cpu_s") or 0.0),
                            float(event.get("audio_s") or 0.0), int(event.get("dropped") or 0)))

    def _finish_load_test(self, test_id: str) -> None:
        with self._campaign_lock:
            load = self.load_test
            if load is None or load["id"] != test_id or load["state"] != "running":
                return
            for serial in load["serials"]:
                if self._device_state(serial) == "running":
                    try:
                        self._send_to(serial, "campaign_stop", None)
                    except HardwareError:
                        log.warning("Fin du test de charge sur %s", serial)
            load.update(judge_load(load["samples"], self.config, os.cpu_count() or 1,
                                   inline=self._inline))
            load["state"] = "done"
            load["ended_ms"] = now_ms()
            self.db.log_event("load_test", {k: v for k, v in load.items() if k != "samples"})
        self._publish_status()

    def load_report(self) -> dict[str, Any] | None:
        load = self.load_test
        if load is None:
            return None
        return {k: v for k, v in load.items() if k != "samples"}

    def _check_load(self, request: dict[str, Any]) -> None:
        serials = [str(c.get("serial")) for c in request.get("channels") or []]
        load = self.load_test
        if load is None or load.get("state") != "done" \
                or now_ms() - int(load["ended_ms"]) > LOAD_VALID_MS \
                or not set(serials) <= set(load["serials"]):
            raise cm.CampaignError(
                "Faites d'abord le test de charge avec ces SDR (valable une heure).")
        if load["verdict"] == "refuse":
            raise cm.CampaignError("Test de charge : l'ordinateur ne suit pas. " + load["message"])
        if load["verdict"] == "limit" and not request.get("accept_load"):
            raise cm.CampaignError("Test de charge à la limite : confirmez pour lancer quand "
                                   "même. " + load["message"])

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
            self._on_measurement({**event, "serial": self._primary_serial()})
        elif kind in ("survey_result", "survey_error"):
            self._on_survey(event)
        elif kind == "campaign_error":
            log.error("Campagne : %s", event["message"])
            self.db.log_event("error", {"campaign": event["campaign"],
                                        "message": event["message"]})
            if self.campaign is not None:
                self._interrupt(now_ms(), event["message"], self._primary_serial())
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
            self._interrupt(now_ms(), event["message"], self._primary_serial())
            self._publish_status()
            if event.get("fatal"):
                self._schedule_restart()
        elif kind == "closed" and self.receiver.get("state") == "running" and self._running:
            self._interrupt(now_ms(), "Acquisition arrêtée", self._primary_serial())
            self.receiver["state"] = "error"
            self.receiver["error"] = "Acquisition arrêtée de façon inattendue."
            self._publish_status()
            self._schedule_restart()

    def _on_other_event(self, dev: _Device, event: Event) -> None:
        """Events of another SDR: measurements, state; its spectrum is not displayed."""
        kind = event.get("type")
        serial = dev.slot.serial
        r = dev.receiver
        if kind == "spectrum":
            r.update(antenna=event["antenna"], center_hz=event["center_hz"],
                     dropped=event["dropped"], overload=event["overload"],
                     clip_alert=event["clip_alert"])
        elif kind == "opened":
            dev.retry_s = RESTART_DELAY_S
            device = DeviceInfo(**event["device"])
            dev.identity = record_hardware(self.db, device, event["antenna"], event["gain_mode"])
            dev.receiver = {"state": "running", **{k: v for k, v in event.items() if k != "type"},
                            "identity": dev.identity.identity,
                            "identity_changed": dev.identity.changed}
            log.info("SDR ouvert : %s %s", device.model, device.serial)
            if self.campaign is not None:
                try:
                    with self._campaign_lock:
                        self._start_engine()
                except HardwareError:
                    log.exception("Reprise de la campagne")
            self._publish_status()
        elif kind == "measurement":
            self._on_measurement({**event, "serial": serial})
        elif kind in ("survey_result", "survey_error"):
            self._on_survey(event)
        elif kind == "campaign_error":
            log.error("Campagne (%s) : %s", serial, event["message"])
            self.db.log_event("error", {"campaign": event["campaign"], "serial": serial,
                                        "message": event["message"]})
            self._interrupt(now_ms(), f"SDR {serial} : {event['message']}", serial)
            self._publish_status()
        elif kind == "error":
            state = "absent" if event.get("absent") else "error"
            changed = (r.get("state"), r.get("error")) != (state, event["message"])
            dev.receiver = {**r, "state": state, "error": event["message"],
                            "help_url": event.get("help_url")}
            if changed:
                self.db.log_event("sdr_absent" if state == "absent" else "error",
                                  {"serial": serial, "message": event["message"]})
                log.warning("Acquisition %s : %s", serial, event["message"])
            self._interrupt(now_ms(), f"SDR {serial} : {event['message']}", serial)
            self._publish_status()
            if event.get("fatal"):
                self._schedule_other_restart(dev)
        elif kind == "closed" and r.get("state") == "running" and self._running:
            self._interrupt(now_ms(), f"SDR {serial} : acquisition arrêtée", serial)
            dev.receiver = {**r, "state": "error",
                            "error": "Acquisition arrêtée de façon inattendue."}
            self._publish_status()
            self._schedule_other_restart(dev)

    def _publish_status(self) -> None:
        self.hub.publish(TOPIC_STATUS, self.hardware_report())

    # --- commands -----------------------------------------------------------------

    def _send(self, name: str, value: Any) -> None:
        with self._lock:
            acq = self._acq
        if acq is None or self.receiver.get("state") != "running":
            raise HardwareError("Le récepteur n'est pas en marche.")
        acq.send(name, value)

    def _primary_serial(self) -> str:
        hw = self.config.hardware
        return str((self.receiver.get("device") or {}).get("serial") or hw.serial
                   or (hw.simulated.serial if hw.driver == "simulated" else ""))

    def _other(self, serial: str | None) -> _Device | None:
        return next((d for d in self._others if d.slot.serial == serial), None)

    def _device_state(self, serial: str | None) -> str | None:
        if serial == self._primary_serial():
            return self.receiver.get("state")
        dev = self._other(serial)
        return None if dev is None else dev.receiver.get("state")

    def _send_to(self, serial: str | None, name: str, value: Any) -> None:
        if serial == self._primary_serial():
            self._send(name, value)
            return
        dev = self._other(serial)
        with self._lock:
            acq = None if dev is None else dev.acq
        if dev is None or acq is None or dev.receiver.get("state") != "running":
            raise HardwareError(f"Le SDR n° {serial} n'est pas en marche.")
        acq.send(name, value)

    def _no_campaign(self) -> None:
        if self.campaign is not None:
            raise HardwareError("Une campagne est en cours : le récepteur est réservé à la mesure.")
        if (self.load_test or {}).get("state") == "running":
            raise HardwareError("Le test de charge est en cours.")

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
            "devices": [{k: v for k, v in d.items() if k not in ("antennas", "gain_steps")}
                        for d in self.devices_report()],
            "load_test": self.load_report(),
        }

    def _station_status(self) -> dict[str, Any]:
        """State of the station as published on the site."""
        state = str(self.receiver.get("state") or "stopped")
        if state not in ("running", "absent", "error", "starting", "stopped"):
            state = "error"
        models = [str(d["model"]) for d in self.devices_report() if d.get("model")]
        sdr = models[0] if len(set(models)) <= 1 else ", ".join(models)
        if len(models) > 1 and len(set(models)) == 1:
            sdr = f"{len(models)} × {models[0]}"
        return {"state": state, "sdr": sdr[:120],
                "campaign": None if self.campaign is None else self.campaign.uuid}

    def devices_report(self) -> list[dict[str, Any]]:
        """Every SDR of the station, the main one first (simultaneous mode)."""
        out = []
        for primary, r in [(True, self.receiver)] + [(False, d.receiver) for d in self._others]:
            device = r.get("device") or {}
            serial = self._primary_serial() if primary else str(device.get("serial", ""))
            out.append({
                "serial": serial, "primary": primary, "state": r.get("state"),
                "device": {**device, "serial": serial}, "model": device.get("model"),
                "error": r.get("error"), "input": r.get("antenna") or "",
                "antennas": r.get("antennas", []), "gain_steps": r.get("gain_steps", []),
                "gain_index": r.get("gain_index"), "identity": r.get("identity"),
                "bandwidth_hz": r.get("bandwidth_hz"), "sample_rate": r.get("sample_rate"),
                "center_hz": r.get("center_hz"), "dropped": r.get("dropped", 0),
                "overload": r.get("overload", False)})
        return out


def _percentiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"p50": ordered[len(ordered) // 2],
            "p99": ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]}


def judge_load(samples: dict[str, list[tuple[float, float, int]]], cfg: Config, cores: int,
               *, inline: bool = False) -> dict[str, Any]:
    """Verdict of the load test. Load of a worker = processor time / signal time (one core
    at most); station load = sum over the workers / number of cores. ``inline``: the
    workers are threads of one process, whose processor time counts them all."""
    c = cfg.campaign
    loads: dict[str, float | None] = {}
    dropped: dict[str, int] = {}
    for serial, rows in samples.items():
        audio = sum(r[1] for r in rows)
        loads[serial] = round(sum(r[0] for r in rows) / audio, 3) if audio > 0 else None
        dropped[serial] = rows[-1][2] - rows[0][2] if len(rows) > 1 else 0
    known = [v for v in loads.values() if v is not None]
    station = (max(known) if inline else sum(known) / cores) if known else None
    worst = max([*known, station or 0.0]) if known else None
    lost = sum(dropped.values())
    if worst is None or len(known) < len(loads):
        verdict, message = "refuse", "Un SDR n'a fourni aucune mesure pendant le test."
    elif worst >= c.load_max:
        verdict, message = "refuse", f"Charge {worst:.0%} : au-delà de {c.load_max:.0%}."
    elif worst >= c.load_ok or lost:
        verdict = "limit"
        message = f"Charge {worst:.0%}" + (f", {lost} échantillons perdus" if lost else "") + "."
    else:
        verdict, message = "ok", f"Charge {worst:.0%} : l'ordinateur suit sans difficulté."
    return {"verdict": verdict, "message": message, "loads": loads, "station_load": station,
            "dropped": dropped, "cores": cores}
