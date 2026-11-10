"""Umwegberechnung (Matching und Anfrage-Erstellung teilen sie).

Vorgabe: Der Fahrer fährt real zum Pickup. Verglichen wird die OSRM-Route
Fahrer-Start → Pickup → Event mit der gespeicherten Basisroute (``route_distance_m`` /
``route_duration_s``). Der Pickup wird NICHT auf die Route projiziert.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.routing import LatLon, RoutingClient


@dataclass(frozen=True, slots=True)
class Detour:
    distance_m: float  # Mehrdistanz, ≥ 0
    duration_s: float  # Mehrfahrzeit, ≥ 0
    ratio: float  # distance_m / Basisdistanz (inf bei Basisdistanz 0 und Umweg > 0)
    acceptable: bool


def is_acceptable(
    settings: Settings, *, distance_m: float, duration_s: float, ratio: float
) -> bool:
    """„max. 20 % Mehrdistanz ODER max. 25 Minuten“ (``MATCH_DETOUR_MODE=any``, wörtlich);
    ``all`` verlangt beides."""
    distance_ok = ratio <= settings.match_max_detour_ratio
    time_ok = duration_s / 60.0 <= settings.match_max_detour_minutes
    return (
        (distance_ok or time_ok)
        if settings.match_detour_mode == "any"
        else (distance_ok and time_ok)
    )


async def compute_detour(
    routing: RoutingClient,
    settings: Settings,
    *,
    driver_start: LatLon,
    pickup: LatLon,
    event: LatLon,
    base_distance_m: float,
    base_duration_s: float,
) -> Detour:
    """Wirft ``RoutingUnavailableError``/``NoRouteError`` (übersetzen via ``routing_errors``)."""
    via = await routing.route([driver_start, pickup, event], geometry=False)
    distance_m = max(0.0, via.distance_m - base_distance_m)
    duration_s = max(0.0, via.duration_s - base_duration_s)
    if base_distance_m > 0:
        ratio = distance_m / base_distance_m
    else:
        ratio = 0.0 if distance_m == 0 else float("inf")
    return Detour(
        distance_m=distance_m,
        duration_s=duration_s,
        ratio=ratio,
        acceptable=is_acceptable(
            settings, distance_m=distance_m, duration_s=duration_s, ratio=ratio
        ),
    )
