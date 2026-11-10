"""Test-Fixtures.

Die Tests laufen gegen ein echtes PostgreSQL/PostGIS (Compose-DB lokal, Service-Container in der
CI) – nicht gemockt. Pro Session wird eine frische Testdatenbank angelegt und migriert.
Verbindungsdaten kommen aus den POSTGRES_*-Variablen (Host Standard: localhost).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest_asyncio

API_DIR = Path(__file__).resolve().parent.parent
_BASE_DB = os.environ.get("POSTGRES_DB", "mitfahrer")
TEST_DB = f"{_BASE_DB}_test"

# MUSS vor dem ersten Import von app.* passieren (Settings sind gecacht).
os.environ["POSTGRES_DB"] = TEST_DB
os.environ["APP_ENV"] = "test"
os.environ.setdefault("POSTGRES_HOST", "localhost")
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
