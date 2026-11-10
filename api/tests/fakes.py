"""Deterministischer Routing-Fake für Tests (kein OSRM nötig).

Modell: Straßenentfernung = Luftlinie × ``detour_factor``; Fahrzeit = Strecke / ``speed_kmh``.
Das reicht für Logiktests (Umweg-Schwellen, Reihenfolge, Fehlerpfade). Realitätsnahe Tests
(Flussquerung, falsche Richtung) laufen gegen echtes OSRM (Marker ``osrm``).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from itertools import pairwise

from app.routing import NoRouteError, RouteResult, RoutingUnavailableError, Snap
from app.routing.base import LatLon

_EARTH_RADIUS_M = 6_371_008.8


def haversine_m(a: LatLon, b: LatLon) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


class FakeRouting:
    """``RoutingClient``-Testdouble.

    * ``bbox`` = (lat_min, lon_min, lat_max, lon_max): Punkte außerhalb → ``NoRouteError``
      (simuliert „nicht im Extrakt“). Standard: Großraum Hamburg.
    * ``snap_m``: Snap-Distanz, die ``nearest`` meldet (Funktion pro Punkt möglich).
    * ``unavailable=True``: jeder Aufruf wirft ``RoutingUnavailableError`` (→ API 503).
    * ``route_override``: optionale Funktion ``(waypoints) -> (distance_m, duration_s)`` für
      gezielte Szenarien (z. B. „kreuzende Route: kleine Luftlinie, großer realer Umweg“).
    """

    def __init__(
        self,
        *,
        bbox: tuple[float, float, float, float] = (53.2, 9.5, 53.9, 10.5),
        snap_m: float | Callable[[LatLon], float] = 8.0,
        detour_factor: float = 1.3,
        speed_kmh: float = 60.0,
        unavailable: bool = False,
        route_override: Callable[[Sequence[LatLon]], tuple[float, float]] | None = None,
    ) -> None:
        self.bbox = bbox
        self.snap_m = snap_m
        self.detour_factor = detour_factor
        self.speed_kmh = speed_kmh
        self.unavailable = unavailable
        self.route_override = route_override
        self.nearest_calls: list[LatLon] = []
        self.route_calls: list[tuple[LatLon, ...]] = []

    def _inside(self, p: LatLon) -> bool:
        lat_min, lon_min, lat_max, lon_max = self.bbox
        return lat_min <= p[0] <= lat_max and lon_min <= p[1] <= lon_max

    async def nearest(self, point: LatLon) -> Snap:
        self.nearest_calls.append(point)
        if self.unavailable:
            raise RoutingUnavailableError("fake: nicht erreichbar")
        if not self._inside(point):
            raise NoRouteError("fake: außerhalb des Extrakts")
        d = self.snap_m(point) if callable(self.snap_m) else self.snap_m
        return Snap(lat=point[0], lon=point[1], distance_m=d)

    async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        self.route_calls.append(tuple(waypoints))
        if self.unavailable:
            raise RoutingUnavailableError("fake: nicht erreichbar")
        if any(not self._inside(p) for p in waypoints):
            raise NoRouteError("fake: Wegpunkt außerhalb des Extrakts")
        if self.route_override is not None:
            distance_m, duration_s = self.route_override(waypoints)
        else:
            straight = sum(haversine_m(a, b) for a, b in pairwise(waypoints))
            distance_m = straight * self.detour_factor
            duration_s = distance_m / (self.speed_kmh / 3.6)
        points: tuple[LatLon, ...] = ()
        if geometry:
            points = tuple(self._interpolate(waypoints))
        return RouteResult(distance_m=distance_m, duration_s=duration_s, geometry=points)

    @staticmethod
    def _interpolate(waypoints: Sequence[LatLon], step_m: float = 400.0) -> list[LatLon]:
        """Gerade Segmente, etwa alle ``step_m`` Meter ein Punkt (damit Simplify/Trim greifen)."""
        out: list[LatLon] = [waypoints[0]]
        for a, b in pairwise(waypoints):
            n = max(1, int(haversine_m(a, b) // step_m))
            for i in range(1, n + 1):
                f = i / n
                out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        return out

    async def aclose(self) -> None:
        return None
