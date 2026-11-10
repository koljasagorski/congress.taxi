"""OsrmClient gegen eine gemockte HTTP-Transportschicht (kein OSRM nötig)."""

from __future__ import annotations

import httpx
import pytest

from app.routing import NoRouteError, OsrmClient, RoutingUnavailableError

pytestmark = pytest.mark.asyncio(loop_scope="session")


def client_with(handler: httpx.MockTransport | None = None, **json_kwargs: object) -> OsrmClient:
    if handler is None:
        handler = httpx.MockTransport(lambda _req: httpx.Response(**json_kwargs))  # type: ignore[arg-type]
    return OsrmClient("http://osrm.test", transport=handler)


async def test_nearest_parses_snap_and_swaps_axes() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "waypoints": [{"location": [9.99, 53.55], "distance": 12.5, "name": "x"}],
            },
        )

    snap = await client_with(httpx.MockTransport(handler)).nearest((53.5501, 9.9901))
    assert (snap.lat, snap.lon, snap.distance_m) == (53.55, 9.99, 12.5)
    # OSRM erwartet lon,lat
    assert seen == ["/nearest/v1/driving/9.990100,53.550100"]


async def test_route_parses_distance_duration_and_geometry() -> None:
    payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 1234.5,
                "duration": 99.0,
                "geometry": {"type": "LineString", "coordinates": [[10.0, 53.5], [9.9, 53.6]]},
            }
        ],
    }
    route = await client_with(status_code=200, json=payload).route([(53.5, 10.0), (53.6, 9.9)])
    assert route.distance_m == 1234.5
    assert route.duration_s == 99.0
    assert route.geometry == ((53.5, 10.0), (53.6, 9.9))


async def test_route_without_geometry_requests_overview_false() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"code": "Ok", "routes": [{"distance": 1, "duration": 2}]})

    route = await client_with(httpx.MockTransport(handler)).route(
        [(53.5, 10.0), (53.6, 9.9)], geometry=False
    )
    assert route.geometry == ()
    assert seen[0].params["overview"] == "false"


@pytest.mark.parametrize("code", ["NoRoute", "NoSegment"])
async def test_no_route_codes_are_domain_errors(code: str) -> None:
    client = client_with(status_code=400 if code == "NoSegment" else 200, json={"code": code})
    with pytest.raises(NoRouteError):
        await client.nearest((0.0, 0.0))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status_code": 500, "text": "boom"},
        {"status_code": 200, "text": "<html>kein json</html>"},
        {"status_code": 200, "json": {"unexpected": True}},
        {"status_code": 400, "json": {"code": "InvalidQuery"}},
        {"status_code": 200, "json": {"code": "Ok", "waypoints": []}},
    ],
)
async def test_garbage_means_service_unavailable(kwargs: dict[str, object]) -> None:
    with pytest.raises(RoutingUnavailableError):
        await client_with(**kwargs).nearest((53.5, 10.0))


async def test_connection_error_means_service_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(RoutingUnavailableError):
        await client_with(httpx.MockTransport(handler)).route([(53.5, 10.0), (53.6, 9.9)])


async def test_route_needs_two_waypoints() -> None:
    with pytest.raises(ValueError, match="zwei"):
        await client_with(status_code=200, json={}).route([(53.5, 10.0)])
