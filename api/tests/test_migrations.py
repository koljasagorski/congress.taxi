"""Migrationen gegen echtes PostGIS: hoch, runter, wieder hoch; Schema-Garantien prüfen."""

from __future__ import annotations

import pytest
from asyncpg import CheckViolationError, UniqueViolationError

from tests.conftest import connect, run_alembic

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_upgrade_downgrade_upgrade(scratch_database: str) -> None:
    up = run_alembic(scratch_database, "upgrade", "head")
    assert up.returncode == 0, up.stderr

    conn = await connect(scratch_database)
    try:
        tables = {
            r["tablename"]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
        assert {"users", "rides", "ride_requests", "alembic_version"} <= tables
    finally:
        await conn.close()

    down = run_alembic(scratch_database, "downgrade", "base")
    assert down.returncode == 0, down.stderr

    conn = await connect(scratch_database)
    try:
        tables = {
            r["tablename"]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
        assert not {"users", "rides", "ride_requests"} & tables
    finally:
        await conn.close()

    again = run_alembic(scratch_database, "upgrade", "head")
    assert again.returncode == 0, again.stderr


async def test_upgrade_is_idempotent(migrated_database: str) -> None:
    result = run_alembic(migrated_database, "upgrade", "head")
    assert result.returncode == 0, result.stderr


async def test_spatial_and_time_indexes_exist(migrated_database: str) -> None:
    conn = await connect(migrated_database)
    try:
        rows = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='rides'"
        )
    finally:
        await conn.close()
    defs = {r["indexname"]: r["indexdef"] for r in rows}
    assert "USING gist (route_geom)" in defs["ix_rides_route_geom"]
    assert "USING gist (start_geom)" in defs["ix_rides_start_geom"]
    assert "USING btree (departure_time)" in defs["ix_rides_departure_time"]


async def _insert_user(conn, email: str) -> str:  # type: ignore[no-untyped-def]
    return str(
        await conn.fetchval(
            "INSERT INTO users (id, display_name, email, password_hash, consent_at) "
            "VALUES (gen_random_uuid(), 'T', $1, 'x', now()) RETURNING id",
            email,
        )
    )


_RIDE_INSERT = (
    "INSERT INTO rides (id, driver_id, start_geom, end_geom, route_geom, route_distance_m, "
    "route_duration_s, seats_total, seats_available, departure_time) "
    "VALUES (gen_random_uuid(), $1::uuid, "
    "ST_GeogFromText('POINT(10 53.5)'), ST_GeogFromText('POINT(9.99 53.56)'), "
    "ST_GeogFromText('LINESTRING(10 53.5, 9.99 53.56)'), 1000, 120, $2, $3, now()) RETURNING id"
)


async def test_ride_check_constraints(migrated_database: str) -> None:
    conn = await connect(migrated_database)
    try:
        driver = await _insert_user(conn, "driver-checks@example.org")
        with pytest.raises(CheckViolationError):  # mehr als 8 Plätze
            await conn.fetchval(_RIDE_INSERT, driver, 9, 9)
        with pytest.raises(CheckViolationError):  # mehr frei als gesamt
            await conn.fetchval(_RIDE_INSERT, driver, 2, 3)
        with pytest.raises(CheckViolationError):  # 0 Plätze gesamt
            await conn.fetchval(_RIDE_INSERT, driver, 0, 0)
        assert await conn.fetchval(_RIDE_INSERT, driver, 3, 3)
    finally:
        await conn.close()


async def test_only_one_active_request_per_ride_and_rider(migrated_database: str) -> None:
    conn = await connect(migrated_database)
    try:
        driver = await _insert_user(conn, "driver-unique@example.org")
        rider = await _insert_user(conn, "rider-unique@example.org")
        ride = await conn.fetchval(_RIDE_INSERT, driver, 3, 3)
        insert = (
            "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m, "
            "detour_duration_s, status) VALUES (gen_random_uuid(), $1::uuid, $2::uuid, "
            "ST_GeogFromText('POINT(10 53.5)'), 100, 60, $3)"
        )
        await conn.execute(insert, ride, rider, "pending")
        with pytest.raises(UniqueViolationError):  # zweite aktive Anfrage
            await conn.execute(insert, ride, rider, "accepted")
        # Nach Absage/Abbruch ist eine neue Anfrage wieder erlaubt
        await conn.execute(
            "UPDATE ride_requests SET status='declined' WHERE ride_id=$1::uuid", ride
        )
        await conn.execute(insert, ride, rider, "pending")
    finally:
        await conn.close()


async def test_deleting_user_cascades(migrated_database: str) -> None:
    conn = await connect(migrated_database)
    try:
        driver = await _insert_user(conn, "driver-cascade@example.org")
        rider = await _insert_user(conn, "rider-cascade@example.org")
        ride = await conn.fetchval(_RIDE_INSERT, driver, 3, 3)
        await conn.execute(
            "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m, "
            "detour_duration_s) VALUES (gen_random_uuid(), $1::uuid, $2::uuid, "
            "ST_GeogFromText('POINT(10 53.5)'), 100, 60)",
            ride,
            rider,
        )
        await conn.execute("DELETE FROM users WHERE id=$1::uuid", driver)
        assert await conn.fetchval("SELECT count(*) FROM rides WHERE id=$1::uuid", ride) == 0
        assert (
            await conn.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id=$1::uuid", ride)
            == 0
        )
    finally:
        await conn.close()
