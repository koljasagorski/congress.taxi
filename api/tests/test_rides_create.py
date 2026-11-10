"""POST /api/rides – Anlegen inkl. Routenberechnung (Fake-Routing, echtes PostGIS)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.routing import NoRouteError, RouteResult
from app.routing.base import LatLon
from tests.fakes import FakeRouting, haversine_m
from tests.helpers import EVENT, NORTH, create_user, future
from tests.test_rides_common import ALL_RIDE_KEYS, RIDES, START, auth_headers, create_body

pytestmark = pytest.mark.asyncio(loop_scope="session")

MUENCHEN: LatLon = (48.1351, 11.5820)


async def _count_rides(db: Any) -> int:
    return int(await db.fetchval("SELECT count(*) FROM rides"))


async def test_create_ride_success_stores_geometries_and_values(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    departure = future(48)
    resp = await client.post(
        RIDES,
        json=create_body(departure.isoformat(), start=START, seats=3),
        headers=auth_headers(driver),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == ALL_RIDE_KEYS
    assert body["start"] == {"lat": START[0], "lon": START[1]}
    settings = get_settings()
    assert body["end"] == {"lat": settings.event_lat, "lon": settings.event_lon}
    assert body["seats_total"] == 3
    assert body["seats_available"] == 3
    assert body["status"] == "open"
    assert body["pending_requests"] == 0
    assert body["accepted_requests"] == 0
    assert body["departure_time"].endswith(("Z", "+00:00"))
    assert body["created_at"].endswith(("Z", "+00:00"))
    assert len(body["route"]) >= 2
    assert all(len(p) == 2 for p in body["route"])
    # Route ist (lat, lon): Start zuerst nahe dem Startpunkt, Ende nahe dem Event
    assert haversine_m(tuple(body["route"][0]), START) < 100  # type: ignore[arg-type]
    assert haversine_m(tuple(body["route"][-1]), EVENT) < 100  # type: ignore[arg-type]

    # Routing wurde mit [Start, Event] aufgerufen; Start und Event wurden auf Straßen geprüft
    expected_event = (settings.event_lat, settings.event_lon)
    assert routing.route_calls == [(START, expected_event)]
    assert START in routing.nearest_calls
    assert expected_event in routing.nearest_calls

    row = await db.fetchrow(
        "SELECT driver_id, ST_Y(start_geom::geometry) AS s_lat,"
        " ST_X(start_geom::geometry) AS s_lon, ST_Y(end_geom::geometry) AS e_lat,"
        " ST_X(end_geom::geometry) AS e_lon,"
        " ST_GeometryType(route_geom::geometry) AS gtype, ST_NPoints(route_geom::geometry) AS n,"
        " ST_Y(ST_StartPoint(route_geom::geometry)) AS r0_lat,"
        " ST_X(ST_EndPoint(route_geom::geometry)) AS rn_lon,"
        " route_distance_m, route_duration_s, seats_total, seats_available, status,"
        " departure_time FROM rides WHERE id = $1::uuid",
        body["id"],
    )
    assert row is not None
    assert row["driver_id"] == driver["id"]
    assert (row["s_lat"], row["s_lon"]) == pytest.approx(START)
    assert (row["e_lat"], row["e_lon"]) == pytest.approx(expected_event)
    assert row["gtype"] == "ST_LineString"
    expected_route = await routing.route([START, expected_event])
    assert row["n"] == len(expected_route.geometry)
    assert row["r0_lat"] == pytest.approx(expected_route.geometry[0][0])
    assert row["rn_lon"] == pytest.approx(expected_route.geometry[-1][1])
    assert row["route_distance_m"] == pytest.approx(expected_route.distance_m)
    assert row["route_duration_s"] == pytest.approx(expected_route.duration_s)
    assert (row["seats_total"], row["seats_available"], row["status"]) == (3, 3, "open")
    assert abs(row["departure_time"] - departure) < timedelta(seconds=1)
    # distance_km / duration_min sind gerundete Werte der gespeicherten Route
    assert body["distance_km"] == pytest.approx(expected_route.distance_m / 1000, abs=0.06)
    assert body["duration_min"] == pytest.approx(expected_route.duration_s / 60, abs=0.06)


async def test_departure_time_with_offset_is_stored_as_the_same_instant(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    departure = future(30).astimezone().replace(microsecond=0)
    local = departure.isoformat()
    resp = await client.post(
        RIDES, json=create_body("2999-01-01T10:00:00+02:00"), headers=auth_headers(driver)
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["departure_time"].startswith("2999-01-01T08:00:00")
    resp2 = await client.post(RIDES, json=create_body(local), headers=auth_headers(driver))
    assert resp2.status_code == 201, resp2.text


async def test_seats_boundaries_are_accepted(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    for seats in (1, 8):
        resp = await client.post(
            RIDES,
            json=create_body(future().isoformat(), seats=seats),
            headers=auth_headers(driver),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["seats_total"] == seats == resp.json()["seats_available"]


async def test_start_equal_to_event_builds_a_valid_line(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    settings = get_settings()
    event = (settings.event_lat, settings.event_lon)
    driver = await create_user(db)
    resp = await client.post(
        RIDES,
        json=create_body(future().isoformat(), start=event),
        headers=auth_headers(driver),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["route"]) >= 2
    assert body["distance_km"] == 0
    gtype, n = await db.fetchrow(
        "SELECT ST_GeometryType(route_geom::geometry), ST_NPoints(route_geom::geometry) "
        "FROM rides WHERE id = $1::uuid",
        body["id"],
    )
    assert gtype == "ST_LineString"
    assert n >= 2


async def test_single_point_geometry_is_turned_into_a_valid_line(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    async def one_point(waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        return RouteResult(distance_m=0.0, duration_s=0.0, geometry=(waypoints[0],))

    routing.route = one_point  # type: ignore[method-assign]
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 201, resp.text
    n = await db.fetchval(
        "SELECT ST_NPoints(route_geom::geometry) FROM rides WHERE id = $1::uuid",
        resp.json()["id"],
    )
    assert n >= 2
    assert len(resp.json()["route"]) >= 2


async def test_empty_geometry_falls_back_to_start_and_event(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    async def no_geometry(waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        return RouteResult(distance_m=1234.0, duration_s=100.0, geometry=())

    routing.route = no_geometry  # type: ignore[method-assign]
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["route"]) == 2


# --- Validierung ---------------------------------------------------------------------------------


def _bodies() -> list[tuple[str, dict[str, Any]]]:
    good = create_body("2999-01-01T10:00:00Z")
    cases: list[tuple[str, dict[str, Any]]] = []

    def variant(name: str, **changes: Any) -> None:
        cases.append((name, {**good, **changes}))

    variant("vergangenheit", departure_time="2020-01-01T10:00:00Z")
    variant("naive_zeit", departure_time="2999-01-01T10:00:00")
    variant("kein_datum", departure_time="morgen")
    variant("seats_0", seats=0)
    variant("seats_9", seats=9)
    variant("seats_negativ", seats=-1)
    variant("seats_float", seats=2.5)
    variant("seats_string", seats="3")
    variant("seats_bool", seats=True)
    variant("lat_91", start={"lat": 91, "lon": 9.9})
    variant("lat_minus_91", start={"lat": -91, "lon": 9.9})
    variant("lon_181", start={"lat": 53.5, "lon": 181})
    variant("start_fehlt_lon", start={"lat": 53.5})
    variant("start_extra", start={"lat": 53.5, "lon": 9.9, "alt": 3})
    variant("start_null", start=None)
    variant("start_string", start="53.5,9.9")
    variant("extra_feld", driver_id="x")
    cases.append(("leer", {}))
    cases.append(("ohne_zeit", {k: v for k, v in good.items() if k != "departure_time"}))
    cases.append(("ohne_seats", {k: v for k, v in good.items() if k != "seats"}))
    return cases


@pytest.mark.parametrize(("name", "body"), _bodies(), ids=[n for n, _ in _bodies()])
async def test_validation_errors(
    client: httpx.AsyncClient, db: Any, name: str, body: dict[str, Any]
) -> None:
    driver = await create_user(db)
    resp = await client.post(RIDES, json=body, headers=auth_headers(driver))
    assert resp.status_code == 422, (name, resp.text)
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]
    assert await _count_rides(db) == 0


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_nan_and_inf_coordinates_are_rejected(
    client: httpx.AsyncClient, db: Any, literal: str
) -> None:
    driver = await create_user(db)
    raw = (
        '{"start": {"lat": ' + literal + ', "lon": 9.9}, '
        '"departure_time": "2999-01-01T10:00:00Z", "seats": 2}'
    )
    resp = await client.post(
        RIDES,
        content=raw,
        headers={"content-type": "application/json", **auth_headers(driver)},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    assert await _count_rides(db) == 0


async def test_validation_error_does_not_echo_input(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    resp = await client.post(
        RIDES,
        json={
            "start": {"lat": 91.123456, "lon": 9.9},
            "departure_time": "2999-01-01T10:00:00Z",
            "seats": 3,
        },
        headers=auth_headers(driver),
    )
    assert resp.status_code == 422
    assert "91.123456" not in resp.text


# --- Routing-Fehler ------------------------------------------------------------------------------


async def test_start_outside_the_extract_is_outside_coverage(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    resp = await client.post(
        RIDES,
        json=create_body(future().isoformat(), start=MUENCHEN),
        headers=auth_headers(driver),
    )
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "outside_coverage"
    assert "48.1351" not in resp.text
    assert routing.route_calls == []
    assert await _count_rides(db) == 0


async def test_too_large_snap_distance_for_start_is_outside_coverage(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.snap_m = lambda p: 5_000.0 if p == START else 8.0
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "outside_coverage"
    assert routing.route_calls == []
    assert await _count_rides(db) == 0


async def test_snap_distance_exactly_at_the_limit_is_accepted(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    limit = float(get_settings().match_max_snap_distance_m)
    routing.snap_m = lambda _p: limit
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 201, resp.text


async def test_event_too_far_from_a_road_is_outside_coverage(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    settings = get_settings()
    event = (settings.event_lat, settings.event_lon)
    routing.snap_m = lambda p: 9_000.0 if p == event else 8.0
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "outside_coverage"
    assert await _count_rides(db) == 0


async def test_routing_unavailable_returns_503_and_stores_nothing(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.unavailable = True
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "routing_unavailable"
    assert await _count_rides(db) == 0


async def test_routing_unavailable_during_route_call_stores_nothing(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    from app.routing import RoutingUnavailableError

    async def boom(waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        raise RoutingUnavailableError("fake")

    routing.route = boom  # type: ignore[method-assign]
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "routing_unavailable"
    assert await _count_rides(db) == 0


async def test_no_route_returns_422_no_route_and_stores_nothing(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    async def nothing(waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        raise NoRouteError("fake")

    routing.route = nothing  # type: ignore[method-assign]
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_route"
    assert await _count_rides(db) == 0


# --- Verbindungen während OSRM-Aufrufen ------------------------------------------------------


async def test_no_db_transaction_is_held_while_waiting_for_osrm(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    """Während nearest/route läuft, darf keine Verbindung „idle in transaction“ sein.

    Sonst belegen langsame OSRM-Aufrufe den Pool (5 + 10) und blockieren alle Endpunkte.
    """
    idle_in_tx: list[int] = []

    async def _probe() -> None:
        idle_in_tx.append(
            int(
                await db.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                    " AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                )
            )
        )

    real_nearest, real_route = routing.nearest, routing.route

    async def nearest(point: LatLon) -> Any:
        await _probe()
        return await real_nearest(point)

    async def route(waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        await _probe()
        return await real_route(waypoints, geometry=geometry)

    routing.nearest = nearest  # type: ignore[method-assign]
    routing.route = route  # type: ignore[method-assign]
    driver = await create_user(db)
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 201, resp.text
    assert len(idle_in_tx) == 3  # nearest (Start), nearest (Event), route
    assert idle_in_tx == [0, 0, 0]


async def test_driver_deleted_while_routing_returns_401_not_500(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    """Konto wird gelöscht, während OSRM antwortet → 401 statt Fremdschlüssel-Fehler (500)."""
    driver = await create_user(db)
    real_route = routing.route

    async def route(waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        await db.execute("DELETE FROM users WHERE id = $1", driver["id"])
        return await real_route(waypoints, geometry=geometry)

    routing.route = route  # type: ignore[method-assign]
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "unauthorized"
    assert await _count_rides(db) == 0


# --- Anmeldung -----------------------------------------------------------------------------------


async def test_create_requires_login(client: httpx.AsyncClient, db: Any) -> None:
    resp = await client.post(RIDES, json=create_body(future().isoformat()))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert await _count_rides(db) == 0


async def test_response_does_not_leak_driver_identity(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db, email="geheim@example.org")
    resp = await client.post(
        RIDES, json=create_body(future().isoformat()), headers=auth_headers(driver)
    )
    assert resp.status_code == 201
    assert "geheim@example.org" not in resp.text
    assert str(driver["id"]) not in resp.text
    assert NORTH == START
