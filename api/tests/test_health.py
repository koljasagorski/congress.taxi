from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

import httpx
import pytest
import pytest_asyncio

from app.main import app
from app.routing import (
    NoRouteError,
    RouteResult,
    RoutingClient,
    RoutingUnavailableError,
    Snap,
    get_routing_client,
)
from app.routing.base import LatLon

pytestmark = pytest.mark.asyncio(loop_scope="session")


class FakeRouting:
    """Testdouble für den RoutingClient."""

    def __init__(self, *, snap_m: float | None = 8.0, error: Exception | None = None) -> None:
        self.snap_m = snap_m
        self.error = error

    async def nearest(self, point: LatLon) -> Snap:
        if self.error:
            raise self.error
        assert self.snap_m is not None
        return Snap(lat=point[0], lon=point[1], distance_m=self.snap_m)

    async def route(self, waypoints: Sequence[LatLon], *, geometry: bool = True) -> RouteResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture(loop_scope="session")
async def client(migrated_database: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def use_routing(fake: RoutingClient) -> None:
    app.dependency_overrides[get_routing_client] = lambda: fake


async def test_liveness_needs_no_dependencies(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_ok(client: httpx.AsyncClient) -> None:
    use_routing(FakeRouting(snap_m=8.0))
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["database"]["postgis"]  # PostGIS-Version vorhanden
    assert body["checks"]["osrm"] == {"ok": True, "event_snap_distance_m": 8.0}


async def test_readiness_503_when_osrm_down(client: httpx.AsyncClient) -> None:
    use_routing(FakeRouting(error=RoutingUnavailableError("down")))
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["osrm"] == {"ok": False, "error": "osrm_unavailable"}
    assert body["checks"]["database"]["ok"] is True  # Fehler sind isoliert


async def test_readiness_flags_event_outside_extract(client: httpx.AsyncClient) -> None:
    use_routing(FakeRouting(error=NoRouteError("NoSegment")))
    body = (await client.get("/api/health/ready")).json()
    assert body["checks"]["osrm"]["error"] == "event_not_covered_by_osrm_extract"


async def test_readiness_flags_event_too_far_from_road(client: httpx.AsyncClient) -> None:
    use_routing(FakeRouting(snap_m=9000.0))  # OSRM snappt still auf weit entfernte Straße
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["osrm"]["error"] == "event_too_far_from_road"


async def test_unknown_route_uses_unified_error_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/gibt-es-nicht")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_request_log_contains_no_query_or_ip(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.request"):
        await client.get("/api/health?lat=53.5511&lon=9.9937")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "/api/health" in text
    assert "53.5511" not in text
    assert "9.9937" not in text
    assert "lat=" not in text
