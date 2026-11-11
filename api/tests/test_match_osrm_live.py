"""Matching-Randfälle gegen ECHTES OSRM (Marker ``osrm``, Hamburg-Extrakt).

Die erwarteten Größenordnungen wurden vorab gegen den Hamburg-Extrakt gemessen (Umweg über den
Pickup gegenüber der Basisroute, Standardschwellen 20 % ODER 25 min). Die Tests prüfen jeweils, dass
die Fahrt tatsächlich geroutet wurde (sonst bewiese ein leeres Ergebnis nichts).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import pytest
import pytest_asyncio

from app.main import app
from app.routing import NoRouteError, OsrmClient, RoutingUnavailableError, get_routing_client
from app.routing.base import LatLon, RouteResult
from tests.helpers import EVENT, create_user, future, insert_ride
from tests.test_match_support import dist_m, login, override_settings

pytestmark = [pytest.mark.osrm, pytest.mark.asyncio(loop_scope="session")]

BERGEDORF: LatLon = (53.4890, 10.2150)
POPPENBUETTEL: LatLon = (53.6560, 10.0840)
HARBURG: LatLon = (53.4609, 9.9831)  # südlich der Elbe
BLANKENESE: LatLon = (53.5640, 9.8120)  # westlich des Ziels
ALTONA: LatLon = (53.5511, 9.9265)
WILHELMSBURG: LatLon = (53.5000, 10.0100)
MUENCHEN: LatLon = (48.1351, 11.5820)

DEFAULTS = {
    "match_prefilter_radius_m": 20_000,
    "match_max_detour_ratio": 0.20,
    "match_max_detour_minutes": 25.0,
    "match_detour_mode": "any",
    "match_max_candidates": 30,
    "match_max_snap_distance_m": 500,
    "match_route_trim_start_m": 1000,
}


class SpyOsrm(OsrmClient):
    def __init__(self, url: str) -> None:
        super().__init__(url, timeout_s=10)
        self.route_calls: list[tuple[LatLon, ...]] = []

    async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        self.route_calls.append(tuple(waypoints))
        return await super().route(waypoints, geometry=geometry)


@pytest_asyncio.fixture(loop_scope="session")
async def osrm() -> AsyncIterator[SpyOsrm]:
    spy = SpyOsrm(os.environ.get("OSRM_URL", "http://127.0.0.1:55000"))
    try:
        await spy.nearest(EVENT)
    except (RoutingUnavailableError, NoRouteError):
        await spy.aclose()
        pytest.skip("OSRM mit Hamburg-Extrakt nicht erreichbar")
    yield spy
    await spy.aclose()


async def real_ride(db: Any, osrm: SpyOsrm, driver_id: Any, start: LatLon, **kw: Any) -> Any:
    base = await osrm.route([start, EVENT])
    osrm.route_calls.clear()
    ride_id = await insert_ride(
        db,
        driver_id,
        start=start,
        end=EVENT,
        route=list(base.geometry),
        distance_m=base.distance_m,
        duration_s=base.duration_s,
        **kw,
    )
    return ride_id, base


async def match(client: httpx.AsyncClient, rider: dict[str, Any], pickup: LatLon, **p: Any) -> Any:
    login(client, rider)
    return await client.get("/api/match", params={"lat": pickup[0], "lon": pickup[1], **p})


@pytest_asyncio.fixture(loop_scope="session")
async def live(client: httpx.AsyncClient, osrm: SpyOsrm) -> httpx.AsyncClient:
    app.dependency_overrides[get_routing_client] = lambda: osrm
    return client


async def test_pickup_exactly_on_the_route_has_no_detour(
    live: httpx.AsyncClient, db: Any, osrm: SpyOsrm
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id, base = await real_ride(db, osrm, driver["id"], BERGEDORF)
    on_route = base.geometry[len(base.geometry) // 2]  # echter Routenpunkt
    with override_settings(**DEFAULTS):
        r = await match(live, rider, on_route)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["ride_id"] for i in items] == [str(ride_id)]
    assert items[0]["detour_km"] <= 0.5 and items[0]["detour_minutes"] <= 1.5
    assert len(osrm.route_calls) == 1


async def test_displayed_route_is_real_trimmed_and_simplified(
    live: httpx.AsyncClient, db: Any, osrm: SpyOsrm
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    _, base = await real_ride(db, osrm, driver["id"], BERGEDORF)
    with override_settings(**DEFAULTS):
        r = await match(live, rider, base.geometry[len(base.geometry) // 2])
    route = r.json()["items"][0]["route"]
    assert 2 <= len(route) < len(base.geometry) / 3  # vereinfacht
    assert dist_m(BERGEDORF, route[0]) > 400  # Anfang (≈ 1 km entlang der Route) abgeschnitten
    assert all(dist_m(BERGEDORF, p) > 400 for p in route)
    assert dist_m(EVENT, route[-1]) < 200


async def test_pickup_behind_the_destination_is_rejected(
    live: httpx.AsyncClient, db: Any, osrm: SpyOsrm
) -> None:
    """Blankenese liegt hinter dem Ziel (falsche Richtung): ≈ +45 min, +140 %."""
    driver = await create_user(db)
    rider = await create_user(db)
    await real_ride(db, osrm, driver["id"], BERGEDORF)
    with override_settings(**DEFAULTS):
        r = await match(live, rider, BLANKENESE)
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}
    assert len(osrm.route_calls) == 1  # Kandidat (Luftlinie ≈ 12 km) wurde tatsächlich bewertet
    assert osrm.route_calls[0][1] == BLANKENESE


async def test_small_beeline_but_large_real_detour_over_the_elbe_is_rejected(
    live: httpx.AsyncClient, db: Any, osrm: SpyOsrm
) -> None:
    """Harburg liegt ≈ 11 km Luftlinie von der Route (Prefilter-Kandidat), real +40 min / +200 %."""
    driver = await create_user(db)
    rider = await create_user(db)
    await real_ride(db, osrm, driver["id"], POPPENBUETTEL)
    with override_settings(**DEFAULTS):
        r = await match(live, rider, HARBURG)
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}
    assert len(osrm.route_calls) == 1


async def test_nearby_pickup_with_small_real_detour_is_accepted(
    live: httpx.AsyncClient, db: Any, osrm: SpyOsrm
) -> None:
    """Gegenprobe: Wilhelmsburg liegt (fast) auf der Harburger Route → ≈ 2,5 km, 7 min, 15 %."""
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id, _ = await real_ride(db, osrm, driver["id"], HARBURG)
    with override_settings(**DEFAULTS):
        r = await match(live, rider, WILHELMSBURG)
    items = r.json()["items"]
    assert [i["ride_id"] for i in items] == [str(ride_id)]
    assert 1.5 < items[0]["detour_km"] < 4 and 3 < items[0]["detour_minutes"] < 12


async def test_detour_mode_any_vs_all_with_real_numbers(
    live: httpx.AsyncClient, db: Any, osrm: SpyOsrm
) -> None:
    """Bergedorf → Pickup Wilhelmsburg: ≈ +12 min (≤ 25) aber ≈ +61 % (> 20 %)."""
    driver = await create_user(db)
    rider = await create_user(db)
    await real_ride(db, osrm, driver["id"], BERGEDORF)
    with override_settings(**{**DEFAULTS, "match_detour_mode": "any"}):
        assert (await match(live, rider, WILHELMSBURG)).json()["total"] == 1
    with override_settings(**{**DEFAULTS, "match_detour_mode": "all"}):
        assert (await match(live, rider, WILHELMSBURG)).json()["total"] == 0


async def test_sorted_by_real_detour(live: httpx.AsyncClient, db: Any, osrm: SpyOsrm) -> None:
    """Pickup Altona: Harburg (≈ 11 min) liegt klar vor Poppenbüttel und Bergedorf (je ≈ 18 min)."""
    rider = await create_user(db)
    # Je Fahrt ein eigener Fahrer (Kandidatenlimit je Fahrer, SEC-1).
    # Abfahrten absichtlich in umgekehrter Reihenfolge – der Umweg entscheidet
    d1, d2, d3 = [await create_user(db) for _ in range(3)]
    bergedorf, _ = await real_ride(db, osrm, d1["id"], BERGEDORF, departure_time=future(5))
    poppen, _ = await real_ride(db, osrm, d2["id"], POPPENBUETTEL, departure_time=future(10))
    harburg, _ = await real_ride(db, osrm, d3["id"], HARBURG, departure_time=future(15))
    with override_settings(**DEFAULTS):
        r = await match(live, rider, ALTONA)
    items = r.json()["items"]
    assert len(items) == 3 and items[0]["ride_id"] == str(harburg)
    assert {i["ride_id"] for i in items} == {str(harburg), str(poppen), str(bergedorf)}
    assert [i["detour_minutes"] for i in items] == sorted(i["detour_minutes"] for i in items)


async def test_munich_pickup_is_outside_coverage_with_real_osrm(
    live: httpx.AsyncClient, db: Any
) -> None:
    rider = await create_user(db)
    r = await match(live, rider, MUENCHEN)  # OSRM würde still auf eine ferne Straße snappen
    assert r.status_code == 422 and r.json()["error"]["code"] == "outside_coverage"
    assert "48.13" not in r.text


async def test_unreachable_osrm_is_a_clean_503(client: httpx.AsyncClient, db: Any) -> None:
    dead = OsrmClient("http://127.0.0.1:9", timeout_s=1)  # Port 9 (discard): nichts hört zu
    app.dependency_overrides[get_routing_client] = lambda: dead
    try:
        rider = await create_user(db)
        r = await match(client, rider, ALTONA)
    finally:
        await dead.aclose()
    assert r.status_code == 503 and r.json()["error"]["code"] == "routing_unavailable"
