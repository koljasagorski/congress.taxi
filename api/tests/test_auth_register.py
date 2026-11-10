"""POST /api/auth/register (Vertrag: Abschnitt Authentifizierung)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from app.auth.passwords import Argon2PasswordHasher
from app.config import Settings, get_settings
from tests.helpers import create_user

PASSWORD = "korrekt-pferd-batterie"


def payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": "Neu.Person@Example.org",
        "password": PASSWORD,
        "display_name": "Neue Person",
        "consent": True,
    }
    body.update(overrides)
    return body


async def user_rows(db: Any) -> list[Any]:
    return list(await db.fetch("SELECT * FROM users ORDER BY created_at"))


@pytest.mark.asyncio(loop_scope="session")
async def test_register_creates_account_with_normalized_email_and_argon2id(
    client: httpx.AsyncClient, db: Any
) -> None:
    r = await client.post("/api/auth/register", json=payload(email="  Neu.Person@Example.ORG "))
    assert r.status_code == 202
    assert "set-cookie" not in r.headers  # kein Auto-Login
    rows = await user_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["email"] == "neu.person@example.org"
    assert row["display_name"] == "Neue Person"
    assert row["consent_at"] is not None
    assert row["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in row["password_hash"]


@pytest.mark.asyncio(loop_scope="session")
async def test_register_response_identical_for_new_and_known_email(
    client: httpx.AsyncClient, db: Any
) -> None:
    first = await client.post("/api/auth/register", json=payload())
    again = await client.post(
        "/api/auth/register",
        json=payload(email=" NEU.PERSON@example.org", password="ganz-anderes-passwort"),
    )
    assert first.status_code == again.status_code == 202
    assert first.content == again.content
    assert first.json() == again.json()
    assert dict(first.headers) == dict(again.headers)
    rows = await user_rows(db)
    assert len(rows) == 1  # nichts geändert
    assert Argon2PasswordHasher().verify(rows[0]["password_hash"], PASSWORD) is True
    assert rows[0]["display_name"] == "Neue Person"


@pytest.mark.asyncio(loop_scope="session")
async def test_register_known_email_of_existing_user_is_untouched(
    client: httpx.AsyncClient, db: Any
) -> None:
    existing = await create_user(db, email="da@example.org", display_name="Vorhanden")
    r = await client.post(
        "/api/auth/register", json=payload(email="da@example.org", display_name="Angreifer")
    )
    assert r.status_code == 202
    row = await db.fetchrow(
        "SELECT display_name, password_hash FROM users WHERE id=$1", existing["id"]
    )
    assert row["display_name"] == "Vorhanden"
    assert row["password_hash"] == "!"


@pytest.mark.asyncio(loop_scope="session")
async def test_parallel_registrations_of_same_email_yield_one_account(
    client: httpx.AsyncClient, db: Any
) -> None:
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/auth/register",
                json=payload(email="race@example.org", password=PASSWORD + str(i)),
            )
            for i in range(6)
        ]
    )
    assert {r.status_code for r in responses} == {202}
    assert len({r.content for r in responses}) == 1
    assert len(await user_rows(db)) == 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("consent", [False, None, "true", 1, "yes"])
async def test_register_requires_strict_true_consent(
    client: httpx.AsyncClient, db: Any, consent: Any
) -> None:
    r = await client.post("/api/auth/register", json=payload(consent=consent))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert await user_rows(db) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_register_missing_consent_is_422(client: httpx.AsyncClient, db: Any) -> None:
    body = payload()
    del body["consent"]
    r = await client.post("/api/auth/register", json=body)
    assert r.status_code == 422
    assert await user_rows(db) == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "password",
    ["kurz", "123456789", "x" * 129, "gültig-aber\x00-mit-nul", 12345678901234, None],
)
async def test_register_rejects_bad_passwords(
    client: httpx.AsyncClient, db: Any, password: Any
) -> None:
    r = await client.post("/api/auth/register", json=payload(password=password))
    assert r.status_code == 422
    assert await user_rows(db) == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("password", ["x" * 10, "x" * 128, "ü" * 10, "🚗" * 10])
async def test_register_accepts_password_length_boundaries(
    client: httpx.AsyncClient, db: Any, password: str
) -> None:
    r = await client.post("/api/auth/register", json=payload(password=password))
    assert r.status_code == 202
    assert len(await user_rows(db)) == 1


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "email",
    [
        "",
        "kein-at",
        "a@",
        "@example.org",
        "a b@example.org",
        "a@@example.org",
        "a" * 330 + "@x.org",
    ],
)
async def test_register_rejects_invalid_emails_without_echoing_them(
    client: httpx.AsyncClient, db: Any, email: str
) -> None:
    r = await client.post("/api/auth/register", json=payload(email=email))
    assert r.status_code == 422
    assert email == "" or email not in r.text
    assert await user_rows(db) == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "name",
    ["", "   ", "x" * 81, "Tab\there", "Zeile\nUmbruch", "Null\x00", "Bidi‮Evil", None],
)
async def test_register_rejects_bad_display_names(
    client: httpx.AsyncClient, db: Any, name: Any
) -> None:
    r = await client.post("/api/auth/register", json=payload(display_name=name))
    assert r.status_code == 422
    assert await user_rows(db) == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "name", ["Jörg Müller-Ünal", "田中 太郎", "Zoë 🚗", "x" * 80, "Åsa", "👨‍👩‍👧"]
)
async def test_register_accepts_unicode_display_names(
    client: httpx.AsyncClient, db: Any, name: str
) -> None:
    r = await client.post("/api/auth/register", json=payload(display_name=f"  {name}  "))
    assert r.status_code == 202
    assert (await user_rows(db))[0]["display_name"] == name


@pytest.mark.asyncio(loop_scope="session")
async def test_register_ignores_client_supplied_id_and_extra_fields(
    client: httpx.AsyncClient, db: Any
) -> None:
    r = await client.post(
        "/api/auth/register",
        json=payload(id="00000000-0000-0000-0000-000000000001", password_hash="x", is_admin=True),
    )
    assert r.status_code == 202
    rows = await user_rows(db)
    assert str(rows[0]["id"]) != "00000000-0000-0000-0000-000000000001"
    assert rows[0]["password_hash"].startswith("$argon2id$")


@pytest.mark.asyncio(loop_scope="session")
async def test_register_hashes_password_even_for_known_email(
    client: httpx.AsyncClient, db: Any
) -> None:
    """Zeitangleichung: auch bei bekannter E-Mail wird gehasht (kein Zeit-Orakel)."""
    from app.api.auth import get_password_hasher
    from app.main import app

    calls: list[int] = []

    class Counting(Argon2PasswordHasher):
        def hash(self, password: str) -> str:
            calls.append(1)
            return super().hash(password)

    app.dependency_overrides[get_password_hasher] = lambda: Counting()
    await create_user(db, email="bekannt@example.org")
    await client.post("/api/auth/register", json=payload(email="bekannt@example.org"))
    await client.post("/api/auth/register", json=payload(email="neu@example.org"))
    assert len(calls) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_register_logs_contain_neither_email_nor_password(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    email = "geheim.adresse@example.org"
    await client.post("/api/auth/register", json=payload(email=email))
    await client.post("/api/auth/register", json=payload(email=email))
    await client.post("/api/auth/register", json=payload(email=email, consent=False))
    for secret in ("geheim.adresse", PASSWORD, "Neue Person"):
        assert secret not in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_register_rate_limit_returns_429_with_retry_after(client: httpx.AsyncClient) -> None:
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: Settings(
        rate_limit_enabled=True,
        rate_limit_register_per_hour=2,
        jwt_secret="x" * 40,  # type: ignore[arg-type]
    )
    codes = []
    for i in range(3):
        r = await client.post("/api/auth/register", json=payload(email=f"rl{i}@example.org"))
        codes.append(r.status_code)
    assert codes == [202, 202, 429]
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
