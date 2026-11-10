"""POST /api/auth/login und /api/auth/logout."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from argon2 import PasswordHasher as Argon2

from app.auth.passwords import Argon2PasswordHasher
from app.auth.tokens import COOKIE_NAME, decode_access_token
from app.config import Settings, get_settings
from tests.helpers import create_user

PASSWORD = "korrekt-pferd-batterie"
WRONG = "falsch-falsch-falsch"


async def make_user(db: Any, email: str = "fahrer@example.org", **kw: Any) -> dict[str, Any]:
    return await create_user(
        db, email=email, password_hash=Argon2PasswordHasher().hash(PASSWORD), **kw
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_login_success_sets_cookie_and_returns_user(
    client: httpx.AsyncClient, db: Any
) -> None:
    user = await make_user(db, display_name="Zoë Ünal")
    r = await client.post(
        "/api/auth/login", json={"email": "  FAHRER@example.org ", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert r.json() == {"user": {"id": str(user["id"]), "display_name": "Zoë Ünal"}}
    cookie = r.headers["set-cookie"]
    low = cookie.lower()
    assert cookie.startswith(f"{COOKIE_NAME}=")
    assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    assert "; secure" not in low  # Tests laufen mit COOKIE_SECURE=false
    assert decode_access_token(client.cookies[COOKIE_NAME]) == user["id"]
    assert (await client.get("/api/me")).status_code == 200


@pytest.mark.asyncio(loop_scope="session")
async def test_login_cookie_is_secure_when_configured(client: httpx.AsyncClient, db: Any) -> None:
    from app.main import app

    await make_user(db)
    app.dependency_overrides[get_settings] = lambda: Settings(
        cookie_secure=True,
        jwt_secret="x" * 40,  # type: ignore[arg-type]
    )
    r = await client.post(
        "/api/auth/login", json={"email": "fahrer@example.org", "password": PASSWORD}
    )
    assert r.status_code == 200
    assert "; secure" in r.headers["set-cookie"].lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_login_failures_are_identical_for_unknown_email_and_wrong_password(
    client: httpx.AsyncClient, db: Any
) -> None:
    await make_user(db)
    wrong = await client.post(
        "/api/auth/login", json={"email": "fahrer@example.org", "password": WRONG}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "niemand@example.org", "password": WRONG}
    )
    for r in (wrong, unknown):
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"
    assert wrong.content == unknown.content
    assert dict(wrong.headers) == dict(unknown.headers)
    assert "set-cookie" not in wrong.headers and "set-cookie" not in unknown.headers


@pytest.mark.asyncio(loop_scope="session")
async def test_login_verifies_a_hash_even_for_unknown_email(
    client: httpx.AsyncClient,
) -> None:
    """Zeitangleichung über Dummy-Hash: verify läuft auch ohne Konto."""
    from app.api.auth import get_password_hasher
    from app.main import app

    seen: list[str] = []

    class Spy(Argon2PasswordHasher):
        def verify(self, password_hash: str, password: str) -> bool:
            seen.append(password_hash)
            return super().verify(password_hash, password)

    app.dependency_overrides[get_password_hasher] = lambda: Spy()
    await client.post(
        "/api/auth/login", json={"email": "keiner@example.org", "password": "irgendwas"}
    )
    assert len(seen) == 1 and seen[0].startswith("$argon2id$")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "body",
    [
        {"email": "a@example.org"},
        {"password": "x"},
        {},
        {"email": "a@example.org", "password": ""},
        {"email": "", "password": "x"},
        {"email": "a@example.org", "password": "x" * 129},
        {"email": "a" * 400, "password": "x"},
        {"email": 5, "password": "x"},
    ],
)
async def test_login_malformed_body_is_422(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    r = await client.post("/api/auth/login", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio(loop_scope="session")
async def test_login_with_account_without_usable_hash_is_401(
    client: httpx.AsyncClient, db: Any
) -> None:
    await create_user(db, email="kaputt@example.org", password_hash="!")
    r = await client.post(
        "/api/auth/login", json={"email": "kaputt@example.org", "password": "irgendwas-langes"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio(loop_scope="session")
async def test_login_rehashes_outdated_hash(client: httpx.AsyncClient, db: Any) -> None:
    weak = Argon2(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    user = await create_user(db, email="alt@example.org", password_hash=weak)
    r = await client.post(
        "/api/auth/login", json={"email": "alt@example.org", "password": PASSWORD}
    )
    assert r.status_code == 200
    new_hash = await db.fetchval("SELECT password_hash FROM users WHERE id=$1", user["id"])
    assert new_hash != weak and new_hash.startswith("$argon2id$")
    assert Argon2PasswordHasher().needs_rehash(new_hash) is False
    again = await client.post(
        "/api/auth/login", json={"email": "alt@example.org", "password": PASSWORD}
    )
    assert again.status_code == 200
    assert await db.fetchval("SELECT password_hash FROM users WHERE id=$1", user["id"]) == new_hash


@pytest.mark.asyncio(loop_scope="session")
async def test_wrong_password_does_not_rehash(client: httpx.AsyncClient, db: Any) -> None:
    weak = Argon2(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    user = await create_user(db, email="alt@example.org", password_hash=weak)
    r = await client.post("/api/auth/login", json={"email": "alt@example.org", "password": WRONG})
    assert r.status_code == 401
    assert await db.fetchval("SELECT password_hash FROM users WHERE id=$1", user["id"]) == weak


@pytest.mark.asyncio(loop_scope="session")
async def test_register_then_login_roundtrip(client: httpx.AsyncClient) -> None:
    reg = await client.post(
        "/api/auth/register",
        json={
            "email": "Rundlauf@Example.org",
            "password": PASSWORD,
            "display_name": "Rundlauf",
            "consent": True,
        },
    )
    assert reg.status_code == 202
    r = await client.post(
        "/api/auth/login", json={"email": "rundlauf@example.org", "password": PASSWORD}
    )
    assert r.status_code == 200 and r.json()["user"]["display_name"] == "Rundlauf"


@pytest.mark.asyncio(loop_scope="session")
async def test_logout_clears_cookie_and_is_idempotent_without_login(
    client: httpx.AsyncClient, db: Any
) -> None:
    anon = await client.post("/api/auth/logout")
    assert anon.status_code == 204 and anon.content == b""
    await make_user(db)
    await client.post("/api/auth/login", json={"email": "fahrer@example.org", "password": PASSWORD})
    assert COOKIE_NAME in client.cookies
    r = await client.post("/api/auth/logout")
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=" in cookie and ("max-age=0" in cookie or "expires=" in cookie)
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert COOKIE_NAME not in client.cookies
    assert (await client.get("/api/me")).status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_login_logs_contain_neither_email_nor_password(
    client: httpx.AsyncClient, db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await make_user(db, email="log.test@example.org")
    await client.post(
        "/api/auth/login", json={"email": "log.test@example.org", "password": PASSWORD}
    )
    await client.post("/api/auth/login", json={"email": "log.test@example.org", "password": WRONG})
    await client.post(
        "/api/auth/login", json={"email": "unbekannt.test@example.org", "password": WRONG}
    )
    for secret in ("log.test", "unbekannt.test", PASSWORD, "falsch-falsch", "access_token="):
        assert secret not in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_login_rate_limit_returns_429_with_retry_after(
    client: httpx.AsyncClient, db: Any
) -> None:
    from app.main import app

    await make_user(db)
    app.dependency_overrides[get_settings] = lambda: Settings(
        rate_limit_enabled=True,
        rate_limit_login_per_min=3,
        cookie_secure=False,
        jwt_secret="x" * 40,  # type: ignore[arg-type]
    )
    bad = {"email": "fahrer@example.org", "password": WRONG}
    codes = [(await client.post("/api/auth/login", json=bad)).status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    blocked = await client.post(
        "/api/auth/login", json={"email": "fahrer@example.org", "password": PASSWORD}
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1
    assert "set-cookie" not in blocked.headers


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "raw",
    [
        "é@example.org",  # NFD: e + kombinierender Akut
        "u1@ＥＸＡＭＰＬＥ.org",  # Volle-Breite-Domain
        "  Jörg@Example.ORG ",
    ],
)
async def test_login_works_with_same_input_as_register(
    client: httpx.AsyncClient, db: Any, raw: str
) -> None:
    reg = await client.post(
        "/api/auth/register",
        json={"email": raw, "password": PASSWORD, "display_name": "Uni", "consent": True},
    )
    assert reg.status_code == 202
    r = await client.post("/api/auth/login", json={"email": raw, "password": PASSWORD})
    assert r.status_code == 200, raw


@pytest.mark.asyncio(loop_scope="session")
async def test_login_with_nul_byte_email_is_401_not_500(client: httpx.AsyncClient, db: Any) -> None:
    r = await client.post(
        "/api/auth/login", json={"email": "a\x00@example.org", "password": PASSWORD}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"
