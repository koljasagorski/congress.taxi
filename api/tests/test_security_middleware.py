"""Security-Middleware: Header, Origin-Prüfung, globales Rate-Limit, Fehlerpfade.

Getestet an einer Mini-App (Routen wie in der echten API) und am echten ``app.main.app``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.middleware.security import install_security
from app.ratelimit import limiter

EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
}


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "rate_limit_enabled": True,
        "rate_limit_api_per_min": 5,
        "rate_limit_match_per_min": 2,
        "cookie_secure": True,
        "domain": "example.org",
        "jwt_secret": "x" * 40,
    }
    base.update(overrides)
    return Settings(**base)


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_security(app)
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/api/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/match")
    async def match() -> dict[str, list[Any]]:
        return {"items": []}

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/elsewhere")
    async def elsewhere() -> dict[str, bool]:
        return {"ok": True}

    for method in ("post", "put", "patch", "delete"):
        getattr(app, method)("/api/write")(lambda: {"ok": True})

    @app.get("/api/boom")
    async def boom() -> None:
        raise RuntimeError("geheim@example.org lat=53.5511 lon=9.9937")

    return app


Factory = Callable[..., httpx.AsyncClient]


class ProbeBody(BaseModel):
    email: str


@pytest_asyncio.fixture
async def make_client() -> AsyncIterator[Factory]:
    limiter.reset()
    clients: list[httpx.AsyncClient] = []

    def factory(
        settings: Settings | None = None, peer: str = "198.51.100.10", app: FastAPI | None = None
    ) -> httpx.AsyncClient:
        app = app or build_app(settings or make_settings())
        transport = httpx.ASGITransport(app=app, client=(peer, 40000))
        c = httpx.AsyncClient(transport=transport, base_url="http://test")
        clients.append(c)
        return c

    yield factory
    for c in clients:
        await c.aclose()
    limiter.reset()


# --- Header -----------------------------------------------------------------------------------


def assert_security_headers(r: httpx.Response, *, api: bool = True) -> None:
    for name, value in EXPECTED_HEADERS.items():
        assert r.headers[name] == value, name
    assert "geolocation=()" in r.headers["permissions-policy"]
    if api:
        assert r.headers["cache-control"] == "no-store"
    else:
        assert "cache-control" not in r.headers


async def test_headers_on_every_api_response(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    ok = await c.get("/api/ping")
    assert ok.status_code == 200
    assert_security_headers(ok)
    assert_security_headers(await c.get("/api/gibt-es-nicht"))  # 404 aus dem Router
    assert_security_headers(await c.delete("/api/ping"))  # 405
    assert_security_headers(await c.post("/api/ping", content=b"x"))  # 405
    assert_security_headers(await c.get("/api"))  # kein Eintrag, aber unter /api


async def test_cache_control_only_for_api_paths(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    r = await c.get("/elsewhere")
    assert r.status_code == 200
    assert_security_headers(r, api=False)


async def test_headers_on_403_and_429(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=1))
    forbidden = await c.post("/api/write", headers={"Origin": "https://evil.example"})
    assert forbidden.status_code == 403
    assert_security_headers(forbidden)
    assert (await c.get("/api/ping")).status_code == 200
    limited = await c.get("/api/ping")
    assert limited.status_code == 429
    assert_security_headers(limited)


async def test_unhandled_error_gets_headers_and_leaks_nothing(
    make_client: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    with caplog.at_level(logging.DEBUG):
        r = await c.get("/api/boom", params={"lat": "53.5511", "lon": "9.9937"})
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Interner Fehler"}}
    assert_security_headers(r)
    # httpx (Testclient) loggt seine eigene URL; relevant ist, was die App schreibt.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records if rec.name.startswith("app"))
    assert "RuntimeError" in log_text
    for secret in ("geheim@example.org", "53.5511", "9.9937"):
        assert secret not in log_text
        assert secret not in r.text


async def test_unhandled_error_does_not_propagate_to_the_server(make_client: Factory) -> None:
    # httpx.ASGITransport würde eine nach oben durchgereichte Exception im Test auslösen
    c = make_client(make_settings(rate_limit_enabled=False))
    r = await c.get("/api/boom")
    assert r.status_code == 500


# --- Origin-Prüfung ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_foreign_origin_is_rejected_on_unsafe_methods(
    make_client: Factory, method: str
) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    r = await c.request(method, "/api/write", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
    assert "evil.example" not in r.text  # Eingabe nicht zurückspiegeln


@pytest.mark.parametrize(
    "origin", ["https://example.org", "https://example.org/", "HTTPS://Example.ORG"]
)
async def test_own_origin_is_allowed(make_client: Factory, origin: str) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    assert (await c.post("/api/write", headers={"Origin": origin})).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "",
        "http://example.org",  # falsches Schema (erwartet wird https)
        "https://example.org:8443",
        "https://example.org.evil.example",
        "https://sub.example.org",
        "https://example.org/pfad",
    ],
)
async def test_lookalike_origins_are_rejected(make_client: Factory, origin: str) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    r = await c.post("/api/write", headers={"Origin": origin})
    assert r.status_code == 403, origin


async def test_missing_origin_is_allowed_and_safe_methods_are_not_checked(
    make_client: Factory,
) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    assert (await c.post("/api/write")).status_code == 200
    assert (await c.get("/api/ping", headers={"Origin": "https://evil.example"})).status_code == 200


async def test_one_bad_origin_header_among_several_is_rejected(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    headers = [("Origin", "https://example.org"), ("Origin", "https://evil.example")]
    assert (await c.post("/api/write", headers=headers)).status_code == 403


async def test_explicit_allowed_origins_replace_the_derived_one(make_client: Factory) -> None:
    s = make_settings(
        rate_limit_enabled=False, allowed_origins="http://localhost:5173, https://a.example/"
    )
    c = make_client(s)
    assert (
        await c.post("/api/write", headers={"Origin": "http://localhost:5173"})
    ).status_code == 200
    assert (await c.post("/api/write", headers={"Origin": "https://a.example"})).status_code == 200
    assert (
        await c.post("/api/write", headers={"Origin": "https://example.org"})
    ).status_code == 403


async def test_origin_is_checked_even_if_rate_limit_is_disabled(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_enabled=False))
    r = await c.post("/api/write", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# --- globales Rate-Limit ----------------------------------------------------------------------


async def test_global_limit_returns_429_with_retry_after(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=3))
    codes = [(await c.get("/api/ping")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = await c.get("/api/ping")
    assert r.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert "198.51.100.10" not in r.text


async def test_liveness_is_exempt_from_the_limit(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=1))
    assert [(await c.get("/api/health")).status_code for _ in range(5)] == [200] * 5
    assert (await c.get("/api/ping")).status_code == 200  # Budget unberührt
    assert (await c.get("/api/ping")).status_code == 429


async def test_readiness_is_rate_limited(make_client: Factory) -> None:
    """/api/health/ready belegt eine DB-Verbindung und ruft OSRM auf – nicht unbegrenzt abrufbar."""
    from app.middleware.security import READY_PER_MIN

    c = make_client(make_settings(rate_limit_api_per_min=1000))
    codes = [(await c.get("/api/health/ready")).status_code for _ in range(READY_PER_MIN + 3)]
    assert codes == [200] * READY_PER_MIN + [429] * 3
    # die Liveness bleibt davon unberührt
    assert (await c.get("/api/health")).status_code == 200


async def test_readiness_also_counts_against_the_global_limit(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=2))
    assert (await c.get("/api/health/ready")).status_code == 200
    assert (await c.get("/api/health/ready")).status_code == 200
    assert (await c.get("/api/health/ready")).status_code == 429


async def test_non_api_paths_are_not_limited(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=1))
    assert [(await c.get("/elsewhere")).status_code for _ in range(4)] == [200] * 4


async def test_match_has_its_own_stricter_limit(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=10, rate_limit_match_per_min=2))
    codes = [
        (await c.get("/api/match", params={"lat": 53.5, "lon": 9.9})).status_code for _ in range(3)
    ]
    assert codes == [200, 200, 429]
    assert (await c.get("/api/ping")).status_code == 200  # andere Pfade nur global begrenzt


async def test_match_limit_also_applies_with_trailing_slash(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=10, rate_limit_match_per_min=1))
    assert (await c.get("/api/match")).status_code == 200
    assert (await c.get("/api/match/")).status_code == 429


async def test_limits_are_per_client_ip(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1)
    app = build_app(s)
    a = make_client(app=app, peer="198.51.100.1")
    b = make_client(app=app, peer="198.51.100.2")
    assert (await a.get("/api/ping")).status_code == 200
    assert (await a.get("/api/ping")).status_code == 429
    assert (await b.get("/api/ping")).status_code == 200


async def test_disabled_rate_limit_never_blocks(make_client: Factory) -> None:
    c = make_client(make_settings(rate_limit_enabled=False, rate_limit_api_per_min=1))
    assert [(await c.get("/api/ping")).status_code for _ in range(10)] == [200] * 10


async def test_forwarded_header_from_untrusted_peer_cannot_dodge_the_limit(
    make_client: Factory,
) -> None:
    c = make_client(make_settings(rate_limit_api_per_min=1))
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "1.1.1.1"})).status_code == 200
    # gefälschter Header, anderer "Client" – derselbe Peer, also dasselbe Limit
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "2.2.2.2"})).status_code == 429


async def test_trusted_proxy_separates_clients_by_forwarded_for(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1, trusted_proxies="172.18.0.0/16")
    c = make_client(s, peer="172.18.0.5")
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "203.0.113.1"})).status_code == 200
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "203.0.113.2"})).status_code == 200
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "203.0.113.1"})).status_code == 429


async def test_trusted_proxy_forged_leftmost_entry_does_not_help(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1, trusted_proxies="172.18.0.0/16")
    c = make_client(s, peer="172.18.0.5")
    # Der Client stellt "6.6.6.6" voran; der Proxy hängt die echte Adresse hinten an.
    assert (
        await c.get("/api/ping", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.1"})
    ).status_code == 200
    assert (
        await c.get("/api/ping", headers={"X-Forwarded-For": "7.7.7.7, 203.0.113.1"})
    ).status_code == 429


async def test_trusted_proxy_with_several_forwarded_header_lines(make_client: Factory) -> None:
    # Zwei Header-Zeilen = eine kommagetrennte Liste; die zweite (vom Proxy) zählt.
    s = make_settings(rate_limit_api_per_min=1, trusted_proxies="172.18.0.0/16")
    c = make_client(s, peer="172.18.0.5")
    first = [("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "203.0.113.1")]
    second = [("X-Forwarded-For", "7.7.7.7"), ("X-Forwarded-For", "203.0.113.1")]
    assert (await c.get("/api/ping", headers=first)).status_code == 200
    assert (await c.get("/api/ping", headers=second)).status_code == 429


async def test_ipv6_clients_share_their_slash_64(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1, trusted_proxies="172.18.0.0/16")
    c = make_client(s, peer="172.18.0.5")
    one = {"X-Forwarded-For": "2001:db8:1:2::1"}
    same_prefix = {"X-Forwarded-For": "2001:db8:1:2:abcd:ef01:2345:6789"}
    other_prefix = {"X-Forwarded-For": "2001:db8:1:3::1"}
    assert (await c.get("/api/ping", headers=one)).status_code == 200
    assert (await c.get("/api/ping", headers=same_prefix)).status_code == 429
    assert (await c.get("/api/ping", headers=other_prefix)).status_code == 200


async def test_ipv4_mapped_ipv6_peer_counts_as_ipv4(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1)
    app = build_app(s)
    mapped = make_client(app=app, peer="::ffff:198.51.100.7")
    plain = make_client(app=app, peer="198.51.100.7")
    assert (await mapped.get("/api/ping")).status_code == 200
    assert (await plain.get("/api/ping")).status_code == 429


async def test_ipv6_zone_id_does_not_create_new_identities(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1, trusted_proxies="172.18.0.0/16")
    c = make_client(s, peer="172.18.0.5")
    assert (
        await c.get("/api/ping", headers={"X-Forwarded-For": "fe80::1%eth0"})
    ).status_code == 200
    assert (
        await c.get("/api/ping", headers={"X-Forwarded-For": "fe80::1%wlan7"})
    ).status_code == 429


async def test_garbage_forwarded_entry_falls_back_to_the_peer(make_client: Factory) -> None:
    s = make_settings(rate_limit_api_per_min=1, trusted_proxies="172.18.0.0/16")
    c = make_client(s, peer="172.18.0.5")
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "unbekannt"})).status_code == 200
    assert (await c.get("/api/ping", headers={"X-Forwarded-For": "auch-nicht"})).status_code == 429


# --- echte App --------------------------------------------------------------------------------


async def test_real_app_sends_headers(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert_security_headers(r)
    r = await client.get("/api/config")
    assert_security_headers(r)
    r = await client.post("/api/health", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# --- Request-Body-Obergrenze -------------------------------------------------------------------


async def test_oversized_content_length_is_rejected_with_413(make_client: Factory) -> None:
    from app.middleware.security import MAX_BODY_BYTES

    c = make_client(make_settings(rate_limit_enabled=False))
    r = await c.post("/api/write", content=b"x" * (MAX_BODY_BYTES + 1))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert_security_headers(r)
    ok = await c.post("/api/write", content=b"x" * MAX_BODY_BYTES)
    assert ok.status_code == 200


async def test_lying_or_missing_content_length_cannot_dodge_the_body_limit() -> None:
    """Gestreamter Body (Transfer-Encoding: chunked, kein Content-Length) wird mitgezählt –
    auch wenn die Route den Body selbst und vollständig liest (FastAPI/Pydantic)."""
    from app.middleware.security import MAX_BODY_BYTES

    app = FastAPI()
    install_error_handlers(app)
    install_security(app)
    app.dependency_overrides[get_settings] = lambda: make_settings(rate_limit_enabled=False)
    seen: list[int] = []

    @app.post("/api/auth/probe")
    async def probe(body: ProbeBody) -> dict[str, int]:
        seen.append(len(body.email))
        return {"n": len(body.email)}

    chunk = b"a" * 4096
    sent = 0

    async def stream() -> AsyncIterator[bytes]:
        nonlocal sent
        yield b'{"email": "'
        for _ in range(200):  # 800 KiB
            sent += len(chunk)
            yield chunk
        yield b'"}'

    transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/auth/probe", content=stream(), headers={"Content-Type": "application/json"}
        )
        small = await c.post("/api/auth/probe", json={"email": "a" * 100})
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "payload_too_large"
    assert seen == [100]  # die Route wurde für den großen Body nie ausgeführt
    assert sent <= MAX_BODY_BYTES + 2 * len(chunk)  # nicht den ganzen Body gelesen
    assert small.status_code == 200


# --- Hinweis auf fehlende TRUSTED_PROXIES ---------------------------------------------------------


async def test_warns_once_when_a_private_peer_forwards_but_no_proxy_is_trusted(
    make_client: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    import app.middleware.security as sec

    sec._proxy_warning_emitted = False
    c = make_client(make_settings(trusted_proxies=""), peer="172.18.0.5")
    with caplog.at_level(logging.WARNING, logger="app.security"):
        for ip in ("203.0.113.1", "203.0.113.2"):
            await c.get("/api/ping", headers={"X-Forwarded-For": ip})
    warnings = [r for r in caplog.records if r.name == "app.security"]
    assert len(warnings) == 1
    assert "TRUSTED_PROXIES" in warnings[0].getMessage()
    for needle in ("172.18.0.5", "203.0.113"):
        assert needle not in warnings[0].getMessage()


@pytest.mark.parametrize(
    ("peer", "xff", "trusted"),
    [
        ("198.51.100.10", "1.2.3.4", ""),  # öffentlicher Peer: Fälschung eines Clients, kein Proxy
        ("172.18.0.5", None, ""),  # kein Forwarded-Header
        ("172.18.0.5", "203.0.113.1", "172.18.0.0/16"),  # korrekt konfiguriert
    ],
)
async def test_no_proxy_warning_in_unremarkable_setups(
    make_client: Factory,
    caplog: pytest.LogCaptureFixture,
    peer: str,
    xff: str | None,
    trusted: str,
) -> None:
    import app.middleware.security as sec

    sec._proxy_warning_emitted = False
    c = make_client(make_settings(trusted_proxies=trusted), peer=peer)
    with caplog.at_level(logging.WARNING, logger="app.security"):
        await c.get("/api/ping", headers={"X-Forwarded-For": xff} if xff else {})
    assert not [r for r in caplog.records if r.name == "app.security"]
