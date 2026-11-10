"""Randfälle der Umwegberechnung (``app.services.detour``) – Signatur und Verhalten sind Vertrag."""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from app.config import Settings
from app.routing import NoRouteError, RoutingUnavailableError
from app.routing.base import LatLon
from app.services.detour import Detour, compute_detour, is_acceptable
from tests.fakes import FakeRouting
from tests.helpers import EVENT, NORTH

PICKUP: LatLon = (53.655, 9.969)


def scripted(distance_m: float, duration_s: float) -> FakeRouting:
    def via(_: Sequence[LatLon]) -> tuple[float, float]:
        return distance_m, duration_s

    return FakeRouting(route_override=via)


async def detour(
    routing: FakeRouting,
    settings: Settings | None = None,
    *,
    base_distance_m: float = 10_000.0,
    base_duration_s: float = 600.0,
) -> Detour:
    return await compute_detour(
        routing,
        settings or Settings(),
        driver_start=NORTH,
        pickup=PICKUP,
        event=EVENT,
        base_distance_m=base_distance_m,
        base_duration_s=base_duration_s,
    )


async def test_routes_driver_start_then_pickup_then_event_without_geometry() -> None:
    fake = FakeRouting()
    await detour(fake)
    assert fake.route_calls == [(NORTH, PICKUP, EVENT)]


async def test_shorter_via_route_is_clamped_to_zero_detour() -> None:
    d = await detour(scripted(9_000.0, 500.0))  # schneller als die Basisroute (OSRM-Rauschen)
    assert d.distance_m == 0.0 and d.duration_s == 0.0 and d.ratio == 0.0 and d.acceptable


async def test_exact_thresholds_are_inclusive() -> None:
    s = Settings(
        match_max_detour_ratio=0.20, match_max_detour_minutes=25.0, match_detour_mode="all"
    )
    on_limit = await detour(scripted(12_000.0, 600.0 + 25 * 60.0), s)
    assert on_limit.ratio == pytest.approx(0.2) and on_limit.acceptable
    over = await detour(scripted(12_001.0, 600.0 + 25 * 60.0), s)
    assert not over.acceptable


async def test_zero_base_distance_without_detour_is_ratio_zero() -> None:
    d = await detour(scripted(0.0, 0.0), base_distance_m=0.0, base_duration_s=0.0)
    assert d.ratio == 0.0 and d.acceptable


async def test_zero_base_distance_with_detour_is_infinite_ratio() -> None:
    d = await detour(scripted(500.0, 60.0), base_distance_m=0.0, base_duration_s=0.0)
    assert math.isinf(d.ratio)
    # Modus "any": Zeit (1 min) genügt; Modus "all": die Distanzbedingung scheitert
    assert d.acceptable
    strict = await detour(
        scripted(500.0, 60.0),
        Settings(match_detour_mode="all"),
        base_distance_m=0.0,
        base_duration_s=0.0,
    )
    assert not strict.acceptable


async def test_ratio_is_relative_to_the_stored_base_distance() -> None:
    d = await detour(scripted(15_000.0, 900.0), base_distance_m=10_000.0, base_duration_s=600.0)
    assert d.distance_m == 5_000.0 and d.duration_s == 300.0 and d.ratio == pytest.approx(0.5)
    assert d.acceptable  # 0,5 > 20 %, aber 5 min ≤ 25 min → "any"


async def test_routing_errors_propagate_unchanged() -> None:
    with pytest.raises(RoutingUnavailableError):
        await detour(FakeRouting(unavailable=True))

    def no_route(_: Sequence[LatLon]) -> tuple[float, float]:
        raise NoRouteError("keine Verbindung")

    with pytest.raises(NoRouteError):
        await detour(FakeRouting(route_override=no_route))


@pytest.mark.parametrize("mode", ["any", "all"])
def test_is_acceptable_is_pure_and_monotonic(mode: str) -> None:
    s = Settings(match_detour_mode=mode)  # type: ignore[arg-type]
    small = is_acceptable(s, distance_m=100.0, duration_s=60.0, ratio=0.01)
    huge = is_acceptable(s, distance_m=90_000.0, duration_s=7_200.0, ratio=3.0)
    assert small is True and huge is False
