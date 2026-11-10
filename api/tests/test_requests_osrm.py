"""Anfragen gegen echtes OSRM mit Hamburg-Extrakt (Marker ``osrm``; sonst übersprungen)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from app.routing import NoRouteError, OsrmClient, RoutingUnavailableError, get_routing_client
from tests.helpers import EVENT, auth_cookies, create_user, insert_ride
from tests.test_requests_common import assert_seat_invariant, pickup_json

pytestmark = [pytest.mark.osrm, pytest.mark.asyncio(loop_scope="session")]

HARBURG = (53.4609, 9.9831)  # südlich der Elbe
WILHELMSBURG = (53.50, 9.99)  # liegt auf dem Weg Harburg -> Event
AIRPORT = (53.6304, 9.9882)  # Umweg nach Norden (Messung: +124 %, +33 min, Snap ≈ 290 m)
MUENCHEN = (48.1351, 11.5820)  # weit außerhalb des Extrakts


@pytest_asyncio.fixture(loop_scope="session")
async def osrm(client: httpx.AsyncClient) -> AsyncIterator[OsrmClient]:
    from app.main import app

    c = OsrmClient(os.environ.get("OSRM_URL", "http://127.0.0.1:55000"), timeout_s=5)
    try:
        await c.nearest(EVENT)
    except (RoutingUnavailableError, NoRouteError):
        await c.aclose()
        pytest.skip("OSRM mit Hamburg-Extrakt nicht erreichbar")
    app.dependency_overrides[get_routing_client] = lambda: c
    yield c
    await c.aclose()


async def _ride_with_real_base_route(db: Any, osrm: OsrmClient) -> tuple[Any, uuid.UUID]:
    driver = await create_user(db)
    base = await osrm.route([HARBURG, EVENT], geometry=True)
    ride_id = await insert_ride(
        db,
        driver["id"],
        start=HARBURG,
        end=EVENT,
        route=list(base.geometry),
        distance_m=base.distance_m,
        duration_s=base.duration_s,
    )
    return driver, ride_id


async def _post(
    client: httpx.AsyncClient, rider: dict[str, Any], ride_id: uuid.UUID, pickup: Any
) -> httpx.Response:
    client.cookies.clear()
    for k, v in auth_cookies(rider).items():
        client.cookies.set(k, v)
    return await client.post(f"/api/rides/{ride_id}/requests", json=pickup_json(pickup))


async def test_pickup_on_the_way_is_accepted_with_realistic_detour(
    client: httpx.AsyncClient, db: Any, osrm: OsrmClient
) -> None:
    _driver, ride_id = await _ride_with_real_base_route(db, osrm)
    rider = await create_user(db)
    r = await _post(client, rider, ride_id, WILHELMSBURG)
    assert r.status_code == 201, r.text
    body = r.json()
    assert 0.0 <= body["detour_km"] < 6.0  # gemessen: ≈ 1,5 km
    assert 0.0 <= body["detour_minutes"] < 15.0
    await assert_seat_invariant(db)


async def test_pickup_with_big_detour_is_rejected(
    client: httpx.AsyncClient, db: Any, osrm: OsrmClient
) -> None:
    _driver, ride_id = await _ride_with_real_base_route(db, osrm)
    rider = await create_user(db)
    r = await _post(client, rider, ride_id, AIRPORT)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "detour_too_large"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


async def test_pickup_far_outside_extract_is_rejected_instead_of_silently_snapped(
    client: httpx.AsyncClient, db: Any, osrm: OsrmClient
) -> None:
    # OSRM würde München still auf eine ferne Straße snappen; die Snap-Prüfung muss greifen.
    _driver, ride_id = await _ride_with_real_base_route(db, osrm)
    rider = await create_user(db)
    r = await _post(client, rider, ride_id, MUENCHEN)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "outside_coverage"


async def test_osrm_down_is_503(client: httpx.AsyncClient, db: Any, osrm: OsrmClient) -> None:
    from app.main import app

    _driver, ride_id = await _ride_with_real_base_route(db, osrm)
    rider = await create_user(db)
    dead = OsrmClient("http://127.0.0.1:9", timeout_s=1)  # Port 9 (discard): nichts lauscht
    app.dependency_overrides[get_routing_client] = lambda: dead
    try:
        r = await _post(client, rider, ride_id, WILHELMSBURG)
    finally:
        await dead.aclose()
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "routing_unavailable"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0
