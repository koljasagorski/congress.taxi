"""Test-Fixtures.

Die Tests laufen gegen ein echtes PostgreSQL/PostGIS (Compose-DB lokal, Service-Container in der
CI) – nicht gemockt. Pro Session wird eine frische Testdatenbank angelegt und migriert.
Verbindungsdaten kommen aus den POSTGRES_*-Variablen (Host Standard: localhost).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import pytest_asyncio

from tests.fakes import FakeRouting

API_DIR = Path(__file__).resolve().parent.parent
_BASE_DB = os.environ.get("POSTGRES_DB", "mitfahrer")
# TEST_DB_SUFFIX trennt parallele Testläufe (z. B. mehrere Entwickler/Agenten gegen denselben
# Postgres): `make test TEST_DB_SUFFIX=p3` nutzt die Datenbank `<POSTGRES_DB>_test_p3`.
_SUFFIX = os.environ.get("TEST_DB_SUFFIX", "")
if _SUFFIX and not re.fullmatch(r"[a-z0-9_]{1,16}", _SUFFIX):
    raise RuntimeError("TEST_DB_SUFFIX darf nur [a-z0-9_] enthalten (max. 16 Zeichen)")
TEST_DB = f"{_BASE_DB}_test" + (f"_{_SUFFIX}" if _SUFFIX else "")

# MUSS vor dem ersten Import von app.* passieren (Settings sind gecacht).
os.environ["POSTGRES_DB"] = TEST_DB
os.environ["APP_ENV"] = "test"
# Tests sprechen HTTP (kein TLS) und sollen deterministisch sein: Secure-Cookie und Rate-Limits
# aus. Rate-Limit-Tests schalten sie gezielt über ein überschriebenes Settings-Objekt wieder an.
os.environ["COOKIE_SECURE"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
# Standard bewusst nicht 5432: dort läuft auf Entwicklerrechnern oft ein fremder Postgres.
# 127.0.0.1 statt localhost, damit nicht ::1 (fremder Server) zuerst probiert wird.
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "55432")
os.environ.setdefault("POSTGRES_USER", "mitfahrer")
os.environ.setdefault("POSTGRES_PASSWORD", "mitfahrer")
os.environ.setdefault("JWT_SECRET", "test-secret")

from app.config import get_settings  # noqa: E402


def _admin_dsn() -> dict[str, object]:
    s = get_settings()
    return {
        "host": s.postgres_host,
        "port": s.postgres_port,
        "user": s.postgres_user,
        "password": s.postgres_password.get_secret_value(),
        "database": "postgres",
    }


async def _admin_connection(name: str) -> asyncpg.Connection:
    """Admin-Verbindung mit Schutz vor fremden Servern.

    Die Tests legen Datenbanken an und löschen sie. Das geschieht nur auf einem Server, auf dem
    die Basis-Datenbank (POSTGRES_DB, vom Compose-/CI-Container angelegt) existiert, und nur für
    Namen, die auf einen Test-Suffix enden.
    """
    assert name.startswith(TEST_DB), f"Unerwarteter Datenbankname: {name}"
    conn = await asyncpg.connect(**_admin_dsn())  # type: ignore[arg-type]
    if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", _BASE_DB):
        await conn.close()
        raise RuntimeError(
            f"Der Server enthält keine Datenbank '{_BASE_DB}' – das ist nicht der Projekt-Server. "
            "Tests abgebrochen (POSTGRES_HOST/POSTGRES_PORT prüfen)."
        )
    return conn


async def _recreate_database(name: str) -> None:
    conn = await _admin_connection(name)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _drop_database(name: str) -> None:
    conn = await _admin_connection(name)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def run_alembic(database: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Alembic in eigenem Prozess (eigene Event-Loop) gegen die angegebene Datenbank."""
    env = {**os.environ, "POSTGRES_DB": database}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_database() -> AsyncIterator[str]:
    await _recreate_database(TEST_DB)
    result = run_alembic(TEST_DB, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    yield TEST_DB
    from app.db import dispose_engine

    await dispose_engine()
    await _drop_database(TEST_DB)


@pytest_asyncio.fixture(loop_scope="session")
async def scratch_database() -> AsyncIterator[str]:
    """Frische, leere Datenbank für Migrationstests (wird am Ende gelöscht)."""
    name = f"{TEST_DB}_scratch"
    await _recreate_database(name)
    yield name
    await _drop_database(name)


async def connect(database: str) -> asyncpg.Connection:
    """Direkte asyncpg-Verbindung (für Schema-Prüfungen)."""
    dsn = {**_admin_dsn(), "database": database}
    return await asyncpg.connect(**dsn)  # type: ignore[arg-type]


# --- Fixtures für API-Tests (Vertrag; gemeinsam für alle Phasen) ---


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(migrated_database: str) -> AsyncIterator[None]:
    """Leert die Tabellen vor und nach dem Test (Schema bleibt)."""

    async def _truncate() -> None:
        conn = await connect(migrated_database)
        try:
            await conn.execute("TRUNCATE users, rides, ride_requests RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    await _truncate()
    yield
    await _truncate()


@pytest.fixture
def routing() -> FakeRouting:
    """Deterministischer Routing-Fake (Hamburg-Ebene). Pro Test anpassbar."""
    return FakeRouting()


@pytest_asyncio.fixture(loop_scope="session")
async def client(clean_db: None, routing: FakeRouting) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-Client gegen die ASGI-App mit Fake-Routing (setzt Limiter und Overrides zurück)."""
    from app.main import app
    from app.ratelimit import limiter
    from app.routing import get_routing_client

    limiter.reset()
    app.dependency_overrides[get_routing_client] = lambda: routing
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    limiter.reset()


@pytest_asyncio.fixture(loop_scope="session")
async def db(clean_db: None) -> AsyncIterator[object]:
    """Direkte asyncpg-Verbindung zur Test-Datenbank (für Vorbedingungen und Nachprüfungen)."""
    conn = await connect(TEST_DB)
    try:
        yield conn
    finally:
        await conn.close()
