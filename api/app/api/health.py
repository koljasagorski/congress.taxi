"""Health-Endpunkte.

* ``/api/health``       – Liveness: Prozess läuft (für Docker-Healthcheck, keine Abhängigkeiten).
* ``/api/health/ready`` – Readiness: Datenbank (PostGIS) und OSRM; prüft zusätzlich, ob das
  Eventziel im OSRM-Extrakt liegt (sonst würde jede Route ins Leere/zu einer fernen Straße führen).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.routing import NoRouteError, RoutingClient, RoutingUnavailableError, get_routing_client

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


async def _check_db(session: AsyncSession) -> dict[str, Any]:
    try:
        version = (await session.execute(text("SELECT PostGIS_Version()"))).scalar_one()
    except Exception:  # Verbindungsfehler können SQLAlchemy- oder asyncpg-/OS-Fehler sein
        return {"ok": False, "error": "database_unavailable"}
    return {"ok": True, "postgis": str(version).split()[0]}


async def _check_osrm(routing: RoutingClient, settings: Settings) -> dict[str, Any]:
    try:
        snap = await routing.nearest((settings.event_lat, settings.event_lon))
    except NoRouteError:
        return {"ok": False, "error": "event_not_covered_by_osrm_extract"}
    except RoutingUnavailableError:
        return {"ok": False, "error": "osrm_unavailable"}
    covered = snap.distance_m <= settings.match_max_snap_distance_m
    result: dict[str, Any] = {"ok": covered, "event_snap_distance_m": round(snap.distance_m, 1)}
    if not covered:
        result["error"] = "event_too_far_from_road"
    return result


@router.get("/health/ready")
async def readiness(
    session: Annotated[AsyncSession, Depends(get_session)],
    routing: Annotated[RoutingClient, Depends(get_routing_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    checks = {
        "database": await _check_db(session),
        "osrm": await _check_osrm(routing, settings),
    }
    ready = all(c["ok"] for c in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ok" if ready else "degraded", "checks": checks},
    )
