"""Gemeinsame Helfer der Fahrten-Tests (Phase 3). Enthält selbst keine Tests."""

from __future__ import annotations

import uuid
from typing import Any

from app.routing.base import LatLon
from tests.helpers import EVENT, NORTH, auth_cookies, wkt_point

START: LatLon = NORTH
RIDES = "/api/rides"
ALL_RIDE_KEYS = {
    "id",
    "start",
    "end",
    "departure_time",
    "seats_total",
    "seats_available",
    "status",
    "distance_km",
    "duration_min",
    "route",
    "created_at",
    "pending_requests",
    "accepted_requests",
}


def auth_headers(user: dict[str, Any]) -> dict[str, str]:
    """Cookie als Header (vermeidet die httpx-Deprecation von per-request-Cookies)."""
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies(user).items())}


def create_body(
    departure_time: str,
    *,
    start: LatLon = START,
    seats: int = 3,
) -> dict[str, Any]:
    return {
        "start": {"lat": start[0], "lon": start[1]},
        "departure_time": departure_time,
        "seats": seats,
    }


async def insert_request(
    db: Any, ride_id: uuid.UUID, rider_id: uuid.UUID, status: str = "pending"
) -> uuid.UUID:
    """Fügt eine Anfrage direkt ein (Phase 5 ist nicht nötig)."""
    request_id = uuid.uuid4()
    await db.execute(
        "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m, "
        "detour_duration_s, status, decided_at) VALUES ($1, $2, $3, ST_GeogFromText($4), 1000, "
        "120, $5::varchar, CASE WHEN $5::varchar = 'pending' THEN NULL ELSE now() END)",
        request_id,
        ride_id,
        rider_id,
        wkt_point(EVENT),
        status,
    )
    return request_id
