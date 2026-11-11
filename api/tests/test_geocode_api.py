"""GET /api/geocode: Zugriff, Validierung, Fehlerabbildung, Datenschutz (Fake-Geocoder)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.geocoding import (
    GeocodeHit,
    Geocoder,
    GeocoderBusyError,
    GeocoderUnavailableError,
    get_geocoder,
)
from app.main import app
from tests.helpers import create_user
from tests.test_match_support import login, override_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

SECRET_QUERY = "Geheimweg 17 Hamburg"


class FakeGeocoder(Geocoder):
    def __init__(
        self, hits: list[GeocodeHit] | None = None, error: Exception | None = None
    ) -> None:
        default = [GeocodeHit("Geheimweg 17, Hamburg", 53.55, 9.99)]
        self.hits = hits if hits is not None else default
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str) -> list[GeocodeHit]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.hits


def use(geocoder: Geocoder | None) -> None:
    app.dependency_overrides[get_geocoder] = lambda: geocoder


async def logged_in(client: httpx.AsyncClient, db: Any) -> None:
    login(client, await create_user(db))


async def test_requires_login(client: httpx.AsyncClient) -> None:
    use(FakeGeocoder())
    r = await client.get("/api/geocode", params={"q": SECRET_QUERY})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_returns_items(client: httpx.AsyncClient, db: Any) -> None:
    fake = FakeGeocoder()
    use(fake)
    await logged_in(client, db)
    r = await client.get("/api/geocode", params={"q": f"  {SECRET_QUERY}  "})
    assert r.status_code == 200
    assert r.json() == {"items": [{"label": "Geheimweg 17, Hamburg", "lat": 53.55, "lon": 9.99}]}
    assert fake.queries == [SECRET_QUERY]  # getrimmt


async def test_empty_result_is_200_with_empty_list(client: httpx.AsyncClient, db: Any) -> None:
    use(FakeGeocoder(hits=[]))
    await logged_in(client, db)
    r = await client.get("/api/geocode", params={"q": SECRET_QUERY})
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_disabled_geocoder_is_404(client: httpx.AsyncClient, db: Any) -> None:
    use(None)
    await logged_in(client, db)
    r = await client.get("/api/geocode", params={"q": SECRET_QUERY})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "geocoder_disabled"


async def test_default_settings_do_not_enable_it(client: httpx.AsyncClient, db: Any) -> None:
    app.dependency_overrides.pop(get_geocoder, None)
    await logged_in(client, db)
    with override_settings(geocoder="none"):
        r = await client.get("/api/geocode", params={"q": SECRET_QUERY})
    assert r.status_code == 404


@pytest.mark.parametrize("q", ["", "zq", "  y  ", "Q" * 201])
async def test_validation_errors_do_not_echo_input(
    client: httpx.AsyncClient, db: Any, q: str
) -> None:
    use(FakeGeocoder())
    await logged_in(client, db)
    r = await client.get("/api/geocode", params={"q": q})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert q.strip() == "" or q not in r.text


async def test_missing_q_is_422(client: httpx.AsyncClient, db: Any) -> None:
    use(FakeGeocoder())
    await logged_in(client, db)
    assert (await client.get("/api/geocode")).status_code == 422


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (GeocoderUnavailableError("weg"), "geocoder_unavailable"),
        (GeocoderBusyError("voll"), "geocoder_busy"),
    ],
)
async def test_upstream_problems_are_503_without_leaking_details(
    client: httpx.AsyncClient, db: Any, error: Exception, code: str
) -> None:
    use(FakeGeocoder(error=error))
    await logged_in(client, db)
    r = await client.get("/api/geocode", params={"q": SECRET_QUERY})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == code
    assert SECRET_QUERY not in r.text


async def test_rate_limited_per_user(client: httpx.AsyncClient, db: Any) -> None:
    use(FakeGeocoder())
    await logged_in(client, db)
    with override_settings(rate_limit_enabled=True, rate_limit_geocode_user_per_min=2):
        codes = [
            (await client.get("/api/geocode", params={"q": f"{SECRET_QUERY} {i}"})).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]


async def test_query_is_never_logged(
    client: httpx.AsyncClient, db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    use(FakeGeocoder())
    await logged_in(client, db)
    with caplog.at_level(logging.DEBUG):
        await client.get("/api/geocode", params={"q": SECRET_QUERY})
        use(FakeGeocoder(error=GeocoderUnavailableError("x")))
        await client.get("/api/geocode", params={"q": SECRET_QUERY})
    assert SECRET_QUERY not in caplog.text
    assert "Geheimweg" not in caplog.text
    assert any("/api/geocode" in rec.getMessage() for rec in caplog.records)
