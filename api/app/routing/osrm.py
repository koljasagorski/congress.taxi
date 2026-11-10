"""OSRM-Implementierung des :class:`RoutingClient` (HTTP-API, Profil ``driving``/car)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.routing.base import (
    LatLon,
    NoRouteError,
    RouteResult,
    RoutingUnavailableError,
    Snap,
)

# OSRM-Antwortcodes, die „fachlich keine Route/Straße" bedeuten (kein Dienstausfall)
_NO_ROUTE_CODES = {"NoRoute", "NoSegment", "NoMatch"}


def _coord(point: LatLon) -> str:
    lat, lon = point
    return f"{lon:.6f},{lat:.6f}"


class OsrmClient:
    def __init__(
        self,
        base_url: str,
        timeout_s: float = 5.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)

    async def _get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise RoutingUnavailableError("OSRM nicht erreichbar") from exc

        if response.status_code >= 500:
            raise RoutingUnavailableError(f"OSRM-Serverfehler {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RoutingUnavailableError("OSRM lieferte kein JSON") from exc
        if not isinstance(data, dict) or "code" not in data:
            raise RoutingUnavailableError("Unerwartete OSRM-Antwort")

        code = data["code"]
        if code in _NO_ROUTE_CODES:
            raise NoRouteError(str(data.get("message", code)))
        if code != "Ok":
            # z. B. InvalidQuery/InvalidUrl: ein Fehler auf unserer Seite, nicht des Nutzers
            raise RoutingUnavailableError(f"OSRM-Fehlercode {code}")
        return data

    async def nearest(self, point: LatLon) -> Snap:
        data = await self._get(f"/nearest/v1/driving/{_coord(point)}", {"number": 1})
        try:
            wp = data["waypoints"][0]
            lon, lat = wp["location"]
            return Snap(lat=float(lat), lon=float(lon), distance_m=float(wp["distance"]))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingUnavailableError("Unerwartete OSRM-nearest-Antwort") from exc

    async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        if len(waypoints) < 2:
            raise ValueError("Mindestens zwei Wegpunkte nötig")
        coords = ";".join(_coord(p) for p in waypoints)
        params: dict[str, str | int] = {
            "overview": "full" if geometry else "false",
            "geometries": "geojson",
            "steps": "false",
            "alternatives": "false",
        }
        data = await self._get(f"/route/v1/driving/{coords}", params)
        try:
            best = data["routes"][0]
            line: tuple[LatLon, ...] = ()
            if geometry:
                line = tuple(
                    (float(lat), float(lon)) for lon, lat in best["geometry"]["coordinates"]
                )
            return RouteResult(
                distance_m=float(best["distance"]),
                duration_s=float(best["duration"]),
                geometry=line,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RoutingUnavailableError("Unerwartete OSRM-route-Antwort") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
