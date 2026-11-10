"""Gemeinsame Helfer der Phase-5-Tests (keine Tests in dieser Datei)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.routing.base import LatLon
from tests.helpers import wkt_point

#: Liegt nahe der Geraden NORTH → EVENT
PICKUP: LatLon = (53.65, 9.97)

BASE_DISTANCE_M = 30_000.0
BASE_DURATION_S = 1_800.0


def pickup_json(p: LatLon = PICKUP) -> dict[str, dict[str, float]]:
    return {"pickup": {"lat": p[0], "lon": p[1]}}


def small_detour(waypoints: Sequence[LatLon]) -> tuple[float, float]:
    """Route über den Pickup: genau +3 km und +4 min gegenüber der Basisroute (≈ 10 %)."""
    return BASE_DISTANCE_M + 3_000.0, BASE_DURATION_S + 240.0


def huge_detour(waypoints: Sequence[LatLon]) -> tuple[float, float]:
    """Unabhängig von der Schwellenkonfiguration deutlich zu groß (+100 km, +100 min)."""
    return BASE_DISTANCE_M + 100_000.0, BASE_DURATION_S + 6_000.0


async def insert_request(
    db: Any,
    ride_id: uuid.UUID,
    rider_id: uuid.UUID,
    *,
    status: str = "pending",
    pickup: LatLon = PICKUP,
    detour_distance_m: float = 3_000.0,
    detour_duration_s: float = 240.0,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Fügt eine Anfrage direkt ein. Bei ``accepted`` wird ein Platz der Fahrt belegt."""
    request_id = uuid.uuid4()
    await db.execute(
        "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m, "
        "detour_duration_s, status, created_at, decided_at) VALUES "
        "($1, $2, $3, ST_GeogFromText($4), $5, $6, $7::varchar, COALESCE($8, now()), "
        "CASE WHEN $7::varchar = 'pending' THEN NULL ELSE now() END)",
        request_id,
        ride_id,
        rider_id,
        wkt_point(pickup),
        detour_distance_m,
        detour_duration_s,
        status,
        created_at,
    )
    if status == "accepted":
        await db.execute(
            "UPDATE rides SET seats_available = seats_available - 1, "
            "status = CASE WHEN seats_available - 1 = 0 THEN 'full' ELSE status END WHERE id = $1",
            ride_id,
        )
    return request_id


async def ride_row(db: Any, ride_id: uuid.UUID) -> dict[str, Any]:
    row = await db.fetchrow(
        "SELECT seats_total, seats_available, status FROM rides WHERE id = $1", ride_id
    )
    return dict(row)


async def request_status(db: Any, request_id: uuid.UUID) -> str:
    return str(await db.fetchval("SELECT status FROM ride_requests WHERE id = $1", request_id))


async def assert_seat_invariant(db: Any) -> None:
    """Konsistenz-Invariante direkt in der DB: für jede nicht abgesagte Fahrt gilt

    ``seats_available == seats_total − #accepted``, ``0 ≤ seats_available``, und der Status ist
    ``full`` genau dann, wenn kein Platz frei ist.
    """
    rows = await db.fetch(
        "SELECT r.id, r.seats_total, r.seats_available, r.status, "
        "(SELECT count(*) FROM ride_requests q WHERE q.ride_id = r.id AND q.status = 'accepted') "
        "AS accepted FROM rides r WHERE r.status <> 'cancelled'"
    )
    for r in rows:
        assert r["seats_available"] >= 0, "seats_available darf nie negativ werden"
        assert r["seats_available"] == r["seats_total"] - r["accepted"], dict(r)
        assert (r["status"] == "full") == (r["seats_available"] == 0), dict(r)
