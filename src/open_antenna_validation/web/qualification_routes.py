"""REST routes of the qualification bench."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from open_antenna_validation import qualification as q
from open_antenna_validation.hardware import HardwareError
from open_antenna_validation.service import Station
from open_antenna_validation.web.auth import require_auth


class NewRun(BaseModel):
    attenuator_db: float | None = Field(default=None, gt=0, le=40)


def build_router(station: Station) -> APIRouter:
    router = APIRouter(prefix="/api/qualification", dependencies=[Depends(require_auth)])
    cfg = station.config.qualification

    def guarded(action: Any) -> Any:
        try:
            return action()
        except (q.QualificationError, HardwareError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def band_name() -> str:
        center = station.receiver.get("center_hz") or station.config.receiver.center_frequency_hz
        for band in station.config.bands:
            if band.low_hz <= center <= band.high_hz:
                return band.name
        return ""

    @router.get("")
    def overview() -> dict[str, Any]:
        latest = q.latest_run(station.db)
        return {
            "steps": q.steps_payload(),
            "settings": {"attenuator_db": cfg.attenuator_db, "switch_rate_hz": cfg.switch_rate_hz,
                         "class_rate_hz": cfg.class_rate_hz, "useful_ok": cfg.useful_ok,
                         "useful_min": cfg.useful_min, "switches": cfg.switches,
                         "dwell_ms": cfg.dwell_ms},
            "bench": station.bench,
            "receiver": station.hardware_report(),
            "run": q.run_payload(station.db, latest, cfg) if latest else None,
        }

    @router.post("/runs")
    def new_run(body: NewRun) -> dict[str, Any]:
        def create() -> dict[str, Any]:
            run = q.create_run(station.db, receiver=station.receiver, band=band_name(),
                               attenuator_db=body.attenuator_db or cfg.attenuator_db,
                               switch_rate_hz=cfg.switch_rate_hz)
            return q.run_payload(station.db, run, cfg)
        result: dict[str, Any] = guarded(create)
        return result

    @router.post("/runs/{run}/steps/{step}", status_code=202)
    def start_step(run: str, step: str) -> dict[str, Any]:
        guarded(lambda: station.start_bench_step(run, step))
        return station.bench

    @router.post("/runs/{run}/finish")
    def finish(run: str) -> dict[str, Any]:
        result: dict[str, Any] = guarded(lambda: q.finish(station.db, run, cfg))
        return result

    @router.post("/runs/{run}/accept")
    def accept(run: str) -> dict[str, Any]:
        result: dict[str, Any] = guarded(lambda: q.accept(station.db, run, cfg))
        return result

    return router
