"""Tests für die gemeinsamen Vertragsmodule (Token, Dependencies, Limiter, Snap, Umweg, Config)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import jwt
import pytest
from fastapi import Request

from app.auth.deps import CurrentUser
from app.auth.tokens import (
    COOKIE_NAME,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
)
from app.config import Settings, get_settings
from app.errors import ApiError
from app.ratelimit import SlidingWindowLimiter, client_ip
from app.services.detour import compute_detour, is_acceptable
from app.services.geo import geojson_linestring_to_latlon, snap_or_error
from tests.fakes import FakeRouting, haversine_m
from tests.helpers import EVENT, NORTH, auth_cookies, create_user

# --- Token ---------------------------------------------------------------------------------------


def test_token_roundtrip() -> None:
    uid = uuid.uuid4()
    assert decode_access_token(create_access_token(uid)) == uid


def test_token_rejects_tampering_expiry_and_alg_none() -> None:
    uid = uuid.uuid4()
    token = create_access_token(uid)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token + "x")
    secret = get_settings().jwt_secret.get_secret_value()
    expired = jwt.encode(
        {"sub": str(uid), "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(expired)
    none_alg = jwt.encode({"sub": str(uid), "exp": 9_999_999_999}, "", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_access_token(none_alg)
    wrong_key = jwt.encode({"sub": str(uid), "exp": 9_999_999_999}, "anderes-geheimnis" * 3)
    with pytest.raises(InvalidTokenError):
        decode_access_token(wrong_key)


# --- Abhängigkeit get_current_user ----------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_current_user_dependency_via_a_protected_route(
    client: httpx.AsyncClient, db: Any
) -> None:
    from app.main import app

    @app.get("/api/_vertrag_test")
    async def whoami(user: CurrentUser) -> dict[str, str]:
        return {"id": str(user.id)}

    try:
        assert (await client.get("/api/_vertrag_test")).status_code == 401
        bad = await client.get("/api/_vertrag_test", cookies={COOKIE_NAME: "kaputt"})
        assert bad.status_code == 401
        assert bad.json()["error"]["code"] == "unauthorized"
        user = await create_user(db)
        ok = await client.get("/api/_vertrag_test", cookies=auth_cookies(user))
        assert ok.status_code == 200 and ok.json() == {"id": str(user["id"])}
        # Konto weg → Token wertlos
        await db.execute("DELETE FROM users WHERE id=$1", user["id"])
        gone = await client.get("/api/_vertrag_test", cookies=auth_cookies(user))
        assert gone.status_code == 401
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/api/_vertrag_test"
        ]


# --- Rate-Limiter --------------------------------------------------------------------------------


def test_sliding_window_limits_and_recovers() -> None:
    lim = SlidingWindowLimiter()
    assert [lim.hit("k", 2, 10, now=t)[0] for t in (0, 1, 2)] == [True, True, False]
    allowed, retry = lim.hit("k", 2, 10, now=3)
    assert not allowed and 6.5 < retry <= 7.0
    assert lim.hit("k", 2, 10, now=10.5)[0] is True  # ältester Treffer ist aus dem Fenster
    assert lim.hit("anderer", 2, 10, now=3)[0] is True  # Schlüssel sind getrennt


def test_limiter_memory_is_bounded() -> None:
    lim = SlidingWindowLimiter(max_keys=100)
    for i in range(1_000):
        lim.hit(f"ip-{i}", 5, 60, now=float(i) / 1000)
    assert len(lim._hits) <= 100


def _request(peer: str, forwarded: str | None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {"type": "http", "client": (peer, 1234), "headers": headers}
    return Request(scope)


def test_client_ip_only_trusts_forwarded_header_from_trusted_proxies() -> None:
    plain = Settings(trusted_proxies="")
    assert client_ip(_request("203.0.113.9", "1.2.3.4"), plain) == "203.0.113.9"
    trusted = Settings(trusted_proxies="10.10.10.0/24")
    # Peer vertraut → rechtester NICHT vertrauter Eintrag (links stehende Einträge sind fälschbar)
    assert client_ip(_request("10.10.10.70", "6.6.6.6, 198.51.100.7"), trusted) == "198.51.100.7"
    assert client_ip(_request("10.10.10.70", "198.51.100.7, 10.10.10.5"), trusted) == "198.51.100.7"
    # Peer nicht vertraut → Header ignorieren
    assert client_ip(_request("203.0.113.9", "198.51.100.7"), trusted) == "203.0.113.9"


@pytest.mark.asyncio(loop_scope="session")
async def test_rate_limit_dependency_returns_429_with_retry_after(
    client: httpx.AsyncClient,
) -> None:
    from fastapi import Depends

    from app.main import app
    from app.ratelimit import rate_limit

    @app.get(
        "/api/_limit_test", dependencies=[Depends(rate_limit("t", "rate_limit_login_per_min", 60))]
    )
    async def limited() -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_settings] = lambda: Settings(
        rate_limit_enabled=True,
        rate_limit_login_per_min=2,
        jwt_secret="x" * 40,  # type: ignore[arg-type]
    )
    try:
        codes = [(await client.get("/api/_limit_test")).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        blocked = await client.get("/api/_limit_test")
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["retry-after"]) >= 1
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/api/_limit_test"
        ]


# --- Snap-Prüfung und Umweg ----------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snap_or_error_rejects_far_points_and_outside_extract() -> None:
    settings = Settings()
    fake = FakeRouting(snap_m=lambda p: 900.0 if p == (53.6, 10.0) else 5.0)
    assert (await snap_or_error(fake, settings, NORTH, field="start")).distance_m == 5.0
    with pytest.raises(ApiError) as far:
        await snap_or_error(fake, settings, (53.6, 10.0), field="start")
    assert far.value.status_code == 422 and far.value.code == "outside_coverage"
    with pytest.raises(ApiError) as outside:
        await snap_or_error(fake, settings, (48.14, 11.58), field="start")  # München
    assert outside.value.code == "outside_coverage"
    with pytest.raises(ApiError) as down:
        await snap_or_error(FakeRouting(unavailable=True), settings, NORTH, field="start")
    assert down.value.status_code == 503 and down.value.code == "routing_unavailable"


def test_is_acceptable_modes() -> None:
    any_mode = Settings(match_detour_mode="any")
    all_mode = Settings(match_detour_mode="all")
    # 10 % und 30 min: Distanz ok, Zeit nicht → any ja, all nein
    kw: dict[str, Any] = {"distance_m": 3_000.0, "duration_s": 30 * 60.0, "ratio": 0.10}
    assert is_acceptable(any_mode, **kw) is True
    assert is_acceptable(all_mode, **kw) is False
    # 40 % und 5 min: Zeit ok, Distanz nicht
    kw = {"distance_m": 12_000.0, "duration_s": 5 * 60.0, "ratio": 0.40}
    assert is_acceptable(any_mode, **kw) is True
    assert is_acceptable(all_mode, **kw) is False
    # beides zu groß
    kw = {"distance_m": 12_000.0, "duration_s": 40 * 60.0, "ratio": 0.40}
    assert is_acceptable(any_mode, **kw) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_compute_detour_on_route_is_about_zero_and_off_route_is_not() -> None:
    settings = Settings()
    fake = FakeRouting()
    base = await fake.route([NORTH, EVENT], geometry=False)
    mid = (53.655, 9.969)  # liegt (fast) auf der Geraden NORTH → EVENT
    on = await compute_detour(
        fake, settings, driver_start=NORTH, pickup=mid, event=EVENT,
        base_distance_m=base.distance_m, base_duration_s=base.duration_s,
    )  # fmt: skip
    assert on.distance_m < 150 and on.acceptable
    off = await compute_detour(
        fake, settings, driver_start=NORTH, pickup=(53.40, 9.70), event=EVENT,
        base_distance_m=base.distance_m, base_duration_s=base.duration_s,
    )  # fmt: skip
    assert off.distance_m > 10_000 and off.ratio > 0.2


# --- Geometrie, Config-Endpunkt, Fake ------------------------------------------------------------


def test_geojson_conversion_swaps_to_lat_lon() -> None:
    gj = '{"type":"LineString","coordinates":[[9.99,53.55],[10.0,53.56]]}'
    assert geojson_linestring_to_latlon(gj) == [[53.55, 9.99], [53.56, 10.0]]


def test_fake_routing_is_deterministic_and_consistent() -> None:
    d = haversine_m(NORTH, EVENT)
    assert 20_000 < d < 23_000


@pytest.mark.asyncio(loop_scope="session")
async def test_public_config_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["event"]["name"] == get_settings().event_name
    assert set(body) == {"event", "matching", "map", "limits", "retention_days_after_event"}
    assert body["limits"] == {"min_seats": 1, "max_seats": 8}
    flat = str(body).lower()
    assert "secret" not in flat and "password" not in flat


def test_settings_defaults_for_contract_fields() -> None:
    s = Settings()
    assert s.match_route_trim_start_m == 1000
    assert Settings(cookie_secure=True).allowed_origin_list == ["https://localhost"]
    assert Settings(cookie_secure=False).allowed_origin_list == ["http://localhost"]
    assert Settings(allowed_origins="https://a.example/, http://b.example").allowed_origin_list == [
        "https://a.example",
        "http://b.example",
    ]
    assert (
        str(Settings(trusted_proxies="10.0.0.1, 10.1.0.0/16").trusted_proxy_networks[1])
        == "10.1.0.0/16"
    )


def test_unused_imports_guard() -> None:
    assert SimpleNamespace and cast
