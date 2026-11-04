"""FastAPI application: REST API, WebSockets, static page, session authentication."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from open_antenna_validation import __version__
from open_antenna_validation.campaigns import CampaignNotFound, campaign_results, list_campaigns
from open_antenna_validation.config import Antenna
from open_antenna_validation.hardware import HardwareError
from open_antenna_validation.service import TOPIC_SPECTRUM, TOPIC_STATUS, Station
from open_antenna_validation.web import campaign_routes, installation_routes, qualification_routes
from open_antenna_validation.web.auth import (
    SESSION_KEY,
    is_authenticated,
    require_auth,
    verify_password,
)

log = logging.getLogger(__name__)

# The Windows registry may map .js to text/plain, which browsers refuse for modules.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("font/woff2", ".woff2")

# Slows down password guessing from the local network.
FAILED_LOGIN_DELAY_S = 1.0
# WebSocket close code for "policy violation": used for missing authentication.
WS_UNAUTHORIZED = 1008


def static_dir() -> Path:
    """``web/`` from a source checkout, or the copy packaged in the wheel."""
    source = Path(__file__).resolve().parents[3] / "web"
    if (source / "index.html").exists():
        return source
    return Path(str(resources.files("open_antenna_validation") / "_web"))


class LoginRequest(BaseModel):
    password: str


class AntennaRequest(BaseModel):
    antenna: Antenna


class FrequencyRequest(BaseModel):
    center_hz: float = Field(gt=0)


class GainRequest(BaseModel):
    index: int = Field(ge=0)


def create_app(station: Station, *, run_lifecycle: bool = True) -> FastAPI:
    web = station.config.web
    secret = web.session_secret
    if not secret:
        log.warning("session_secret absent de la configuration : sessions perdues au redémarrage")
        secret = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if run_lifecycle:
            await asyncio.to_thread(station.startup)
        yield
        if run_lifecycle:
            await asyncio.to_thread(station.shutdown)

    app = FastAPI(title="openAntennaValidation", version=__version__, lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="oav_session",
        max_age=web.session_max_age_hours * 3600,
        same_site="strict",
        https_only=web.https_only,
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    root = static_dir()

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(root / "index.html")

    def page(request: Request, name: str) -> Response:
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(root / name)

    @app.get("/campaigns", include_in_schema=False)
    def campaigns_page(request: Request) -> Response:
        return page(request, "campaigns.html")

    @app.get("/campaign", include_in_schema=False)
    def campaign_page(request: Request) -> Response:
        return page(request, "campaign.html")

    @app.get("/campaigns/new", include_in_schema=False)
    def new_campaign_page(request: Request) -> Response:
        return page(request, "new-campaign.html")

    @app.get("/installation", include_in_schema=False)
    def installation_page(request: Request) -> Response:
        return page(request, "installation.html")

    @app.get("/qualification", include_in_schema=False)
    def qualification_page(request: Request) -> Response:
        return page(request, "qualification.html")

    @app.get("/login", include_in_schema=False)
    def login_page() -> FileResponse:
        return FileResponse(root / "login.html")

    @app.get("/api/session")
    def session(request: Request) -> dict[str, bool]:
        return {
            "authenticated": is_authenticated(request),
            "password_set": bool(station.config.web.password_hash),
        }

    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request) -> dict[str, bool]:
        if not verify_password(station.config.web.password_hash, body.password):
            await asyncio.sleep(FAILED_LOGIN_DELAY_S)
            raise HTTPException(status_code=401, detail="Mot de passe incorrect.")
        request.session.clear()
        request.session[SESSION_KEY] = True
        return {"authenticated": True}

    @app.post("/api/logout")
    def logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"authenticated": False}

    @app.get("/api/status", dependencies=[Depends(require_auth)])
    def status() -> dict[str, Any]:
        s = station.config.station
        return {
            "version": __version__,
            "station": {"id": s.id, "name": s.name, "callsign": s.callsign, "locator": s.locator},
            "hardware": station.hardware_report(),
        }

    @app.get("/api/hardware", dependencies=[Depends(require_auth)])
    def hardware() -> dict[str, Any]:
        return station.hardware_report()

    def _apply(action: Any) -> dict[str, Any]:
        try:
            action()
        except HardwareError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return station.hardware_report()

    @app.post("/api/hardware/antenna", dependencies=[Depends(require_auth)])
    def set_antenna(body: AntennaRequest) -> dict[str, Any]:
        return _apply(lambda: station.set_antenna(body.antenna))

    @app.post("/api/hardware/gain", dependencies=[Depends(require_auth)])
    def set_gain(body: GainRequest) -> dict[str, Any]:
        return _apply(lambda: station.set_gain_step(body.index))

    @app.post("/api/receiver/frequency", dependencies=[Depends(require_auth)])
    def set_frequency(body: FrequencyRequest) -> dict[str, Any]:
        return _apply(lambda: station.set_frequency(body.center_hz))

    @app.get("/api/bands", dependencies=[Depends(require_auth)])
    def bands() -> list[dict[str, Any]]:
        return [b.model_dump() for b in station.config.bands]

    @app.get("/api/campaigns", dependencies=[Depends(require_auth)])
    def campaigns() -> list[dict[str, Any]]:
        return list_campaigns(station.db, show_demo=station.config.demo.enabled)

    @app.get("/api/campaigns/{uuid}/results", dependencies=[Depends(require_auth)])
    def results(uuid: str) -> dict[str, Any]:
        try:
            return campaign_results(station.db, uuid)
        except CampaignNotFound as exc:
            raise HTTPException(status_code=404, detail="Campagne introuvable.") from exc

    async def stream(websocket: WebSocket, topic: str, first: Any) -> None:
        if not websocket.session.get(SESSION_KEY):
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        await websocket.accept()
        try:
            async with station.hub.subscribe(topic) as queue:
                if first is not None:
                    await websocket.send_json(first)
                while True:
                    await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/spectrum")
    async def ws_spectrum(websocket: WebSocket) -> None:
        await stream(websocket, TOPIC_SPECTRUM, station.last_spectrum)

    @app.websocket("/ws/status")
    async def ws_status(websocket: WebSocket) -> None:
        await stream(websocket, TOPIC_STATUS, station.hardware_report())

    app.include_router(qualification_routes.build_router(station))
    app.include_router(installation_routes.build_router(station))
    app.include_router(campaign_routes.build_router(station))
    app.mount("/static", StaticFiles(directory=root), name="static")
    return app
