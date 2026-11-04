"""REST routes to prepare, start and stop a campaign."""

from __future__ import annotations

import json
import shutil
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from open_antenna_validation import campaign_manager as cm
from open_antenna_validation.hardware import HardwareError
from open_antenna_validation.installation import list_antennas
from open_antenna_validation.service import Station
from open_antenna_validation.web.auth import require_auth


class FrequencyBody(BaseModel):
    freq_hz: float = Field(gt=0)
    class_: Literal["carrier", "digital", "ssb", "am_fm"] = Field(default="carrier", alias="class")
    width_hz: float | None = Field(default=None, gt=0, le=20_000)


class ChannelBody(BaseModel):
    antenna_id: int


class CampaignBody(BaseModel):
    title: str = ""
    mode: Literal["fixed", "auto"] = "fixed"
    frequencies: list[FrequencyBody] = Field(default_factory=list)
    low_hz: float | None = Field(default=None, gt=0)
    high_hz: float | None = Field(default=None, gt=0)
    max_signals: int | None = Field(default=None, ge=1, le=20)
    channels: list[ChannelBody]
    reference: int = 0
    duration_s: float
    rate_hz: float | None = Field(default=None, gt=0, le=200)
    aa: bool = False
    accept_limits: bool = False


def build_router(station: Station) -> APIRouter:
    router = APIRouter(prefix="/api/campaign", dependencies=[Depends(require_auth)])

    def profile_summary(band: str) -> dict[str, Any] | None:
        p = cm.active_profile(station.db, station.receiver.get("identity"), band)
        if p is None:
            return None
        margins = json.loads(p["margins_json"])
        return {"band": p["band"], "blanking_ms": p["blanking_ms"],
                "useful": p["useful_fraction"], "accepted": p["accepted_ms"] is not None,
                "valid_until_ms": p["valid_until_ms"], "verdicts": margins.get("verdicts", []),
                "margins": margins.get("margins"), "switch_rate_hz": p["switch_rate_hz"]}

    @router.get("/setup")
    def setup() -> dict[str, Any]:
        cfg = station.config
        free = shutil.disk_usage(station.db.path.parent).free
        return {
            "receiver": station.hardware_report(),
            "antennas": list_antennas(station.db),
            "bands": [b.model_dump() for b in cfg.bands],
            "class_width_hz": cfg.campaign.class_width_hz,
            "rate_hz": cfg.qualification.switch_rate_hz,
            "default_blanking_ms": cfg.campaign.default_blanking_ms,
            "period_s": cfg.campaign.period_s,
            "windows": cfg.campaign.windows,
            "max_duration_days": cfg.campaign.max_duration_days,
            "max_signals": cfg.campaign.max_signals, "survey_s": cfg.campaign.survey_s,
            "reevaluate_s": cfg.campaign.reevaluate_s,
            "profiles": {b.name: profile_summary(b.name) for b in cfg.bands},
            "disk_free_bytes": free,
            "campaign": station.campaign_report(),
        }

    @router.post("")
    def start(body: CampaignBody) -> dict[str, Any]:
        request = body.model_dump(by_alias=True)
        try:
            uuid = station.start_campaign(request)
        except (cm.CampaignError, HardwareError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"uuid": uuid, "campaign": station.campaign_report()}

    @router.post("/stop")
    def stop() -> dict[str, Any]:
        try:
            station.stop_campaign()
        except (cm.CampaignError, HardwareError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"campaign": None}

    return router
