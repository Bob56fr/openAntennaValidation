"""REST routes of the operator installation: antennas, receive chains, cable catalogue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from open_antenna_validation import installation as inst
from open_antenna_validation.service import Station
from open_antenna_validation.web.auth import require_auth


class AntennaBody(BaseModel):
    type: str
    name: str
    model: str = ""
    polarization: str = ""
    height_m: float | None = None
    height_note: str = ""
    azimuth_deg: float | None = None
    orientation_note: str = ""
    bands_note: str = ""


class ElementBody(BaseModel):
    kind: str
    label: str = ""
    cable_type: str | None = None
    length_m: float | None = None
    db: float | None = None


class ChainBody(BaseModel):
    elements: list[ElementBody] = Field(default_factory=list)
    note: str = ""


class LossBody(BaseModel):
    elements: list[ElementBody]
    freq_hz: float = Field(gt=0)


def build_router(station: Station) -> APIRouter:
    router = APIRouter(prefix="/api/installation", dependencies=[Depends(require_auth)])

    def guarded(action: Any) -> Any:
        try:
            return action()
        except inst.InstallationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def payload() -> dict[str, Any]:
        return {**inst.catalogue(), "antennas": inst.list_antennas(station.db),
                "center_hz": station.receiver.get("center_hz")
                or station.config.receiver.center_frequency_hz}

    @router.get("")
    def overview() -> dict[str, Any]:
        return payload()

    @router.post("/antennas")
    def create(body: AntennaBody) -> dict[str, Any]:
        created = guarded(lambda: inst.save_antenna(station.db, body.model_dump()))
        return {**payload(), "created_id": created}

    @router.put("/antennas/{antenna_id}")
    def update(antenna_id: int, body: AntennaBody) -> dict[str, Any]:
        guarded(lambda: inst.save_antenna(station.db, body.model_dump(), antenna_id))
        return payload()

    @router.delete("/antennas/{antenna_id}")
    def archive(antenna_id: int) -> dict[str, Any]:
        inst.archive_antenna(station.db, antenna_id)
        return payload()

    @router.post("/antennas/{antenna_id}/chain")
    def save_chain(antenna_id: int, body: ChainBody) -> dict[str, Any]:
        elements = [e.model_dump(exclude_none=True) for e in body.elements]
        guarded(lambda: inst.save_chain(station.db, antenna_id, elements, body.note))
        return payload()

    @router.post("/chain-loss")
    def chain_loss(body: LossBody) -> dict[str, Any]:
        def compute() -> dict[str, Any]:
            losses = []
            for e in body.elements:
                checked = inst.check_element(e.model_dump(exclude_none=True))
                losses.append(round(inst.element_loss_db(checked, body.freq_hz), 2))
            return {"elements_db": losses, "total_db": round(sum(losses), 2)}
        result: dict[str, Any] = guarded(compute)
        return result

    return router
