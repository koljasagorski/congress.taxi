"""Retention-Cleanup: Fristgrenze, Löschen, Dry-Run, Idempotenz, Transaktion, CLI.

Läuft gegen echtes PostGIS (Test-Datenbank aus conftest); Datensätze werden direkt eingefügt.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from app import cleanup
from app.cleanup import is_due, retention_deadline_day, retention_expires_at, run_cleanup
from app.config import Settings
from tests.conftest import API_DIR, TEST_DB, connect
from tests.helpers import create_user, insert_ride

SETTINGS = Settings(event_end_date=date(2027, 1, 1), retention_days_after_event=14)
# Stichtag 2027-01-15 -> Frist endet mit Ablauf dieses Tages (UTC)
EXPIRES = datetime(2027, 1, 16, 0, 0, 0, tzinfo=UTC)

# --- Fristgrenze (rein, ohne DB) ----------------------------------------------------------------


def test_deadline_day_and_expiry_instant() -> None:
    assert retention_deadline_day(SETTINGS) == date(2027, 1, 15)
    assert retention_expires_at(SETTINGS) == EXPIRES


@pytest.mark.parametrize(
    ("now", "due"),
    [
        (EXPIRES - timedelta(days=30), False),
        (EXPIRES - timedelta(seconds=1), False),  # 23:59:59 am Stichtag
        (EXPIRES - timedelta(microseconds=1), False),  # letzter Instant des Stichtags
        (EXPIRES, True),  # genau auf der Grenze: 00:00:00 des Folgetags -> gelöscht
        (EXPIRES + timedelta(microseconds=1), True),
        (EXPIRES + timedelta(seconds=1), True),
        (EXPIRES + timedelta(days=400), True),
    ],
)
def test_is_due_boundary(now: datetime, due: bool) -> None:
    assert is_due(SETTINGS, now) is due


def test_is_due_respects_timezone_offsets() -> None:
    plus2 = timezone(timedelta(hours=2))
    # 01:59:59 in +02:00 = 23:59:59 UTC -> noch nicht; 02:00:00 in +02:00 = 00:00:00 UTC -> fällig
    assert is_due(SETTINGS, datetime(2027, 1, 16, 1, 59, 59, tzinfo=plus2)) is False
    assert is_due(SETTINGS, datetime(2027, 1, 16, 2, 0, 0, tzinfo=plus2)) is True


def test_is_due_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="Zeitzone"):
        is_due(SETTINGS, datetime(2027, 1, 16))


def test_zero_retention_days_expires_after_event_end_day() -> None:
    s = Settings(event_end_date=date(2027, 1, 1), retention_days_after_event=0)
    assert retention_expires_at(s) == datetime(2027, 1, 2, tzinfo=UTC)


# --- gegen die Datenbank --------------------------------------------------------------------------


async def _seed(db: Any, users: int = 3) -> dict[str, int]:
    """Legt Nutzer, Fahrten und Anfragen an. Gibt die erwarteten Zeilenzahlen zurück."""
    people = [
        await create_user(db, email=f"geheim{i}@example.org", display_name=f"Name{i}")
        for i in range(users)
    ]
    ride_ids = [await insert_ride(db, people[0]["id"]), await insert_ride(db, people[1]["id"])]
    requests = 0
    for ride_id, rider in ((ride_ids[0], people[1]), (ride_ids[0], people[2])):
        await db.execute(
            "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m, "
            "detour_duration_s) VALUES ($1, $2, $3, ST_GeogFromText('POINT(9.9 53.6)'), 1, 1)",
            uuid.uuid4(),
            ride_id,
            rider["id"],
        )
        requests += 1
    return {"users": users, "rides": 2, "ride_requests": requests}


async def _counts(db: Any) -> dict[str, int]:
    return {
        t: await db.fetchval(f"SELECT count(*) FROM {t}")  # noqa: S608
        for t in ("users", "rides", "ride_requests")
    }


async def test_nothing_happens_before_the_deadline(db: Any) -> None:
    seeded = await _seed(db)
    result = await run_cleanup(settings=SETTINGS, now=EXPIRES - timedelta(seconds=1))
    assert result.due is False
    assert await _counts(db) == seeded
    assert "nichts zu löschen" in result.summary(SETTINGS)


async def test_deletes_everything_on_the_boundary(db: Any) -> None:
    seeded = await _seed(db)
    result = await run_cleanup(settings=SETTINGS, now=EXPIRES)
    assert result.due and not result.dry_run
    assert result.counts == seeded
    assert await _counts(db) == {"users": 0, "rides": 0, "ride_requests": 0}


async def test_dry_run_counts_but_keeps_data(db: Any) -> None:
    seeded = await _seed(db)
    result = await run_cleanup(settings=SETTINGS, now=EXPIRES + timedelta(days=1), dry_run=True)
    assert result.due and result.dry_run
    assert result.counts == seeded
    assert await _counts(db) == seeded
    assert "würde löschen" in result.summary(SETTINGS)


async def test_is_idempotent(db: Any) -> None:
    await _seed(db)
    now = EXPIRES + timedelta(hours=5)
    first = await run_cleanup(settings=SETTINGS, now=now)
    second = await run_cleanup(settings=SETTINGS, now=now)
    assert sum(first.counts.values()) > 0
    assert second.counts == {"ride_requests": 0, "rides": 0, "users": 0}


async def test_runs_on_empty_database(db: Any) -> None:
    result = await run_cleanup(settings=SETTINGS, now=EXPIRES)
    assert result.due and sum(result.counts.values()) == 0


async def test_is_transactional(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = await _seed(db)
    # Dritte "Tabelle" existiert nicht: das DELETE scheitert, nachdem Anfragen und Fahrten
    # bereits gelöscht wurden – die Transaktion muss alles zurückrollen.
    monkeypatch.setattr(cleanup, "TABLES", ("ride_requests", "rides", "gibt_es_nicht"))
    with pytest.raises(Exception, match="gibt_es_nicht"):
        await run_cleanup(settings=SETTINGS, now=EXPIRES)
    assert await _counts(db) == seeded


async def test_cleanup_locks_in_contract_order_and_does_not_deadlock(db: Any) -> None:
    """Vertrag: Fahrt (rides) vor Anfrage (ride_requests). Eine fachliche Transaktion (accept,
    cancel ...) hält erst ``rides`` und fasst dann ``ride_requests`` an; der Cleanup darf damit
    nicht in einen Deadlock laufen, sondern muss warten, bis sie fertig ist."""
    seeded = await _seed(db)
    ride_id = await db.fetchval("SELECT id FROM rides ORDER BY id LIMIT 1")
    tx_conn = await connect(TEST_DB)
    tx = tx_conn.transaction()
    await tx.start()
    task: asyncio.Task[Any] | None = None
    try:
        await tx_conn.execute("UPDATE rides SET seats_total = seats_total WHERE id = $1", ride_id)
        task = asyncio.create_task(run_cleanup(settings=SETTINGS, now=EXPIRES))
        waiting = 0
        for _ in range(200):  # warten, bis der Cleanup auf eine Sperre wartet
            waiting = await db.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if waiting or task.done():
                break
            await asyncio.sleep(0.05)
        assert waiting, "Der Cleanup hätte auf die offene Transaktion warten müssen"
        # zweite Sperre der Fachtransaktion, in Vertragsreihenfolge (rides -> ride_requests)
        await tx_conn.execute(
            "UPDATE ride_requests SET status = status WHERE ride_id = $1", ride_id
        )
        await tx.commit()
        result = await asyncio.wait_for(task, timeout=20)
    finally:
        if tx_conn.is_in_transaction():
            await tx.rollback()
        await tx_conn.close()
        if task is not None and not task.done():
            task.cancel()
    assert result.counts == seeded
    assert await _counts(db) == {"users": 0, "rides": 0, "ride_requests": 0}


def test_summary_contains_only_counts_and_dates() -> None:
    result = cleanup.CleanupResult(
        due=True, dry_run=False, counts={"ride_requests": 1, "rides": 2, "users": 3}
    )
    text = result.summary(SETTINGS)
    assert text == (
        "cleanup: Frist abgelaufen seit 2027-01-16T00:00:00+00:00, gelöscht: "
        "ride_requests=1 rides=2 users=3"
    )


# --- CLI (eigener Prozess; erbt die Test-Datenbank aus conftest) -------------------------------


def _cli(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    full_env = {
        **os.environ,
        "EVENT_END_DATE": "2027-01-01",
        "RETENTION_DAYS_AFTER_EVENT": "14",
        **env,
    }
    return subprocess.run(
        [sys.executable, "-m", "app.cleanup", *args],
        cwd=API_DIR,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


async def test_cli_deletes_and_prints_only_counts(db: Any) -> None:
    await _seed(db)
    proc = await asyncio.to_thread(_cli, "--now", "2027-01-16T00:00:00Z")
    assert proc.returncode == 0, proc.stderr
    assert "ride_requests=2 rides=2 users=3" in proc.stdout
    for secret in ("geheim", "example.org", "Name0"):
        assert secret not in proc.stdout + proc.stderr
    assert await _counts(db) == {"users": 0, "rides": 0, "ride_requests": 0}


async def test_cli_dry_run_and_before_deadline(db: Any) -> None:
    seeded = await _seed(db)
    early = await asyncio.to_thread(_cli, "--now", "2027-01-15T23:59:59+00:00")
    assert early.returncode == 0 and "nichts zu löschen" in early.stdout
    dry = await asyncio.to_thread(_cli, "--dry-run", "--now", "2027-02-01T12:00:00Z")
    assert dry.returncode == 0 and "würde löschen" in dry.stdout
    assert await _counts(db) == seeded


async def test_cli_rejects_naive_and_invalid_now() -> None:
    naive = await asyncio.to_thread(_cli, "--now", "2027-01-16T00:00:00")
    assert naive.returncode == 2 and "Zeitzone" in naive.stderr
    garbage = await asyncio.to_thread(_cli, "--now", "morgen")
    assert garbage.returncode == 2


async def test_cli_database_error_exits_1_with_type_only() -> None:
    proc = await asyncio.to_thread(
        _cli,
        "--now",
        "2027-01-16T00:00:00Z",
        POSTGRES_PORT="1",
        POSTGRES_PASSWORD="supergeheimes-passwort",
    )
    assert proc.returncode == 1
    assert proc.stderr.strip().startswith("cleanup: Fehler (")
    assert "supergeheimes-passwort" not in proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
