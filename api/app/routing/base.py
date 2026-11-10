"""Dünnes Routing-Interface. OSRM ist eine Implementierung; GraphHopper o. Ä. wäre austauschbar.

Konvention: Koordinaten sind in der gesamten Anwendung ``(lat, lon)``. Die Umkehrung
nach ``lon,lat`` (OSRM/GeoJSON) bleibt in den Implementierungen gekapselt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

LatLon = tuple[float, float]


class RoutingError(Exception):
    """Basisklasse für Routing-Fehler."""


class RoutingUnavailableError(RoutingError):
    """Routing-Dienst nicht erreichbar oder liefert Unsinn → API antwortet mit 503."""


class NoRouteError(RoutingError):
    """Keine Straße in der Nähe bzw. keine Verbindung zwischen den Punkten → fachlicher Fehler."""


@dataclass(frozen=True, slots=True)
class Snap:
    """Nächster Straßenpunkt zu einer Eingabekoordinate."""

    lat: float
    lon: float
    distance_m: float


@dataclass(frozen=True, slots=True)
class RouteResult:
    distance_m: float
    duration_s: float
    #: Routenverlauf als (lat, lon)-Punkte; leer, wenn ``geometry=False`` angefordert wurde.
    geometry: tuple[LatLon, ...]


class RoutingClient(Protocol):
    async def nearest(self, point: LatLon) -> Snap:
        """Nächste befahrbare Straße. Wirft :class:`NoRouteError`, wenn keine existiert."""
        ...

    async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        """Schnellste Route über die Wegpunkte (in Reihenfolge)."""
        ...

    async def aclose(self) -> None: ...
