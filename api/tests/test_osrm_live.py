"""Tests gegen einen echten OSRM mit **Hamburg-Extrakt** (Marker ``osrm``).

Werden übersprungen, wenn OSRM nicht erreichbar ist. Sie belegen reale Eigenschaften, auf die
die Matching-Logik angewiesen ist (insbesondere das stille Snappen auf ferne Straßen).
"""

from __future__ import annotations

import math
import os

import pytest
import pytest_asyncio

from app.routing import NoRouteError, OsrmClient, RoutingUnavailableError

pytestmark = [pytest.mark.osrm, pytest.mark.asyncio(loop_scope="session")]

EVENT = (53.5615, 9.9885)  # Platzhalter-Ziel (CCH Hamburg)
ALTONA = (53.5511, 9.9265)
HARBURG = (53.4609, 9.9831)  # südlich der Elbe
MUENCHEN = (48.1351, 11.5820)  # weit außerhalb des Extrakts


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = p2 - p1
    dlmb = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@pytest_asyncio.fixture(loop_scope="session")
async def osrm() -> OsrmClient:
    client = OsrmClient(os.environ.get("OSRM_URL", "http://127.0.0.1:55000"), timeout_s=5)
    try:
        await client.nearest(EVENT)
    except (RoutingUnavailableError, NoRouteError):
        await client.aclose()
        pytest.skip("OSRM mit Hamburg-Extrakt nicht erreichbar")
    return client


async def test_event_is_close_to_a_road(osrm: OsrmClient) -> None:
    snap = await osrm.nearest(EVENT)
    assert snap.distance_m < 500


async def test_far_away_point_is_silently_snapped_to_a_distant_road(osrm: OsrmClient) -> None:
    """OSRM wirft hier KEINEN Fehler, sondern liefert eine Straße hunderte km entfernt.

    Deshalb ist die Snap-Distanzprüfung (MATCH_MAX_SNAP_DISTANCE_M) zwingend.
    """
    snap = await osrm.nearest(MUENCHEN)
    assert snap.distance_m > 100_000


async def test_route_is_plausible(osrm: OsrmClient) -> None:
    route = await osrm.route([ALTONA, EVENT])
    straight = haversine_m(ALTONA, EVENT)
    assert straight < route.distance_m < straight * 2.5
    assert 0 < route.duration_s < 3600
    assert len(route.geometry) > 10
    # Geometrie beginnt/endet nahe der Eingabepunkte (Snap-Toleranz)
    assert haversine_m(route.geometry[0], ALTONA) < 500
    assert haversine_m(route.geometry[-1], EVENT) < 500


async def test_river_crossing_has_small_beeline_but_large_real_distance(osrm: OsrmClient) -> None:
    """Grundlage für den Matching-Randfall „kleine Luftlinie, großer realer Umweg“ (Elbe)."""
    route = await osrm.route([HARBURG, EVENT], geometry=False)
    straight = haversine_m(HARBURG, EVENT)
    assert route.distance_m > straight * 1.3
    assert route.geometry == ()
