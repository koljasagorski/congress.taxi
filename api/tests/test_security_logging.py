"""Logs ohne personenbezogene Daten: Request-Log, Fehler-Log und Rate-Limit-Antworten."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter

from app.config import Settings, get_settings

CLIENT_IP = "203.0.113.77"
EMAIL = "geheim.person@example.org"
LAT, LON = "53.551086", "9.993682"
FORBIDDEN = (CLIENT_IP, EMAIL, "geheim.person", LAT, LON, "lat=", "lon=", "?")


def app_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    # Bewusst KEIN Filter auf httpx/httpcore: Der Produktions-OSRM-Client ist ein httpx-Client,
    # dessen Anfrage-URL Koordinaten enthält. Die App muss diese Logger selbst stummschalten
    # (``install_security``); der Testclient ist davon ebenso betroffen und stört nicht.
    return [r.getMessage() for r in caplog.records if not r.name.startswith("asyncio")]


@pytest_asyncio.fixture
async def limited_client(client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """Echte App mit aktiven Limits, erreichbar von CLIENT_IP aus."""
    from app.main import app

    router = APIRouter()

    @router.get("/api/_log_boom/{who}")
    async def boom(who: str) -> None:
        raise RuntimeError(f"Konto {who} lat={LAT}")

    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        rate_limit_enabled=True,
        rate_limit_api_per_min=5,
        rate_limit_match_per_min=2,
        jwt_secret="x" * 40,  # type: ignore[arg-type]
    )
    transport = httpx.ASGITransport(app=app, client=(CLIENT_IP, 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.router.routes[:] = [
        r for r in app.router.routes if "_log_boom" not in getattr(r, "path", "")
    ]


async def test_logs_contain_neither_query_ip_nor_email(
    limited_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    c = limited_client
    with caplog.at_level(logging.DEBUG):
        # Koordinaten im Query (Match), E-Mail im Pfad, Header und Body, Fehler und Rate-Limit
        await c.get("/api/match", params={"lat": LAT, "lon": LON})
        await c.get("/api/match", params={"lat": LAT, "lon": LON, "time_from": "2030-01-01"})
        blocked = await c.get("/api/match", params={"lat": LAT, "lon": LON})  # Match-Limit (2)
        await c.get(f"/api/nutzer/{EMAIL}", params={"email": EMAIL})
        error = await c.get(f"/api/_log_boom/{EMAIL}", params={"lat": LAT, "lon": LON})
        await c.get("/api/health", params={"lat": LAT, "lon": LON})
        await c.post(
            "/api/health",
            json={"email": EMAIL, "password": "pw-pw-pw-pw"},
            headers={"X-Forwarded-For": CLIENT_IP, "X-Email": EMAIL},
        )
        limited = await c.get("/api/config", params={"lat": LAT})  # globales Limit (5) erreicht
    assert blocked.status_code == 429
    assert error.status_code == 500
    assert limited.status_code == 429
    records = app_records(caplog)
    assert records, "es sollte überhaupt geloggt worden sein (Request-Log)"
    text = "\n".join(records)
    for needle in FORBIDDEN:
        assert needle not in text, f"{needle!r} im Log: {text}"
    # Fehler-Log nennt nur den Typ
    assert "Unbehandelter Fehler: RuntimeError" in text


async def test_rate_limit_responses_do_not_echo_the_client(
    limited_client: httpx.AsyncClient,
) -> None:
    c = limited_client
    for _ in range(5):
        await c.get("/api/config", params={"lat": LAT})
    r = await c.get("/api/config", params={"lat": LAT, "email": EMAIL})
    assert r.status_code == 429
    for needle in (CLIENT_IP, EMAIL, LAT):
        assert needle not in r.text
        assert needle not in "".join(f"{k}={v}" for k, v in r.headers.items())


async def test_routing_client_does_not_log_coordinates(caplog: pytest.LogCaptureFixture) -> None:
    """Der OSRM-Client ruft per httpx Pfade mit Koordinaten ab; httpx protokolliert auf INFO jede
    Anfrage mit vollständiger URL. Das darf nie im Betriebslog landen."""
    import app.main  # noqa: F401  # installiert das Logging wie im Betrieb
    from app.routing.osrm import OsrmClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "waypoints": [{"location": [float(LON), float(LAT)], "distance": 3.0}],
            },
        )

    client = OsrmClient("http://osrm.invalid", 5.0, transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        await client.nearest((float(LAT), float(LON)))
    text = "\n".join(r.getMessage() for r in caplog.records)
    for needle in (LAT, LON, "9.993682", "53.551086", "osrm.invalid"):
        assert needle not in text, f"{needle!r} im Log: {text}"


def test_install_security_silences_http_client_loggers() -> None:
    from fastapi import FastAPI

    from app.middleware.security import install_security

    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    install_security(FastAPI())
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
