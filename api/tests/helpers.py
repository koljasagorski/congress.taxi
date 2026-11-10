"""Test-Helfer: Nutzer und Fahrten direkt in der DB anlegen, Auth-Cookies erzeugen.

So sind die Phasen 3–5 nicht von Phase 2 (Register/Login) abhängig: Nutzer werden direkt
eingefügt, Anmeldung läuft über ein echtes Token (``app.auth.tokens``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth.tokens import COOKIE_NAME, create_access_token
from app.routing.base import LatLon

# Hamburg-Ebene: Eventziel (Platzhalter) und ein paar Punkte
EVENT: LatLon = (53.5615, 9.9885)
NORTH: LatLon = (53.75, 9.95)  # ~21 km nördlich
SOUTH_WEST: LatLon = (53.40, 9.70)


async def create_user(
    db: Any,
    *,
    email: str | None = None,
    display_name: str = "Testperson",
    password_hash: str = "!",  # noqa: S107 - nicht anmeldbar; Anmeldung über auth_cookies()
) -> dict[str, Any]:
    """Fügt einen Nutzer ein. ``db`` ist die ``db``-Fixture (asyncpg). Gibt ``{"id", "email"}``."""
    user_id = uuid.uuid4()
    email = email or f"user-{user_id.hex[:8]}@example.org"
    await db.execute(
        "INSERT INTO users (id, display_name, email, password_hash, consent_at) "
        "VALUES ($1, $2, $3, $4, now())",
        user_id,
        display_name,
        email,
        password_hash,
    )
    return {"id": user_id, "email": email, "display_name": display_name}


def auth_cookies(user: dict[str, Any]) -> dict[str, str]:
    """Cookie-Dict für ``client.get(..., cookies=auth_cookies(user))``."""
    return {COOKIE_NAME: create_access_token(user["id"])}


def future(hours: float = 24.0) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def wkt_point(p: LatLon) -> str:
    return f"POINT({p[1]} {p[0]})"


def wkt_line(points: Sequence[LatLon]) -> str:
    return "LINESTRING(" + ", ".join(f"{p[1]} {p[0]}" for p in points) + ")"


async def insert_ride(
    db: Any,
    driver_id: uuid.UUID,
    *,
    start: LatLon = NORTH,
    end: LatLon = EVENT,
    route: Sequence[LatLon] | None = None,
    distance_m: float = 30_000.0,
    duration_s: float = 1_800.0,
    seats_total: int = 3,
    seats_available: int | None = None,
    departure_time: datetime | None = None,
    status: str = "open",
) -> uuid.UUID:
    """Fügt eine Fahrt mit Route direkt ein (ohne OSRM). Ohne ``route``: Gerade start → end."""
    ride_id = uuid.uuid4()
    line = route or [start, end]
    await db.execute(
        "INSERT INTO rides (id, driver_id, start_geom, end_geom, route_geom, route_distance_m, "
        "route_duration_s, seats_total, seats_available, departure_time, status) VALUES "
        "($1, $2, ST_GeogFromText($3), ST_GeogFromText($4), ST_GeogFromText($5), $6, $7, $8, $9, "
        "$10, $11)",
        ride_id,
        driver_id,
        wkt_point(start),
        wkt_point(end),
        wkt_line(line),
        distance_m,
        duration_s,
        seats_total,
        seats_total if seats_available is None else seats_available,
        departure_time or future(),
        status,
    )
    return ride_id
