"""FastAPI application.

Run locally:  uvicorn orbitlink.api:app --reload
Environment:  DATABASE_URL (default sqlite:///orbitlink.db)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .commands import Priority
from .db import Database
from .limits import Limit
from .orbit import Station
from .service import GroundSystem, utcnow


class FrameIn(BaseModel):
    hex: str = Field(..., description="raw downlink bytes, hex encoded")
    received_at: Optional[datetime] = None


class CommandIn(BaseModel):
    name: str
    args: dict[str, Any] = {}
    priority: str = "NORMAL"


class LimitIn(BaseModel):
    red_low: Optional[float] = None
    yellow_low: Optional[float] = None
    yellow_high: Optional[float] = None
    red_high: Optional[float] = None
    persistence: int = Field(1, ge=1)
    enabled: bool = True


class StationIn(BaseModel):
    name: str
    lat_deg: float = Field(..., ge=-90, le=90)
    lon_deg: float = Field(..., ge=-180, le=180)
    alt_m: float = 0.0
    min_el_deg: float = Field(10.0, ge=0, le=60)


class TimelineIn(BaseModel):
    start: datetime
    end: datetime
    kind: str
    title: str


class UplinkIn(BaseModel):
    remaining_s: float = Field(float("inf"), description="seconds until LOS")
    frame_time_s: float = 0.0


def create_app(db: Optional[Database] = None, seed_stations: bool = True) -> FastAPI:
    database = db or Database.from_env()
    gs = GroundSystem(database)
    if seed_stations and not gs.stations():
        for st in DEFAULT_STATIONS:
            gs.add_station(st)

    app = FastAPI(title="OrbitLink ground system", version="0.3.0")
    app.state.gs = gs
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "db": database.dialect}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return gs.status()

    # ---------------------------------------------------------------- telemetry
    @app.post("/api/telemetry/frames")
    def ingest(frame: FrameIn) -> dict[str, Any]:
        try:
            raw = bytes.fromhex(frame.hex)
        except ValueError as exc:
            raise HTTPException(400, "invalid hex") from exc
        return gs.ingest(raw, frame.received_at or utcnow())

    @app.get("/api/telemetry/latest")
    def latest() -> dict[str, Any]:
        return {"received_at": gs.latest_hk_at.isoformat() if gs.latest_hk_at else None,
                "hk": gs.latest_hk, "limit_states": gs.limit_states()}

    @app.get("/api/telemetry/history/{param}")
    def history(param: str, limit: int = Query(200, ge=1, le=5000)) -> list[dict[str, Any]]:
        return gs.history(param, limit)

    # ---------------------------------------------------------------- limits
    @app.get("/api/limits")
    def limits() -> list[dict[str, Any]]:
        return database.query("SELECT * FROM limits ORDER BY param")

    @app.put("/api/limits/{param}")
    def put_limit(param: str, body: LimitIn) -> dict[str, Any]:
        gs.save_limit(Limit(param, body.red_low, body.yellow_low, body.yellow_high, body.red_high,
                            body.persistence, body.enabled))
        return {"param": param, **body.model_dump()}

    # ---------------------------------------------------------------- alerts / events
    @app.get("/api/alerts")
    def alerts(open_only: bool = False, limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        if open_only:
            return database.query("SELECT * FROM alerts WHERE acknowledged = ? ORDER BY id DESC LIMIT ?", (False, limit))
        return database.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    @app.post("/api/alerts/{alert_id}/ack")
    def ack_alert(alert_id: int) -> dict[str, Any]:
        if not gs.ack_alert(alert_id):
            raise HTTPException(404, "no such alert")
        return {"id": alert_id, "acknowledged": True}

    @app.get("/api/events")
    def events(category: Optional[str] = None, limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
        if category:
            return database.query("SELECT * FROM events WHERE category = ? ORDER BY id DESC LIMIT ?", (category, limit))
        return database.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- commands
    @app.post("/api/commands", status_code=201)
    def submit(body: CommandIn) -> dict[str, Any]:
        try:
            prio = Priority[body.priority.upper()]
        except KeyError as exc:
            raise HTTPException(422, f"unknown priority {body.priority}") from exc
        try:
            cmd = gs.submit_command(body.name, body.args, prio)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": cmd.id, "state": cmd.state, "priority": prio.name}

    @app.get("/api/commands")
    def list_commands(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        return gs.commands(limit)

    @app.get("/api/commands/{cmd_id}")
    def get_command(cmd_id: int) -> dict[str, Any]:
        row = database.one("SELECT * FROM commands WHERE id = ?", (cmd_id,))
        if row is None:
            raise HTTPException(404, "no such command")
        return row

    @app.post("/api/link/uplink")
    def uplink(body: UplinkIn) -> dict[str, Any]:
        """Front-end processor hook: fetch the next TC frame to radiate during a contact."""
        frame = gs.next_uplink(utcnow(), body.remaining_s, body.frame_time_s)
        return {"frame_hex": frame.hex() if frame else None, "queue_depth": len(gs.queue.pending)}

    # ---------------------------------------------------------------- stations / passes / timeline
    @app.get("/api/stations")
    def stations() -> list[dict[str, Any]]:
        return database.query("SELECT * FROM stations ORDER BY id")

    @app.post("/api/stations", status_code=201)
    def add_station(body: StationIn) -> dict[str, Any]:
        if database.one("SELECT id FROM stations WHERE name = ?", (body.name,)):
            raise HTTPException(409, "station exists")
        sid = gs.add_station(Station(body.name, body.lat_deg, body.lon_deg, body.alt_m, body.min_el_deg))
        return {"id": sid, **body.model_dump()}

    @app.post("/api/passes/plan")
    def plan(hours: float = Query(24.0, gt=0, le=24 * 14), start: Optional[datetime] = None) -> dict[str, Any]:
        s = start or utcnow()
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        allp = gs.plan_passes(s, hours)
        return {"predicted": len(allp), "scheduled": sum(p.scheduled for p in allp),
                "passes": [p.to_dict() for p in allp]}

    @app.get("/api/passes")
    def passes(limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
        return gs.passes(limit=limit)

    @app.get("/api/timeline")
    def timeline(hours: float = Query(24.0, gt=0, le=24 * 14)) -> list[dict[str, Any]]:
        now = utcnow()
        return gs.timeline(now - timedelta(hours=1), now + timedelta(hours=hours))

    @app.post("/api/timeline", status_code=201)
    def add_timeline(body: TimelineIn) -> dict[str, Any]:
        try:
            tid = gs.add_timeline(body.start, body.end, body.kind, body.title)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": tid}

    @app.get("/api/groundtrack")
    def groundtrack() -> dict[str, float]:
        return gs.ground_track(utcnow())

    return app


DEFAULT_STATIONS = [
    Station("toronto", 43.66, -79.40, 110.0, 10.0),
    Station("svalbard", 78.23, 15.41, 450.0, 5.0),
    Station("punta_arenas", -53.15, -70.92, 30.0, 10.0),
]


def _lazy_app() -> FastAPI:
    return create_app()


class _LazyApp:
    """Defer DB creation until the ASGI server first touches the app."""

    _app: Optional[FastAPI] = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = _lazy_app()
        await self._app(scope, receive, send)


app = _LazyApp()
