"""Geo-Helfer: Snap-Prüfung, Routing-Fehler → API-Fehler, GeoJSON-Umwandlung.

Hintergrund: OSRM „snappt“ Punkte außerhalb des geladenen Extrakts still auf ferne Straßen
(München → 592 km im Hamburg-Extrakt). Jede vom Nutzer gelieferte Koordinate muss deshalb gegen
``MATCH_MAX_SNAP_DISTANCE_M`` geprüft werden, bevor mit ihr geroutet wird.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import ColumnElement, cast, func

from app.config import Settings
from app.errors import ApiError
from app.routing import LatLon, NoRouteError, RoutingClient, RoutingUnavailableError, Snap


@contextmanager
def routing_errors() -> Iterator[None]:
    """Übersetzt Routing-Fehler: nicht erreichbar → 503, keine Route → 422."""
    try:
        yield
    except RoutingUnavailableError:
        raise ApiError(503, "routing_unavailable", "Routing-Dienst nicht erreichbar") from None
    except NoRouteError:
        raise ApiError(
            422, "no_route", "Zwischen den angegebenen Punkten gibt es keine befahrbare Route"
        ) from None


async def snap_or_error(
    routing: RoutingClient, settings: Settings, point: LatLon, *, field: str
) -> Snap:
    """Nächste Straße zu ``point``; 422 ``outside_coverage``, wenn zu weit weg/nicht im Extrakt."""
    try:
        snap = await routing.nearest(point)
    except NoRouteError:
        snap = None
    except RoutingUnavailableError:
        raise ApiError(503, "routing_unavailable", "Routing-Dienst nicht erreichbar") from None
    if snap is None or snap.distance_m > settings.match_max_snap_distance_m:
        raise ApiError(
            422,
            "outside_coverage",
            f"{field}: Der Punkt liegt nicht in der Nähe einer befahrbaren Straße im "
            "abgedeckten Kartengebiet",
        )
    return snap


# --- Geometrie <-> JSON ------------------------------------------------------------------------

#: Vereinfachungstoleranz in Grad (≈ 55 m in Breitenrichtung); ST_Simplify auf dem Geometry-Cast,
#: weil ST_Simplify auf Geography nicht existiert und die Toleranz dort Grad wäre.
DEFAULT_SIMPLIFY_TOLERANCE_DEG = 0.0005


def simplified_route_geojson(
    column: Any, tolerance_deg: float = DEFAULT_SIMPLIFY_TOLERANCE_DEG
) -> ColumnElement[str]:
    """SQL-Ausdruck: vereinfachte Route als GeoJSON-Text (5 Nachkommastellen ≈ 1 m)."""
    return func.ST_AsGeoJSON(func.ST_Simplify(cast(column, Geometry), tolerance_deg), 5)


def geojson_linestring_to_latlon(geojson: str) -> list[list[float]]:
    """GeoJSON-LineString (lon,lat) → ``[[lat, lon], ...]`` (Konvention von API und Frontend)."""
    coords = json.loads(geojson)["coordinates"]
    return [[round(lat, 5), round(lon, 5)] for lon, lat in coords]


def point_wkt(point: LatLon) -> str:
    """WKT für ST_GeogFromText – Achtung: WKT ist (lon lat)."""
    lat, lon = point
    return f"POINT({lon} {lat})"
