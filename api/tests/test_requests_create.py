"""POST /api/rides/{id}/requests – Anlegen einer Anfrage (Vertrag: Abschnitt „Anfragen“)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.routing import RoutingUnavailableError
from app.routing.base import LatLon
from tests.fakes import FakeRouting, haversine_m
from tests.helpers import EVENT, NORTH, auth_cookies, create_user, insert_ride
from tests.test_requests_common import (
    BASE_DISTANCE_M,
    BASE_DURATION_S,
    PICKUP,
    assert_seat_invariant,
    huge_detour,
    insert_request,
    pickup_json,
    ride_row,
    small_detour,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _world(db: Any, **ride_kwargs: Any) -> tuple[dict[str, Any], dict[str, Any], uuid.UUID]:
    driver = await create_user(db, display_name="Dora Fahrerin")
    rider = await create_user(db, display_name="Rainer Mitfahrer")
    ride_id = await insert_ride(
        db, driver["id"], distance_m=BASE_DISTANCE_M, duration_s=BASE_DURATION_S, **ride_kwargs
    )
    return driver, rider, ride_id


async def _post(
    client: httpx.AsyncClient, user: dict[str, Any], ride_id: uuid.UUID, pickup: LatLon = PICKUP
) -> httpx.Response:
    client.cookies.clear()
    for k, v in auth_cookies(user).items():
        client.cookies.set(k, v)
    return await client.post(f"/api/rides/{ride_id}/requests", json=pickup_json(pickup))


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    r = await client.post(f"/api/rides/{uuid.uuid4()}/requests", json=pickup_json())
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_create_returns_pending_request_with_server_side_detour(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)

    r = await _post(client, rider, ride_id)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["ride_id"] == str(ride_id)
    assert body["pickup"] == {"lat": PICKUP[0], "lon": PICKUP[1]}
    assert body["detour_km"] == 3.0
    assert body["detour_minutes"] == 4
    assert isinstance(body["detour_minutes"], int)  # ganze Minuten wie bei /api/match
    assert body["decided_at"] is None
    assert body["created_at"].endswith(("Z", "+00:00"))
    assert body["ride"]["driver_display_name"] == "Dora Fahrerin"
    assert body["ride"]["seats_available"] == 3
    assert body["rider"] == {"display_name": "Rainer Mitfahrer"}
    assert body["counterpart_email"] is None
    # Der Vertrag nennt keine Nutzer-IDs oder Passwort-Hashes
    assert set(body) == {
        "id",
        "ride_id",
        "status",
        "pickup",
        "detour_km",
        "detour_minutes",
        "created_at",
        "decided_at",
        "ride",
        "rider",
        "counterpart_email",
    }
    stored = await db.fetchrow(
        "SELECT detour_distance_m, detour_duration_s, status, rider_id "
        "FROM ride_requests WHERE id = $1",
        uuid.UUID(body["id"]),
    )
    assert stored["detour_distance_m"] == pytest.approx(3_000.0)
    assert stored["detour_duration_s"] == pytest.approx(240.0)
    assert stored["rider_id"] == rider["id"]
    # Platz wird erst beim Annehmen belegt
    assert (await ride_row(db, ride_id))["seats_available"] == 3


async def test_detour_is_recomputed_from_stored_route_and_ignores_client_values(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    _driver, rider, ride_id = await _world(db)
    # Kein route_override: Fake-Straße = Luftlinie x 1,3 über Fahrer-Start -> Pickup -> Event
    expected_via = (haversine_m(NORTH, PICKUP) + haversine_m(PICKUP, EVENT)) * routing.detour_factor

    # Clientwerte sind nicht vorgesehen: zusätzliche Felder werden abgelehnt, nicht übernommen
    client.cookies.set("access_token", auth_cookies(rider)["access_token"])
    extra = await client.post(
        f"/api/rides/{ride_id}/requests",
        json={**pickup_json(), "detour_km": 0.0, "detour_minutes": 0.0},
    )
    assert extra.status_code == 422

    r = await _post(client, rider, ride_id)
    assert r.status_code == 201, r.text
    assert routing.route_calls[-1] == (NORTH, PICKUP, EVENT)
    stored = await db.fetchval(
        "SELECT detour_distance_m FROM ride_requests WHERE id = $1", uuid.UUID(r.json()["id"])
    )
    assert stored == pytest.approx(max(0.0, expected_via - BASE_DISTANCE_M), abs=1.0)


async def test_detour_too_large_is_422_and_nothing_is_stored(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = huge_detour
    _driver, rider, ride_id = await _world(db)
    r = await _post(client, rider, ride_id)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "detour_too_large"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


async def test_own_ride_is_403(client: httpx.AsyncClient, db: Any) -> None:
    driver, _rider, ride_id = await _world(db)
    r = await _post(client, driver, ride_id)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "own_ride"


async def test_unknown_and_cancelled_ride_are_404(client: httpx.AsyncClient, db: Any) -> None:
    driver, rider, ride_id = await _world(db, status="cancelled")
    for target in (uuid.uuid4(), ride_id):
        r = await _post(client, rider, target)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
    # Auch der Fahrer erfährt bei abgesagter eigener Fahrt nur 404 (kein Sonderweg)
    assert (await _post(client, driver, ride_id)).status_code == 404


async def test_malformed_ride_id_is_validation_error(client: httpx.AsyncClient, db: Any) -> None:
    rider = await create_user(db)
    client.cookies.set("access_token", auth_cookies(rider)["access_token"])
    r = await client.post("/api/rides/kein-uuid/requests", json=pickup_json())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_full_ride_is_409_ride_full(client: httpx.AsyncClient, db: Any) -> None:
    _driver, rider, ride_id = await _world(db, seats_total=2, seats_available=0, status="full")
    r = await _post(client, rider, ride_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_full"


async def test_departed_ride_is_409_ride_departed(client: httpx.AsyncClient, db: Any) -> None:
    _driver, rider, ride_id = await _world(
        db, departure_time=datetime.now(UTC) - timedelta(minutes=5)
    )
    r = await _post(client, rider, ride_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_departed"


@pytest.mark.parametrize("status", ["pending", "accepted"])
async def test_second_active_request_is_409_already_requested(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting, status: str
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    await insert_request(db, ride_id, rider["id"], status=status)
    r = await _post(client, rider, ride_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_requested"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 1


@pytest.mark.parametrize("status", ["declined", "cancelled"])
async def test_new_request_allowed_after_declined_or_cancelled(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting, status: str
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    await insert_request(db, ride_id, rider["id"], status=status)
    r = await _post(client, rider, ride_id)
    assert r.status_code == 201, r.text
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 2


async def test_two_riders_can_request_the_same_ride(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    other = await create_user(db)
    assert (await _post(client, rider, ride_id)).status_code == 201
    assert (await _post(client, other, ride_id)).status_code == 201


async def test_pickup_far_from_any_road_is_outside_coverage(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.snap_m = 5_000.0  # nächste Straße 5 km entfernt (> MATCH_MAX_SNAP_DISTANCE_M)
    _driver, rider, ride_id = await _world(db)
    r = await _post(client, rider, ride_id)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "outside_coverage"
    assert routing.route_calls == []  # es wird gar nicht erst geroutet
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


async def test_pickup_outside_extract_is_outside_coverage(
    client: httpx.AsyncClient, db: Any
) -> None:
    _driver, rider, ride_id = await _world(db)
    r = await _post(client, rider, ride_id, pickup=(48.1351, 11.5820))  # München
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "outside_coverage"


async def test_routing_down_is_503(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.unavailable = True
    _driver, rider, ride_id = await _world(db)
    r = await _post(client, rider, ride_id)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "routing_unavailable"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


async def test_routing_going_down_between_snap_and_route_is_503(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    def boom(_: Any) -> tuple[float, float]:
        raise RoutingUnavailableError("fake")

    routing.route_override = boom
    _driver, rider, ride_id = await _world(db)
    r = await _post(client, rider, ride_id)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "routing_unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"pickup": {"lat": 91, "lon": 9.9}},
        {"pickup": {"lat": 53.6, "lon": 181}},
        {"pickup": {"lat": "x", "lon": 9.9}},
        {"pickup": {"lat": 53.6}},
        {"pickup": {"lat": 53.6, "lon": 9.9, "extra": 1}},
        {"pickup": None},
    ],
)
async def test_invalid_body_is_validation_error_without_echoing_input(
    client: httpx.AsyncClient, db: Any, payload: dict[str, Any]
) -> None:
    _driver, rider, ride_id = await _world(db)
    client.cookies.set("access_token", auth_cookies(rider)["access_token"])
    r = await client.post(f"/api/rides/{ride_id}/requests", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert '"input"' not in r.text
    assert "53.6" not in r.text


async def test_error_responses_never_contain_emails_or_coordinates(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = huge_detour
    driver, rider, ride_id = await _world(db)
    for resp in (await _post(client, rider, ride_id), await _post(client, driver, ride_id)):
        assert rider["email"] not in resp.text
        assert driver["email"] not in resp.text
        assert str(PICKUP[0]) not in resp.text


async def test_creation_leaves_seat_invariant_intact(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    assert (await _post(client, rider, ride_id)).status_code == 201
    await assert_seat_invariant(db)
    assert await db.fetchval("SELECT status FROM ride_requests") == "pending"


# --- Nachprüfung unter FOR SHARE: Zustandsänderung während des Routings ----------------------


def _mutate_during_routing(routing: FakeRouting, db: Any, sql: str, *args: Any) -> None:
    """Führt ``sql`` genau einmal aus, während die API routet (zwischen Vorprüfung und Insert)."""
    original = routing.route
    done = False

    async def route(waypoints: Any, *, geometry: bool = True) -> Any:
        nonlocal done
        if not done:
            done = True
            await db.execute(sql, *args)
        return await original(waypoints, geometry=geometry)

    routing.route = route  # type: ignore[method-assign]


async def test_ride_cancelled_during_routing_is_404_and_nothing_is_stored(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    _mutate_during_routing(
        routing, db, "UPDATE rides SET status = 'cancelled' WHERE id = $1", ride_id
    )

    r = await _post(client, rider, ride_id)

    assert routing.route_calls, "Mutation muss im Routing-Aufruf ausgelöst worden sein"
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


async def test_in_flight_cancellation_is_awaited_by_for_share_recheck(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    """Eine noch nicht committete Absage (Zeilensperre) muss die Nachprüfung abwarten.

    Ohne ``FOR SHARE`` sähe die Nachprüfung noch den alten Status ``open`` und speicherte eine
    Anfrage auf einer gerade abgesagten Fahrt.
    """
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    original = routing.route
    release: asyncio.Task[None] | None = None
    tx = db.transaction()

    async def commit_later() -> None:
        await asyncio.sleep(0.5)
        await tx.commit()

    async def route(waypoints: Any, *, geometry: bool = True) -> Any:
        nonlocal release
        if release is None:
            await tx.start()
            await db.execute("UPDATE rides SET status = 'cancelled' WHERE id = $1", ride_id)
            release = asyncio.create_task(commit_later())
        return await original(waypoints, geometry=geometry)

    routing.route = route  # type: ignore[method-assign]

    r = await _post(client, rider, ride_id)

    assert release is not None
    await release
    assert r.status_code == 404
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


async def test_ride_filled_during_routing_is_409_ride_full_and_nothing_is_stored(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db, seats_total=1)
    other = await create_user(db)
    original = routing.route
    done = False

    async def route(waypoints: Any, *, geometry: bool = True) -> Any:
        nonlocal done
        if not done:
            done = True
            await insert_request(db, ride_id, other["id"], status="accepted")  # füllt die Fahrt
        return await original(waypoints, geometry=geometry)

    routing.route = route  # type: ignore[method-assign]

    r = await _post(client, rider, ride_id)

    assert done
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_full"
    assert (
        await db.fetchval("SELECT count(*) FROM ride_requests WHERE rider_id = $1", rider["id"])
        == 0
    )
    await assert_seat_invariant(db)


async def test_ride_departing_during_routing_is_409_ride_departed(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    _driver, rider, ride_id = await _world(db)
    _mutate_during_routing(
        routing,
        db,
        "UPDATE rides SET departure_time = now() - interval '1 minute' WHERE id = $1",
        ride_id,
    )

    r = await _post(client, rider, ride_id)

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_departed"
    assert await db.fetchval("SELECT count(*) FROM ride_requests") == 0


# --- Prüf-Reihenfolge beim Anlegen: 404 → own_ride → ride_departed → ride_full ---------------


async def test_precedence_own_ride_beats_ride_full(client: httpx.AsyncClient, db: Any) -> None:
    driver, _rider, ride_id = await _world(db, seats_total=1, seats_available=0, status="full")
    r = await _post(client, driver, ride_id)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "own_ride"


async def test_precedence_own_ride_beats_ride_departed(client: httpx.AsyncClient, db: Any) -> None:
    driver, _rider, ride_id = await _world(
        db, departure_time=datetime.now(UTC) - timedelta(minutes=5)
    )
    r = await _post(client, driver, ride_id)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "own_ride"


async def test_precedence_ride_departed_beats_ride_full(client: httpx.AsyncClient, db: Any) -> None:
    _driver, rider, ride_id = await _world(
        db,
        seats_total=1,
        seats_available=0,
        status="full",
        departure_time=datetime.now(UTC) - timedelta(minutes=5),
    )
    r = await _post(client, rider, ride_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_departed"


async def test_precedence_cancelled_beats_everything_else(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, rider, ride_id = await _world(
        db,
        seats_total=1,
        seats_available=0,
        status="cancelled",
        departure_time=datetime.now(UTC) - timedelta(minutes=5),
    )
    for user in (driver, rider):
        r = await _post(client, user, ride_id)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
