"""GET /api/match – Vertrag, Vorfilter, Umweg-Schwellen, Fehlerpfade (echte DB, FakeRouting)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import timedelta
from itertools import pairwise
from typing import Any

import httpx
import pytest

from app.main import app
from app.routing import NoRouteError, RoutingUnavailableError, get_routing_client
from app.routing.base import LatLon, RouteResult
from tests.fakes import FakeRouting, haversine_m
from tests.helpers import EVENT, NORTH, create_user, future, insert_ride
from tests.test_match_support import MID_NORTH_EVENT, add_ride, dist_m, login, override_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

ITEM_KEYS = {
    "ride_id",
    "driver_display_name",
    "departure_time",
    "seats_available",
    "detour_km",
    "detour_minutes",
    "detour_ratio",
    "route",
}


async def driver_id(db: Any) -> Any:
    """Neuer Fahrer je Fahrt: Das Kandidatenlimit je Fahrer (SEC-1) soll hier nicht eingreifen."""
    return (await create_user(db))["id"]


async def match(
    client: httpx.AsyncClient, user: dict[str, Any], pickup: LatLon = MID_NORTH_EVENT, **params: Any
) -> httpx.Response:
    query: dict[str, Any] = {"lat": pickup[0], "lon": pickup[1], **params}
    login(client, user)
    return await client.get("/api/match", params=query)


# --- Anmeldung, Validierung ------------------------------------------------------------------


async def test_requires_login(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/match", params={"lat": 53.6, "lon": 9.99})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_invalid_login_cookie_is_401(client: httpx.AsyncClient) -> None:
    client.cookies.set("access_token", "kaputt")
    r = await client.get("/api/match", params={"lat": 53.6, "lon": 9.99})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 91.5, "lon": 9.99},
        {"lat": -90.5, "lon": 9.99},
        {"lat": 53.6, "lon": 180.5},
        {"lat": 53.6, "lon": -181},
        {"lat": "nan", "lon": 9.99},
        {"lat": "inf", "lon": 9.99},
        {"lat": "abc", "lon": 9.99},
        {"lon": 9.99},
        {"lat": 53.6},
        {"lat": 53.6, "lon": 9.99, "min_seats": 0},
        {"lat": 53.6, "lon": 9.99, "min_seats": 9},
        {"lat": 53.6, "lon": 9.99, "limit": 0},
        {"lat": 53.6, "lon": 9.99, "limit": 51},
        {"lat": 53.6, "lon": 9.99, "time_from": "2030-01-01T10:00:00"},  # naiv
        {"lat": 53.6, "lon": 9.99, "time_to": "2030-01-01T10:00:00"},  # naiv
        {"lat": 53.6, "lon": 9.99, "time_from": "morgen"},
    ],
)
async def test_validation_errors_are_422_without_echoing_input(
    client: httpx.AsyncClient, db: Any, params: dict[str, Any]
) -> None:
    user = await create_user(db)
    login(client, user)
    r = await client.get("/api/match", params=params)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "validation_error" and body["details"]
    for value in params.values():
        if isinstance(value, float | int) and abs(value) > 50:
            assert str(value) not in r.text  # Eingabewerte werden nie gespiegelt


async def test_time_to_before_time_from_is_422(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    r = await match(
        client,
        user,
        time_from=future(10).isoformat(),
        time_to=future(5).isoformat(),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_window_with_equal_bounds_is_valid(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    t = future(10).isoformat()
    assert (await match(client, user, time_from=t, time_to=t)).status_code == 200


async def test_aware_time_with_offset_is_accepted(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    t = future(10).astimezone().isoformat()
    assert (await match(client, user, time_from=t)).status_code == 200


# --- Snap-Prüfung ---------------------------------------------------------------------------


async def test_pickup_outside_extract_is_outside_coverage(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    user = await create_user(db)
    r = await match(client, user, (48.1351, 11.5820))  # München
    assert r.status_code == 422 and r.json()["error"]["code"] == "outside_coverage"
    assert "48.13" not in r.text
    assert routing.route_calls == []


async def test_pickup_too_far_from_any_road_is_outside_coverage(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.snap_m = 900.0
    user = await create_user(db)
    r = await match(client, user)
    assert r.status_code == 422 and r.json()["error"]["code"] == "outside_coverage"


async def test_routing_down_is_503_not_a_crash(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.unavailable = True
    user = await create_user(db)
    r = await match(client, user)
    assert r.status_code == 503 and r.json()["error"]["code"] == "routing_unavailable"


class RouteDown(FakeRouting):
    """Snap funktioniert, ``route`` fällt aus (OSRM bricht mitten in der Anfrage weg)."""

    async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        self.route_calls.append(tuple(waypoints))
        raise RoutingUnavailableError("fake: route ausgefallen")


async def test_route_failure_after_successful_snap_is_503(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    rider = await create_user(db)
    for _ in range(20):
        await add_ride(db, await driver_id(db))
    down = RouteDown()
    app.dependency_overrides[get_routing_client] = lambda: down
    r = await match(client, rider)
    assert r.status_code == 503 and r.json()["error"]["code"] == "routing_unavailable"
    # Nach dem ersten Ausfall werden nicht mehr alle 20 Kandidaten durchprobiert
    assert 1 <= len(down.route_calls) <= 8


# --- Leere Treffer, Form der Antwort ------------------------------------------------------------


async def test_no_rides_returns_empty_list(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    r = await match(client, user)
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}


async def test_pickup_on_route_is_accepted_with_zero_detour_and_leaks_nothing(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting, caplog: pytest.LogCaptureFixture
) -> None:
    driver = await create_user(db, email="fahrerin.geheim@example.org", display_name="Frida")
    rider = await create_user(db)
    ride_id = await add_ride(db, driver["id"])
    with caplog.at_level(logging.DEBUG, logger="app"):
        r = await match(client, rider)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert set(item) == ITEM_KEYS
    assert item["ride_id"] == str(ride_id)
    assert item["driver_display_name"] == "Frida"
    assert item["seats_available"] == 3
    assert item["detour_km"] <= 0.5 and item["detour_minutes"] == 0 and item["detour_ratio"] < 0.01
    assert item["departure_time"].endswith(("Z", "+00:00"))
    # keine Kontaktdaten, keine Fahrer-ID, kein exakter Start
    assert str(driver["id"]) not in r.text
    assert "fahrerin.geheim" not in r.text and "email" not in r.text
    assert item["route"], "Route zur Anzeige erwartet"
    assert all(dist_m(NORTH, p) > 900 for p in item["route"])
    assert dist_m(EVENT, item["route"][-1]) < 100
    # Logs enthalten weder Koordinaten noch E-Mail
    assert "53.655" not in caplog.text and "9.969" not in caplog.text
    assert "fahrerin.geheim" not in caplog.text


async def test_end_point_of_the_ride_is_used_as_destination(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    """Die Basisroute wurde gegen ``end_geom`` gemessen, nicht gegen die Event-Konfiguration."""
    driver = await create_user(db)
    rider = await create_user(db)
    end: LatLon = (53.58, 10.02)  # anders als EVENT
    await add_ride(db, driver["id"], end=end)
    probe = FakeRouting()
    app.dependency_overrides[get_routing_client] = lambda: probe
    r = await match(client, rider, (53.67, 9.99))
    assert r.status_code == 200
    assert len(probe.route_calls) == 1
    wps = probe.route_calls[0]
    assert wps[0] == pytest.approx(NORTH, abs=1e-6)
    assert wps[1] == (53.67, 9.99)
    assert wps[2] == pytest.approx(end, abs=1e-6)


# --- Vorfilter -------------------------------------------------------------------------------


async def test_unsuitable_rides_are_not_candidates(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    good = await add_ride(db, driver["id"])
    await add_ride(db, driver["id"], status="full", seats_available=0)
    await add_ride(db, driver["id"], status="cancelled")
    await add_ride(db, driver["id"], status="open", seats_available=0)  # inkonsistent
    await add_ride(db, driver["id"], departure_time=future(-3))  # schon abgefahren
    await add_ride(db, rider["id"])  # eigene Fahrt
    r = await match(client, rider)
    assert r.status_code == 200
    assert [i["ride_id"] for i in r.json()["items"]] == [str(good)]
    assert len(routing.route_calls) == 1  # nur die passende Fahrt wurde überhaupt geroutet


async def test_own_rides_are_excluded_even_when_alone(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    await add_ride(db, driver["id"])
    r = await match(client, driver)
    assert r.json() == {"items": [], "total": 0}


async def test_min_seats(client: httpx.AsyncClient, db: Any, routing: FakeRouting) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    one = await add_ride(db, driver["id"], seats_total=3, seats_available=1)
    three = await add_ride(db, driver["id"], seats_total=3, seats_available=3)
    ids = {i["ride_id"] for i in (await match(client, rider)).json()["items"]}
    assert ids == {str(one), str(three)}
    ids = {i["ride_id"] for i in (await match(client, rider, min_seats=2)).json()["items"]}
    assert ids == {str(three)}
    assert (await match(client, rider, min_seats=4)).json()["total"] == 0


async def test_time_window(client: httpx.AsyncClient, db: Any, routing: FakeRouting) -> None:
    rider = await create_user(db)
    early = await add_ride(db, await driver_id(db), departure_time=future(5))
    mid = await add_ride(db, await driver_id(db), departure_time=future(10))
    late = await add_ride(db, await driver_id(db), departure_time=future(20))

    async def ids(**params: Any) -> set[str]:
        r = await match(client, rider, **params)
        assert r.status_code == 200
        return {i["ride_id"] for i in r.json()["items"]}

    assert await ids() == {str(early), str(mid), str(late)}  # Standard: ab jetzt, nach oben offen
    assert await ids(time_from=future(7).isoformat()) == {str(mid), str(late)}
    assert await ids(time_to=future(12).isoformat()) == {str(early), str(mid)}
    assert await ids(time_from=future(7).isoformat(), time_to=future(12).isoformat()) == {str(mid)}
    assert await ids(time_from=future(30).isoformat()) == set()


async def test_time_from_in_the_past_never_returns_departed_rides(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    await add_ride(db, driver["id"], departure_time=future(-2))
    upcoming = await add_ride(db, driver["id"], departure_time=future(2))
    r = await match(client, rider, time_from=future(-10).isoformat())
    assert [i["ride_id"] for i in r.json()["items"]] == [str(upcoming)]


async def test_prefilter_radius_excludes_far_rides_before_any_routing(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    await add_ride(db, driver["id"], start=(53.55, 9.60), end=(53.50, 9.55))
    with override_settings(match_prefilter_radius_m=5_000):
        r = await match(client, rider, MID_NORTH_EVENT)
    assert r.json() == {"items": [], "total": 0}
    assert routing.route_calls == []  # Vorfilter spart OSRM-Aufrufe


async def test_candidate_limit_keeps_the_nearest_by_beeline(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    # Je Fahrt ein eigener Fahrer: Das Kandidatenlimit je Fahrer (SEC-1) greift hier nicht.
    drivers = [await create_user(db) for _ in range(6)]
    rider = await create_user(db)
    starts = [(53.75, 9.95 + 0.03 * k) for k in range(6)]  # zunehmend seitlich der Pickup-Linie
    # Abfahrtszeit und Einfügereihenfolge laufen GEGEN die Entfernung: Die nächste Fahrt fährt
    # zuletzt, die fernste zuerst. Nur eine echte Luftlinien-Sortierung wählt dann die 3 nächsten;
    # ein Tiebreaker nach Abfahrtszeit oder Einfügereihenfolge würde die 3 fernsten wählen.
    for k in reversed(range(6)):
        await add_ride(db, drivers[k]["id"], start=starts[k], departure_time=future(24 + (5 - k)))
    probe = FakeRouting()
    app.dependency_overrides[get_routing_client] = lambda: probe
    with override_settings(
        match_max_candidates=3,
        match_prefilter_radius_m=50_000,
        match_max_detour_ratio=100.0,
        match_max_detour_minutes=10_000.0,
    ):
        r = await match(client, rider)
    assert r.json()["total"] == 3
    assert len(probe.route_calls) == 3
    routed_starts = {wp[0] for wp in probe.route_calls}
    for s in starts[:3]:
        assert any(haversine_m(s, w) < 5 for w in routed_starts)
    for s in starts[3:]:
        assert not any(haversine_m(s, w) < 5 for w in routed_starts)


async def test_decoy_rides_of_one_driver_do_not_crowd_out_real_drivers(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    """SEC-1: Viele Fahrten mit Start exakt am Abholpunkt dürfen echte Fahrer nicht verdrängen."""
    attacker = await create_user(db, display_name="Angreifer")
    real = await create_user(db, display_name="EchterFahrer")
    rider = await create_user(db)
    await add_ride(db, real["id"], start=NORTH, departure_time=future(26))
    for k in range(35):  # Start = Abholpunkt (Luftlinie 0), mehr als MATCH_MAX_CANDIDATES (30)
        await add_ride(db, attacker["id"], start=MID_NORTH_EVENT, departure_time=future(24 + k))
    r = await match(client, rider, MID_NORTH_EVENT, limit=50)
    assert r.status_code == 200, r.text
    names = [item["driver_display_name"] for item in r.json()["items"]]
    assert "EchterFahrer" in names
    assert names.count("Angreifer") == 2  # MATCH_MAX_CANDIDATES_PER_DRIVER (Standard 2)


async def test_per_driver_candidate_limit_keeps_the_nearest_rides_of_that_driver(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    far = await add_ride(db, driver["id"], start=NORTH, departure_time=future(24))
    near_late = await add_ride(db, driver["id"], start=MID_NORTH_EVENT, departure_time=future(30))
    near_early = await add_ride(db, driver["id"], start=MID_NORTH_EVENT, departure_time=future(25))
    with override_settings(match_max_candidates_per_driver=2):
        r = await match(client, rider, MID_NORTH_EVENT)
    ids = {item["ride_id"] for item in r.json()["items"]}
    assert ids == {str(near_early), str(near_late)}, (ids, far)
    with override_settings(match_max_candidates_per_driver=1):
        r = await match(client, rider, MID_NORTH_EVENT)
    assert {item["ride_id"] for item in r.json()["items"]} == {str(near_early)}


async def test_no_db_transaction_is_held_while_waiting_for_osrm(
    client: httpx.AsyncClient, db: Any
) -> None:
    """Während der OSRM-Aufrufe hängt keine Pool-Verbindung als ``idle in transaction``."""
    rider = await create_user(db)
    for _ in range(3):
        await add_ride(db, await driver_id(db))
    idle_seen: list[int] = []
    lock = asyncio.Lock()  # die asyncpg-Verbindung verträgt keine parallelen Abfragen

    async def idle_in_transaction() -> int:
        async with lock:
            return int(
                await db.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                )
            )

    class Probe(FakeRouting):
        async def nearest(self, point: LatLon) -> Any:
            idle_seen.append(await idle_in_transaction())
            return await super().nearest(point)

        async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
            idle_seen.append(await idle_in_transaction())
            return await super().route(waypoints, geometry=geometry)

    app.dependency_overrides[get_routing_client] = lambda: Probe()
    r = await match(client, rider)
    assert r.status_code == 200 and r.json()["total"] == 3
    assert len(idle_seen) == 1 + 3  # nearest + 3 Umweg-Routen
    assert idle_seen == [0] * len(idle_seen)


async def test_routing_fan_out_is_parallel_but_bounded(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    class Slow(FakeRouting):
        current = 0
        peak = 0

        async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
            Slow.current += 1
            Slow.peak = max(Slow.peak, Slow.current)
            try:
                await asyncio.sleep(0.02)
                return await super().route(waypoints, geometry=geometry)
            finally:
                Slow.current -= 1

    rider = await create_user(db)
    for _ in range(20):
        await add_ride(db, await driver_id(db))
    slow = Slow()
    app.dependency_overrides[get_routing_client] = lambda: slow
    r = await match(client, rider)
    assert r.status_code == 200 and r.json()["total"] == 20
    assert 1 < Slow.peak <= 8


# --- Umweg-Schwellen ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "ratio", "extra_min", "expected"),
    [
        ("any", 0.10, 30, True),  # Distanz ok, Zeit zu groß
        ("all", 0.10, 30, False),
        ("any", 0.40, 5, True),  # Zeit ok, Distanz zu groß
        ("all", 0.40, 5, False),
        ("any", 0.10, 5, True),
        ("all", 0.10, 5, True),
        ("any", 0.40, 40, False),
        ("all", 0.40, 40, False),
    ],
)
async def test_detour_thresholds_any_vs_all(
    client: httpx.AsyncClient,
    db: Any,
    mode: str,
    ratio: float,
    extra_min: float,
    expected: bool,
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    await insert_ride(db, driver["id"], distance_m=30_000.0, duration_s=1_800.0)

    def via(_: Sequence[LatLon]) -> tuple[float, float]:
        return 30_000.0 * (1 + ratio), 1_800.0 + extra_min * 60.0

    scripted = FakeRouting(route_override=via)
    app.dependency_overrides[get_routing_client] = lambda: scripted
    with override_settings(
        match_detour_mode=mode, match_max_detour_ratio=0.20, match_max_detour_minutes=25.0
    ):
        r = await match(client, rider)
    assert r.status_code == 200
    assert len(scripted.route_calls) == 1  # nicht vacuous: der Umweg wurde berechnet
    assert (r.json()["total"] == 1) is expected
    if expected:
        item = r.json()["items"][0]
        # Ausgabe ist gröber als die interne Rechnung (Standort-Orakel): 0,5 km / ganze Minuten
        assert item["detour_ratio"] == pytest.approx(ratio, abs=0.005)
        assert item["detour_minutes"] == round(extra_min)
        assert item["detour_km"] == pytest.approx(30 * ratio, abs=0.25)


async def test_small_beeline_but_large_real_detour_is_filtered_out(
    client: httpx.AsyncClient, db: Any
) -> None:
    """Pickup nah an der Route (Luftlinie), aber real nur über einen weiten Umweg erreichbar."""
    driver = await create_user(db)
    rider = await create_user(db)
    await insert_ride(db, driver["id"], distance_m=30_000.0, duration_s=1_800.0)
    scripted = FakeRouting(route_override=lambda _w: (30_000.0 + 25_000.0, 1_800.0 + 40 * 60.0))
    app.dependency_overrides[get_routing_client] = lambda: scripted
    r = await match(client, rider, MID_NORTH_EVENT)
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}
    assert len(scripted.route_calls) == 1


async def test_candidate_without_route_is_skipped_not_fatal(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    good = await add_ride(db, driver["id"], start=NORTH)
    bad_start: LatLon = (53.75, 10.00)
    await add_ride(db, driver["id"], start=bad_start)

    def route_or_fail(wps: Sequence[LatLon]) -> tuple[float, float]:
        if haversine_m(wps[0], bad_start) < 5:
            raise NoRouteError("fake: keine Verbindung")
        d = sum(haversine_m(a, b) for a, b in pairwise(wps)) * 1.3
        return d, d / (60 / 3.6)

    scripted = FakeRouting(route_override=route_or_fail)
    app.dependency_overrides[get_routing_client] = lambda: scripted
    r = await match(client, rider)
    assert r.status_code == 200
    assert [i["ride_id"] for i in r.json()["items"]] == [str(good)]
    assert len(scripted.route_calls) == 2


# --- Sortierung, Limit/Total -------------------------------------------------------------------


async def test_sorted_by_smallest_detour_then_earliest_departure(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    rider = await create_user(db)
    # wachsender Umweg über den Pickup; die Fahrt mit dem kleinsten Umweg fährt zuletzt ab
    small = await add_ride(db, await driver_id(db), start=NORTH, departure_time=future(30))
    medium = await add_ride(
        db, await driver_id(db), start=(53.75, 10.05), departure_time=future(20)
    )
    large = await add_ride(db, await driver_id(db), start=(53.75, 10.15), departure_time=future(10))
    # gleicher Umweg wie ``small`` (gleiche Start-/Zielpunkte) – früher abfahrend
    tie_early = await add_ride(db, await driver_id(db), start=NORTH, departure_time=future(15))
    with override_settings(
        match_prefilter_radius_m=50_000,
        match_max_detour_ratio=100.0,
        match_max_detour_minutes=10_000.0,
    ):
        r = await match(client, rider)
    items = r.json()["items"]
    assert [i["ride_id"] for i in items] == [str(tie_early), str(small), str(medium), str(large)]
    minutes = [i["detour_minutes"] for i in items]
    assert minutes == sorted(minutes)
    assert items[0]["detour_minutes"] == items[1]["detour_minutes"]


async def test_limit_only_truncates_output_total_counts_all(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    rider = await create_user(db)
    for k in range(5):
        await add_ride(db, await driver_id(db), departure_time=future(5 + k))
    r = await match(client, rider, limit=2)
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 2
    default = (await match(client, rider)).json()
    assert default["total"] == 5 and len(default["items"]) == 5


async def test_infinite_ratio_does_not_break_json(client: httpx.AsyncClient, db: Any) -> None:
    """Basisdistanz 0 ergibt ``ratio = inf``; die Antwort bleibt gültiges JSON (gedeckelt)."""
    driver = await create_user(db)
    rider = await create_user(db)
    await insert_ride(
        db,
        driver["id"],
        start=NORTH,
        end=NORTH,
        distance_m=0.0,
        duration_s=0.0,
        route=[NORTH, NORTH],
    )
    scripted = FakeRouting(route_override=lambda _w: (2_000.0, 120.0))
    app.dependency_overrides[get_routing_client] = lambda: scripted
    r = await match(client, rider, (53.751, 9.951))
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["detour_ratio"] == 999.0 and item["route"] == []  # Länge 0 → keine Anzeigeroute


async def test_response_departure_is_timezone_aware(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    dep = future(8).replace(microsecond=0)
    await add_ride(db, driver["id"], departure_time=dep)
    item = (await match(client, rider)).json()["items"][0]
    from datetime import datetime

    parsed = datetime.fromisoformat(item["departure_time"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and abs(parsed - dep) < timedelta(seconds=1)
