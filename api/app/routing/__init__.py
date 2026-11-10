from __future__ import annotations

from app.config import get_settings
from app.routing.base import (
    LatLon,
    NoRouteError,
    RouteResult,
    RoutingClient,
    RoutingError,
    RoutingUnavailableError,
    Snap,
)
from app.routing.osrm import OsrmClient

__all__ = [
    "LatLon",
    "NoRouteError",
    "OsrmClient",
    "RouteResult",
    "RoutingClient",
    "RoutingError",
    "RoutingUnavailableError",
    "Snap",
    "close_routing_client",
    "get_routing_client",
]

_client: RoutingClient | None = None


def get_routing_client() -> RoutingClient:
    """FastAPI-Dependency; in Tests per ``app.dependency_overrides`` ersetzbar."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = OsrmClient(settings.osrm_url, settings.osrm_timeout_s)
    return _client


async def close_routing_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
