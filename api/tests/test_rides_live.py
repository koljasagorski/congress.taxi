"""Live-Tests gegen echtes OSRM (Hamburg-Extrakt, Marker ``osrm``): echte Fahrt anlegen."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from app.config import get_settings
from app.routing import NoRouteError, OsrmClient, RoutingUnavailableError, get_routing_client
from tests.fakes import haversine_m
from tests.helpers import create_user, future
from tests.test_rides_common import auth_headers, create_body

pytestmark = [pytest.mark.osrm, pytest.mark.asyncio(loop_scope="session")]

ALTONA = (53.5511, 9.9265)
HARBURG = (53.4609, 9.9831)  # südlich der Elbe
MUENCHEN = (48.1351, 11.5820)  # weit außerhalb des Extrakts


@pytest_asyncio.fixture(loop_scope="session")
async def live(client: httpx.AsyncClient) -> AsyncIterator[OsrmClient]:
    """Ersetzt den Fake des ``client`` durch einen echten OSRM-Client (sonst Skip)."""
    from app.main import app

    osrm = OsrmClient(os.environ.get("OSRM_URL", "http://127.0.0.1:55000"), timeout_s=10)
    settings = get_settings()
    try:
        await osrm.nearest((settings.event_lat, settings.event_lon))
    except (RoutingUnavailableError, NoRouteError):
        await osrm.aclose()
        pytest.skip("OSRM mit Hamburg-Extrakt nicht erreichbar")
    app.dependency_overrides[get_routing_client] = lambda: osrm
    yield osrm
    await osrm.aclose()


@pytest.mark.parametrize("start", [ALTONA, HARBURG], ids=["altona", "harburg_ueber_die_elbe"])
async def test_create_real_ride_has_a_plausible_route(
    client: httpx.AsyncClient, db: Any, live: OsrmClient, start: tuple[float, float]
) -> None:
    settings = get_settings()
    event = (settings.event_lat, settings.event_lon)
    driver = await create_user(db)
    resp = await client.post(
        "/api/rides",
        json=create_body(future(48).isoformat(), start=start, seats=3),
        headers=auth_headers(driver),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    straight_km = haversine_m(start, event) / 1000
    # Straßenlänge: nie kürzer als die Luftlinie, aber im Stadtgebiet auch nicht ein Vielfaches
    assert straight_km * 0.95 <= body["distance_km"] <= straight_km * 3.0 + 2
    assert 1 <= body["duration_min"] <= 60
    route = body["route"]
    assert len(route) >= 2
    # Start/Ende der Route liegen nahe den Eingaben (Snap ≤ MATCH_MAX_SNAP_DISTANCE_M)
    limit = settings.match_max_snap_distance_m + 50
    assert haversine_m(tuple(route[0]), start) < limit  # type: ignore[arg-type]
    assert haversine_m(tuple(route[-1]), event) < limit  # type: ignore[arg-type]
    # Alle Punkte liegen in Hamburg (lat, lon – nicht vertauscht)
    assert all(53.3 < lat < 53.8 and 9.6 < lon < 10.4 for lat, lon in route)

    row = await db.fetchrow(
        "SELECT ST_GeometryType(route_geom::geometry) AS t, ST_NPoints(route_geom::geometry) AS n,"
        " ST_Length(route_geom) AS len_m, route_distance_m, route_duration_s,"
        " ST_Distance(start_geom, ST_GeogFromText($2)) AS start_off,"
        " ST_Distance(end_geom, ST_GeogFromText($3)) AS end_off"
        " FROM rides WHERE id = $1::uuid",
        body["id"],
        f"POINT({start[1]} {start[0]})",
        f"POINT({event[1]} {event[0]})",
    )
    assert row["t"] == "ST_LineString"
    assert row["n"] > len(route)  # gespeichert ist die volle, die Antwort die vereinfachte Route
    assert row["start_off"] < 0.5 and row["end_off"] < 0.5  # gelieferter Punkt, nicht gesnappt
    # gespeicherte Länge der Geometrie ≈ von OSRM gemeldete Distanz (Rundung/Geometrieglättung)
    assert row["len_m"] == pytest.approx(row["route_distance_m"], rel=0.05)
    assert row["route_duration_s"] > 0


async def test_real_osrm_rejects_start_outside_the_extract(
    client: httpx.AsyncClient, db: Any, live: OsrmClient
) -> None:
    driver = await create_user(db)
    resp = await client.post(
        "/api/rides",
        json=create_body(future().isoformat(), start=MUENCHEN),
        headers=auth_headers(driver),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "outside_coverage"
    assert await db.fetchval("SELECT count(*) FROM rides") == 0
