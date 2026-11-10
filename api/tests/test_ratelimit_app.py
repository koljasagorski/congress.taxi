"""Rate-Limits über die echte App (Router, Middleware, DB) – nicht nur als Einheit.

Alle ASGI-Anfragen haben denselben Peer (127.0.0.1). Um verschiedene Clients zu simulieren, ist
``trusted_proxies`` auf diesen Peer gesetzt und der Test schickt unterschiedliche
``X-Forwarded-For``-Werte – genau wie hinter Caddy. Grenzen werden pro Test klein gesetzt; alle
übrigen Limits stehen hoch, damit jeweils nur das geprüfte greift.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.ratelimit import limiter
from tests.helpers import create_user, future, insert_ride
from tests.test_match_support import MID_NORTH_EVENT, login, override_settings
from tests.test_requests_common import PICKUP, pickup_json
from tests.test_rides_common import RIDES, create_body

pytestmark = pytest.mark.asyncio(loop_scope="session")

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
PASSWORD = "korrekt-pferd-batterie"
HIGH = 10_000


def limits(**small: int) -> Any:
    """Alle Limits hoch, nur die genannten klein; Peer 127.0.0.1 ist vertrauter Proxy."""
    base: dict[str, Any] = {
        "rate_limit_enabled": True,
        "trusted_proxies": "127.0.0.1",
        "rate_limit_api_per_min": HIGH,
        "rate_limit_login_per_min": HIGH,
        "rate_limit_login_fail_per_email_ip_per_min": HIGH,
        "rate_limit_login_fail_per_email_per_min": HIGH,
        "rate_limit_register_per_hour": HIGH,
        "rate_limit_match_per_min": HIGH,
        "rate_limit_match_user_per_min": HIGH,
        "rate_limit_rides_create_per_hour": HIGH,
        "rate_limit_requests_create_per_hour": HIGH,
    }
    base.update(small)
    return override_settings(**base)


def xff(ip: str) -> dict[str, str]:
    return {"X-Forwarded-For": ip}


def as_user(client: httpx.AsyncClient, user: dict[str, Any]) -> None:
    login(client, user)


def assert_limited(r: httpx.Response) -> None:
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1


# --- Anmeldung / Registrierung / allgemeines Limit ---------------------------------------------


async def test_login_is_limited_per_ip(client: httpx.AsyncClient) -> None:
    with limits(rate_limit_login_per_min=3):
        codes = []
        for i in range(4):
            r = await client.post(
                "/api/auth/login",
                json={"email": f"nobody{i}@example.org", "password": PASSWORD},
                headers=xff("198.51.100.1"),
            )
            codes.append(r.status_code)
        assert codes == [401, 401, 401, 429]
        assert_limited(r)
        # eine andere Adresse ist nicht betroffen
        other = await client.post(
            "/api/auth/login",
            json={"email": "nobody9@example.org", "password": PASSWORD},
            headers=xff("198.51.100.2"),
        )
        assert other.status_code == 401


# Login je E-Mail: Tests in test_ratelimit_login_lockout.py


async def test_register_is_limited_per_ip(client: httpx.AsyncClient) -> None:
    with limits(rate_limit_register_per_hour=2):
        codes = []
        for i in range(3):
            r = await client.post(
                "/api/auth/register",
                json={
                    "email": f"neu{i}@example.org",
                    "password": PASSWORD,
                    "display_name": "Neu",
                    "consent": True,
                },
                headers=xff("198.51.100.30"),
            )
            codes.append(r.status_code)
        assert codes == [202, 202, 429]
        assert_limited(r)


async def test_general_api_limit_counts_per_client_but_not_the_liveness(
    client: httpx.AsyncClient,
) -> None:
    with limits(rate_limit_api_per_min=3):
        codes = [
            (await client.get("/api/config", headers=xff("198.51.100.40"))).status_code
            for _ in range(4)
        ]
        assert codes == [200, 200, 200, 429]
        blocked = await client.get("/api/config", headers=xff("198.51.100.40"))
        assert_limited(blocked)
        assert "no-store" in blocked.headers["cache-control"]  # Sicherheits-Header auch am 429
        assert (await client.get("/api/config", headers=xff("198.51.100.41"))).status_code == 200
        for _ in range(10):  # Liveness (Docker-Healthcheck) ist ausgenommen
            assert (
                await client.get("/api/health", headers=xff("198.51.100.40"))
            ).status_code == 200


async def test_ipv6_addresses_of_one_prefix_share_a_limit(client: httpx.AsyncClient) -> None:
    with limits(rate_limit_api_per_min=2):
        a = await client.get("/api/config", headers=xff("2001:db8:1:2::1"))
        b = await client.get("/api/config", headers=xff("2001:db8:1:2:aaaa:bbbb:cccc:dddd"))
        c = await client.get("/api/config", headers=xff("2001:db8:1:2::ffff"))
        assert (a.status_code, b.status_code) == (200, 200)
        assert_limited(c)
        # ein anderes /64 ist ein anderer Client
        d = await client.get("/api/config", headers=xff("2001:db8:1:3::1"))
        assert d.status_code == 200


async def test_active_counter_survives_a_flood_of_new_clients(client: httpx.AsyncClient) -> None:
    table = limiter.get("mw-api")
    original = table._max_keys
    table._max_keys = 4
    try:
        with limits(rate_limit_api_per_min=2):
            for _ in range(2):
                assert (
                    await client.get("/api/config", headers=xff("198.51.100.50"))
                ).status_code == 200
            assert_limited(await client.get("/api/config", headers=xff("198.51.100.50")))
            for i in range(40):  # Flut neuer Adressen füllt die kleine Tabelle
                await client.get("/api/config", headers=xff(f"203.0.113.{i + 1}"))
            assert len(table._hits) <= 4
            # der gesperrte Client bleibt gesperrt (sein Zähler wurde nicht verdrängt)
            assert_limited(await client.get("/api/config", headers=xff("198.51.100.50")))
    finally:
        table._max_keys = original
        limiter.reset()


# --- Limits je Nutzer -----------------------------------------------------------------------------


async def test_match_is_limited_per_user_not_only_per_ip(
    client: httpx.AsyncClient, db: Any
) -> None:
    alice = await create_user(db)
    bob = await create_user(db)
    params = {"lat": MID_NORTH_EVENT[0], "lon": MID_NORTH_EVENT[1]}
    with limits(rate_limit_match_user_per_min=2):
        as_user(client, alice)
        for i in range(2):
            r = await client.get("/api/match", params=params, headers=xff(f"198.51.100.{60 + i}"))
            assert r.status_code == 200, r.text
        # Alice wechselt die Adresse: ihr Nutzer-Limit gilt trotzdem
        assert_limited(await client.get("/api/match", params=params, headers=xff("198.51.100.77")))
        # Bob (andere Adresse) hat einen eigenen Zähler
        as_user(client, bob)
        ok = await client.get("/api/match", params=params, headers=xff("198.51.100.78"))
        assert ok.status_code == 200


async def test_match_ip_limit_still_applies_across_users(
    client: httpx.AsyncClient, db: Any
) -> None:
    users = [await create_user(db) for _ in range(3)]
    params = {"lat": MID_NORTH_EVENT[0], "lon": MID_NORTH_EVENT[1]}
    with limits(rate_limit_match_per_min=2):
        codes = []
        for u in users:  # drei verschiedene Nutzer, dieselbe Adresse
            as_user(client, u)
            r = await client.get("/api/match", params=params, headers=xff("198.51.100.80"))
            codes.append(r.status_code)
        assert codes == [200, 200, 429]
        assert_limited(r)


async def test_anonymous_requests_get_401_not_429_on_user_limited_routes(
    client: httpx.AsyncClient,
) -> None:
    with limits(
        rate_limit_match_user_per_min=1,
        rate_limit_rides_create_per_hour=1,
        rate_limit_requests_create_per_hour=1,
    ):
        client.cookies.clear()
        for _ in range(3):
            assert (
                await client.get("/api/match", params={"lat": 53.6, "lon": 9.9})
            ).status_code == 401
            assert (await client.post(RIDES, json={})).status_code == 401
            r = await client.post(f"/api/rides/{uuid.uuid4()}/requests", json=pickup_json())
            assert r.status_code == 401


async def test_creating_rides_is_limited_per_user(client: httpx.AsyncClient, db: Any) -> None:
    alice = await create_user(db)
    bob = await create_user(db)
    body = create_body(future(24).isoformat())
    with limits(rate_limit_rides_create_per_hour=2):
        as_user(client, alice)
        codes = [
            (await client.post(RIDES, json=body, headers=xff(f"198.51.100.{90 + i}"))).status_code
            for i in range(2)
        ]
        assert codes == [201, 201]
        blocked = await client.post(RIDES, json=body, headers=xff("198.51.100.99"))
        assert_limited(blocked)  # andere Adresse, derselbe Nutzer
        assert (
            await db.fetchval("SELECT count(*) FROM rides WHERE driver_id = $1", alice["id"]) == 2
        )
        # Lesen und Ändern bleiben möglich; Bob hat einen eigenen Zähler
        assert (await client.get(f"{RIDES}/mine")).status_code == 200
        as_user(client, bob)
        assert (
            await client.post(RIDES, json=body, headers=xff("198.51.100.99"))
        ).status_code == 201


async def test_sending_requests_is_limited_per_user(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    other = await create_user(db)
    rides = [await insert_ride(db, driver["id"]) for _ in range(3)]
    with limits(rate_limit_requests_create_per_hour=2):
        as_user(client, rider)
        codes = []
        for i, ride_id in enumerate(rides[:2]):
            r = await client.post(
                f"/api/rides/{ride_id}/requests",
                json=pickup_json(PICKUP),
                headers=xff(f"198.51.100.{100 + i}"),
            )
            codes.append(r.status_code)
        assert codes == [201, 201]
        blocked = await client.post(
            f"/api/rides/{rides[2]}/requests",
            json=pickup_json(PICKUP),
            headers=xff("198.51.100.120"),
        )
        assert_limited(blocked)
        assert (
            await db.fetchval("SELECT count(*) FROM ride_requests WHERE rider_id = $1", rider["id"])
            == 2
        )
        as_user(client, other)
        ok = await client.post(
            f"/api/rides/{rides[2]}/requests",
            json=pickup_json(PICKUP),
            headers=xff("198.51.100.121"),
        )
        assert ok.status_code == 201


async def test_disabled_rate_limit_never_blocks_user_routes(
    client: httpx.AsyncClient, db: Any
) -> None:
    alice = await create_user(db)
    body = create_body(future(24).isoformat())
    with limits(rate_limit_enabled=False, rate_limit_rides_create_per_hour=1):
        as_user(client, alice)
        for _ in range(3):
            assert (await client.post(RIDES, json=body)).status_code == 201


async def test_settings_defaults_and_env_example_list_the_new_limits() -> None:
    fields = Settings.model_fields
    assert fields["rate_limit_login_fail_per_email_ip_per_min"].default == 5
    assert fields["rate_limit_login_fail_per_email_per_min"].default == 30
    # global deutlich höher als je E-Mail+IP (sonst sperrt ein Angreifer das Opfer wieder aus)
    assert (
        fields["rate_limit_login_fail_per_email_per_min"].default
        >= 5 * fields["rate_limit_login_fail_per_email_ip_per_min"].default
    )
    assert fields["rate_limit_match_user_per_min"].default == 20
    assert fields["rate_limit_rides_create_per_hour"].default == 20
    assert fields["rate_limit_requests_create_per_hour"].default == 30
    example = await asyncio.to_thread(ENV_EXAMPLE.read_text)
    for name in (
        "RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_IP_PER_MIN",
        "RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_PER_MIN",
        "RATE_LIMIT_MATCH_USER_PER_MIN",
        "RATE_LIMIT_RIDES_CREATE_PER_HOUR",
        "RATE_LIMIT_REQUESTS_CREATE_PER_HOUR",
    ):
        assert f"{name}=" in example
