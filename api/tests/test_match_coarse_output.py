"""Standort-Orakel entschärfen: Umweg-Werte der Antwort sind gröber als die interne Rechnung.

Vertrag: ``detour_km`` in 0,5-km-Schritten, ``detour_minutes`` ganzzahlig, ``detour_ratio`` auf
2 Nachkommastellen. Filter und Sortierung im Server nutzen die **exakten** Werte.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import httpx
import pytest

from app.main import app
from app.routing import get_routing_client
from app.routing.base import LatLon
from app.services.matching import (
    MAX_RATIO_OUT,
    coarse_detour_km,
    coarse_detour_minutes,
    coarse_detour_ratio,
)
from tests.fakes import FakeRouting
from tests.helpers import NORTH, create_user, future
from tests.test_match_api import match
from tests.test_match_support import add_ride, override_settings

# --- reine Rundungsfunktionen ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("meters", "expected"),
    [
        (0.0, 0.0),
        (-5.0, 0.0),
        (249.0, 0.0),
        (250.0, 0.5),
        (499.0, 0.5),
        (749.0, 0.5),
        (750.0, 1.0),
        (1_240.0, 1.0),
        (1_260.0, 1.5),
        (12_340.0, 12.5),
        (30_000.0, 30.0),
    ],
)
def test_coarse_km_uses_half_kilometer_steps(meters: float, expected: float) -> None:
    assert coarse_detour_km(meters) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, 0), (-3.0, 0), (29.0, 0), (30.0, 1), (89.0, 1), (90.0, 2), (1_499.0, 25)],
)
def test_coarse_minutes_are_whole_numbers(seconds: float, expected: int) -> None:
    result = coarse_detour_minutes(seconds)
    assert result == expected and isinstance(result, int)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.0, 0.0), (0.1234, 0.12), (0.1251, 0.13), (0.2004, 0.2), (1.0, 1.0)],
)
def test_coarse_ratio_has_two_decimals(ratio: float, expected: float) -> None:
    assert coarse_detour_ratio(ratio) == expected


def test_coarse_ratio_caps_infinity() -> None:
    assert coarse_detour_ratio(math.inf) == MAX_RATIO_OUT == 999.0


# --- über die API ---------------------------------------------------------------------------

SLIGHTLY_WEST: LatLon = (53.75, 10.05)


@pytest.mark.asyncio(loop_scope="session")
async def test_filter_and_sort_use_exact_values(client: httpx.AsyncClient, db: Any) -> None:
    """Zwei Fahrten zeigen dieselben groben Werte, werden aber nach exaktem Umweg sortiert; eine
    dritte liegt knapp über der Schwelle (rundet auf 0,20) und wird trotzdem ausgefiltert."""
    driver = await create_user(db)
    rider = await create_user(db)
    ride_late = await add_ride(db, driver["id"], start=NORTH, departure_time=future(20))
    ride_early = await add_ride(db, driver["id"], start=SLIGHTLY_WEST, departure_time=future(10))
    ride_over = await add_ride(db, driver["id"], start=(53.75, 10.15), departure_time=future(5))

    # Basisdistanz je Fahrt lesen, um Umwegwerte gezielt zu setzen.
    rows = await db.fetch(
        "select id, route_distance_m, route_duration_s from rides where driver_id = $1",
        driver["id"],
    )
    base = {row["id"]: (row["route_distance_m"], row["route_duration_s"]) for row in rows}
    dist_late, dur_late = base[ride_late]
    dist_early, dur_early = base[ride_early]
    dist_over, dur_over = base[ride_over]

    def via(waypoints: Sequence[LatLon]) -> tuple[float, float]:
        start = waypoints[0]
        if start == NORTH:  # später, exakt 10,4 min / 1 240 m extra
            return dist_late + 1_240.0, dur_late + 624.0
        if start == SLIGHTLY_WEST:  # früher, exakt 9,6 min / 1 260 m extra
            return dist_early + 1_260.0, dur_early + 576.0
        # knapp über der Distanzschwelle 20 %: Verhältnis 0,2004 → gerundet 0,20
        return dist_over * 1.2004, dur_over + 5 * 60.0

    app.dependency_overrides[get_routing_client] = lambda: FakeRouting(route_override=via)
    with override_settings(
        match_prefilter_radius_m=50_000,
        match_detour_mode="all",
        match_max_detour_ratio=0.20,
        match_max_detour_minutes=25.0,
    ):
        r = await match(client, rider)
    assert r.status_code == 200
    items = r.json()["items"]
    # Rundung gleich (10 min), Reihenfolge nach exaktem Umweg: 9,6 min vor 10,4 min – obwohl die
    # Abfahrt der zweiten Fahrt später ist. Die knapp zu große Fahrt (0,2004 > 0,20) fehlt.
    assert [i["ride_id"] for i in items] == [str(ride_early), str(ride_late)]
    assert r.json()["total"] == 2
    assert [i["detour_minutes"] for i in items] == [10, 10]
    assert all(isinstance(i["detour_minutes"], int) for i in items)
    assert [i["detour_km"] for i in items] == [
        coarse_detour_km(1_260.0),
        coarse_detour_km(1_240.0),
    ]
    assert all((i["detour_km"] * 2).is_integer() for i in items)
    assert all(i["detour_ratio"] == round(i["detour_ratio"], 2) for i in items)
    assert "e+" not in r.text
