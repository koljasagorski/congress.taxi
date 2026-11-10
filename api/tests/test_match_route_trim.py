"""Anzeige-Route: am Anfang in METERN gekürzt, danach vereinfacht (Datenschutz: Wohnadresse)."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import httpx
import pytest

from app.routing.base import LatLon
from tests.fakes import haversine_m
from tests.helpers import create_user, insert_ride
from tests.test_match_support import dist_m, login, override_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

#: Alle Schwellen so locker, dass die Route immer akzeptiert wird – hier geht es nur um die Route.
LAX = {"match_max_detour_ratio": 1e6, "match_max_detour_minutes": 1e6}


def densify(a: LatLon, b: LatLon, step_m: float = 100.0) -> list[LatLon]:
    n = max(1, int(haversine_m(a, b) // step_m))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def polyline(*corners: LatLon) -> list[LatLon]:
    out: list[LatLon] = [corners[0]]
    for a, b in pairwise(corners):
        out.extend(densify(a, b)[1:])
    return out


async def route_of(client: httpx.AsyncClient, db: Any, line: list[LatLon], **settings: Any) -> Any:
    driver = await create_user(db)
    rider = await create_user(db)
    await insert_ride(db, driver["id"], start=line[0], end=line[-1], route=line)
    login(client, rider)
    with override_settings(**{**LAX, **settings}):
        r = await client.get("/api/match", params={"lat": 53.65, "lon": 9.9755})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    return items[0]["route"]


START: LatLon = (53.60, 9.90)
CORNER: LatLon = (53.60, 9.9755)  # ≈ 5 km nach Osten (Ost-West-Stück zuerst!)
END: LatLon = (53.70, 9.9755)  # ≈ 11 km nach Norden


async def test_trim_is_measured_in_metres_along_the_route_even_for_an_east_west_first_leg(
    client: httpx.AsyncClient, db: Any
) -> None:
    route = await route_of(client, db, polyline(START, CORNER, END))
    first = route[0]
    # In Grad gerechnet wären es bei 53,6° Breite nur ≈ 600 m; gefordert sind 1000 m ± Rundung
    assert 950 <= dist_m(START, first) <= 1050
    assert all(dist_m(START, p) >= 940 for p in route)
    assert dist_m(END, route[-1]) < 5  # das Ende bleibt erhalten


async def test_trim_follows_the_configured_distance(client: httpx.AsyncClient, db: Any) -> None:
    route = await route_of(client, db, polyline(START, CORNER, END), match_route_trim_start_m=3000)
    assert 2900 <= dist_m(START, route[0]) <= 3100


async def test_trim_zero_keeps_the_whole_route(client: httpx.AsyncClient, db: Any) -> None:
    route = await route_of(client, db, polyline(START, CORNER, END), match_route_trim_start_m=0)
    assert dist_m(START, route[0]) < 5 and dist_m(END, route[-1]) < 5


async def test_route_shorter_than_trim_keeps_only_the_last_quarter(
    client: httpx.AsyncClient, db: Any
) -> None:
    short_end: LatLon = (53.6036, 9.90)  # ≈ 400 m < 1000 m Kürzung
    length = haversine_m(START, short_end)
    assert length < 1000
    route = await route_of(client, db, polyline(START, short_end))
    assert route, "es bleibt ein Stück zur Anzeige"
    assert dist_m(short_end, route[-1]) < 5  # Ende bleibt
    # nie die Startposition: höchstens das letzte Viertel der Route
    assert all(dist_m(START, p) >= 0.74 * length for p in route)
    assert dist_m(START, route[0]) == pytest.approx(0.75 * length, rel=0.05)


async def test_trim_larger_than_route_never_removes_more_than_three_quarters(
    client: httpx.AsyncClient, db: Any
) -> None:
    total = haversine_m(START, CORNER) + haversine_m(CORNER, END)
    route = await route_of(
        client, db, polyline(START, CORNER, END), match_route_trim_start_m=int(total * 2)
    )
    # das letzte Viertel liegt komplett im Nord-Stück, also gilt die Luftlinie zum Ende
    assert 0.22 * total <= dist_m(END, route[0]) <= 0.28 * total
    assert dist_m(END, route[-1]) < 5


async def test_collinear_vertices_are_simplified_in_degrees_not_metres(
    client: httpx.AsyncClient, db: Any
) -> None:
    dense = polyline((53.60, 9.90), (53.70, 9.90))  # ≈ 11 km, Punkt alle 100 m
    assert len(dense) > 100
    route = await route_of(client, db, dense)
    assert 2 <= len(route) <= 3  # ST_Simplify auf dem Geometry-Cast; sonst blieben > 100 Punkte


async def test_corner_survives_simplification(client: httpx.AsyncClient, db: Any) -> None:
    route = await route_of(client, db, polyline(START, CORNER, END))
    assert any(dist_m(CORNER, p) < 60 for p in route)  # Abzweig > Toleranz bleibt erhalten


async def test_response_uses_lat_lon_order(client: httpx.AsyncClient, db: Any) -> None:
    route = await route_of(client, db, polyline(START, CORNER, END))
    for lat, lon in route:
        assert 53.5 < lat < 53.8 and 9.8 < lon < 10.1  # nicht (lon, lat)
