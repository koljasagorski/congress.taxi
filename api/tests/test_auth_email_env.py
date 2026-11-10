"""E-Mail-Validierung je Umgebung (strikt in Produktion) und Randfälle beim Login."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.config import get_settings
from tests.helpers import create_user

PASSWORD = "korrekt-pferd-batterie"


def register_body(email: str) -> dict[str, Any]:
    return {"email": email, "password": PASSWORD, "display_name": "Demo", "consent": True}


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schaltet nur die Umgebung der E-Mail-Prüfung auf production (Rest der App unverändert)."""
    prod = get_settings().model_copy(update={"app_env": "production"})
    monkeypatch.setattr("app.schemas.auth.get_settings", lambda: prod)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "email", ["seed@demo.invalid", "Alice.Demo@DEMO.Invalid", "x@dev.local", "y@a.b.test"]
)
async def test_non_production_allows_special_use_domains_and_login_works(
    client: httpx.AsyncClient, db: Any, email: str
) -> None:
    assert get_settings().app_env != "production"
    r = await client.post("/api/auth/register", json=register_body(email))
    assert r.status_code == 202
    stored = await db.fetchval("SELECT email FROM users")
    assert stored == email.strip().lower()
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "email",
    [
        "no-at.demo.invalid",
        "a b@demo.invalid",  # Leerzeichen im Local-Part
        "@demo.invalid",
        "a@-x.invalid",
        "a@demo.invalid.",  # Punkt am Ende
        "a@dömo.invalid",  # keine Umlaute in der Ausnahme
        "a@invalid",  # ohne Punkt
        "a\x00@demo.invalid",
    ],
)
async def test_non_production_special_use_exception_stays_strict_otherwise(
    client: httpx.AsyncClient, db: Any, email: str
) -> None:
    r = await client.post("/api/auth/register", json=register_body(email))
    assert r.status_code == 422, email
    assert email.strip() not in r.text
    assert await db.fetchval("SELECT count(*) FROM users") == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "email", ["seed@demo.invalid", "x@dev.local", "y@a.b.test", "z@x.localhost"]
)
async def test_production_rejects_special_use_domains(
    client: httpx.AsyncClient, db: Any, production: None, email: str
) -> None:
    r = await client.post("/api/auth/register", json=register_body(email))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert email not in r.text
    assert await db.fetchval("SELECT count(*) FROM users") == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_production_accepts_regular_addresses_and_login_for_reserved_is_401(
    client: httpx.AsyncClient, db: Any, production: None
) -> None:
    ok = await client.post("/api/auth/register", json=register_body("Echt@Beispiel.example.org"))
    assert ok.status_code == 202
    assert await db.fetchval("SELECT email FROM users") == "echt@beispiel.example.org"
    # Ein (z. B. vor der Umstellung angelegtes) reserviertes Konto: Login bleibt sauber bei 401.
    await create_user(db, email="alt@demo.invalid")
    r = await client.post(
        "/api/auth/login", json={"email": "alt@demo.invalid", "password": PASSWORD}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "email",
    [
        "jörg@bücher.example",  # Unicode in Local-Part und Domain, unbekanntes Konto
        "😀@example.org",
        "a@ＥＸＡＭＰＬＥ.org",
        "‮@example.org",
        "a\x00@example.org",
        "\x00",
        "a@exa\x00mple.org",
        " \x00 ",
    ],
)
@pytest.mark.parametrize("password", [PASSWORD, "pa\x00ss", "pässwörd-日本語"])
async def test_login_with_odd_unicode_or_nul_input_is_401_never_500(
    client: httpx.AsyncClient, db: Any, email: str, password: str
) -> None:
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401, (email, password)
    assert r.json()["error"]["code"] == "invalid_credentials"
