"""REST routes of the publication on the WordPress site."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from open_antenna_validation.publish import PublishError
from open_antenna_validation.service import Station
from open_antenna_validation.web.auth import require_auth


class SettingsBody(BaseModel):
    enabled: bool
    url: str = Field(default="", max_length=300)
    # Empty: keep the token already saved (it is never sent back to the page).
    token: str = Field(default="", max_length=200)


class TestBody(BaseModel):
    # What the form holds; empty: the saved values.
    url: str = Field(default="", max_length=300)
    token: str = Field(default="", max_length=200)


class VisibilityBody(BaseModel):
    visibility: Literal["private", "public", "unlisted"]


def build_router(station: Station) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
    pub = station.publisher

    @router.get("/wordpress")
    def report() -> dict[str, Any]:
        return {**pub.report(), "campaigns": pub.campaign_states()}

    @router.put("/wordpress")
    def settings(body: SettingsBody) -> dict[str, Any]:
        url = body.url.strip().rstrip("/")
        if body.enabled and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400,
                                detail="Adresse du site invalide (http:// ou https://).")
        wp = station.config.wordpress
        wp.enabled, wp.url = body.enabled, url
        if body.token.strip():
            wp.token = body.token.strip()
        if wp.enabled and not wp.token:
            raise HTTPException(status_code=400, detail="Jeton de la station manquant.")
        station.save_config()
        pub.limits = None
        pub.trigger()
        return report()

    @router.post("/wordpress/test")
    def test(body: TestBody | None = None) -> dict[str, Any]:
        body = body or TestBody()
        if body.url.strip() and not body.url.strip().startswith(("http://", "https://")):
            raise HTTPException(status_code=400,
                                detail="Adresse du site invalide (http:// ou https://).")
        try:
            return {"limits": pub.test(body.url, body.token)}
        except PublishError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/wordpress/estimate")
    def estimate(duration_s: float, n_signals: int, n_channels: int,
                 window_s: float) -> dict[str, Any]:
        return pub.estimate(duration_s, n_signals, n_channels, window_s)

    @router.post("/campaigns/{uuid}/visibility")
    def visibility(uuid: str, body: VisibilityBody) -> dict[str, Any]:
        rows = station.db.query("SELECT id, params_json FROM campaigns WHERE uuid = ?", (uuid,))
        if not rows or '"demo": true' in rows[0]["params_json"]:
            raise HTTPException(status_code=404, detail="Campagne introuvable.")
        with station.db.transaction() as conn:
            conn.execute("UPDATE campaigns SET visibility = ? WHERE id = ?",
                         (body.visibility, rows[0]["id"]))
            # Refused as too large: try again, with a window chosen afresh if nothing left yet.
            conn.execute("UPDATE publication SET state = 'pending', message = '',"
                         " window_ms = CASE WHEN COALESCE(sent_until_ms, 0) = 0 THEN NULL"
                         " ELSE window_ms END WHERE campaign_id = ? AND state = 'too_large'",
                         (rows[0]["id"],))
        pub.trigger()
        return {"visibility": body.visibility,
                "publication": pub.campaign_states().get(uuid)}

    return router
