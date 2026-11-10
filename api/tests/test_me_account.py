"""GET /api/me und DELETE /api/me (Konto löschen)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from app.auth.tokens import COOKIE_NAME
from app.config import get_settings
from tests.conftest import TEST_DB, connect
from tests.helpers import auth_cookies, create_user, insert_ride


def sign_in(client: httpx.AsyncClient, user: dict[str, Any]) -> None:
    """Setzt das Token als Cookie am Client (statt veralteter Per-Request-Cookies)."""
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, auth_cookies(user)[COOKIE_NAME])


async def insert_request(db: Any, ride_id: Any, rider_id: Any, status: str = "pending") -> None:
    await db.execute(
        "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m, "
        "detour_duration_s, status) "
        "VALUES ($1, $2, $3, ST_GeogFromText('POINT(9.97 53.6)'), 1000, 60, $4)",
        uuid.uuid4(),
        ride_id,
        rider_id,
        status,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_me_returns_own_profile(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db, email="ich@example.org", display_name="Ich Selbst")
    sign_in(client, user)
    r = await client.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "display_name", "email", "created_at"}
    assert body["id"] == str(user["id"])
    assert body["display_name"] == "Ich Selbst"
    assert body["email"] == "ich@example.org"
    created = datetime.fromisoformat(body["created_at"])
    assert created.tzinfo is not None
    assert abs(datetime.now(UTC) - created) < timedelta(minutes=5)


@pytest.mark.asyncio(loop_scope="session")
async def test_me_requires_valid_token(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    secret = get_settings().jwt_secret.get_secret_value()
    forged = jwt.encode(
        {"sub": str(user["id"]), "exp": 9_999_999_999}, "falsches-geheimnis" * 3, algorithm="HS256"
    )
    expired = jwt.encode(
        {
            "sub": str(user["id"]),
            "exp": int((datetime.now(UTC) - timedelta(minutes=1)).timestamp()),
        },
        secret,
        algorithm="HS256",
    )
    none_alg = jwt.encode({"sub": str(user["id"]), "exp": 9_999_999_999}, "", algorithm="none")
    for token in (None, "", "kaputt", forged, expired, none_alg):
        client.cookies.clear()
        if token is not None:
            client.cookies.set(COOKIE_NAME, token)
        for method in ("GET", "DELETE"):
            r = await client.request(method, "/api/me")
            assert r.status_code == 401, (method, token)
            assert r.json()["error"]["code"] == "unauthorized"
    # nichts wurde gelöscht
    assert await db.fetchval("SELECT count(*) FROM users") == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_me_removes_account_rides_and_requests_only_for_that_user(
    client: httpx.AsyncClient, db: Any
) -> None:
    me = await create_user(db, email="weg@example.org")
    other = await create_user(db, email="bleibt@example.org")
    my_ride = await insert_ride(db, me["id"])
    other_ride = await insert_ride(db, other["id"])
    await insert_request(db, other_ride, me["id"], "accepted")  # meine Anfrage an fremde Fahrt
    await insert_request(db, my_ride, other["id"], "pending")  # fremde Anfrage an meine Fahrt
    await insert_request(db, other_ride, me["id"], "cancelled")

    sign_in(client, me)
    r = await client.delete("/api/me")
    assert r.status_code == 204 and r.content == b""
    cookie = r.headers["set-cookie"].lower()
    assert f"{COOKIE_NAME}=" in cookie and ("max-age=0" in cookie or "expires=" in cookie)

    assert await db.fetchval("SELECT count(*) FROM users WHERE id=$1", me["id"]) == 0
    assert await db.fetchval("SELECT count(*) FROM rides WHERE driver_id=$1", me["id"]) == 0
    assert await db.fetchval("SELECT count(*) FROM ride_requests WHERE rider_id=$1", me["id"]) == 0
    assert await db.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id=$1", my_ride) == 0
    # fremde Daten bleiben
    assert await db.fetchval("SELECT count(*) FROM users WHERE id=$1", other["id"]) == 1
    assert await db.fetchval("SELECT count(*) FROM rides WHERE id=$1", other_ride) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_token_is_worthless_after_account_deletion(
    client: httpx.AsyncClient, db: Any
) -> None:
    user = await create_user(db)
    sign_in(client, user)
    assert (await client.get("/api/me")).status_code == 200
    assert (await client.delete("/api/me")).status_code == 204
    sign_in(client, user)  # altes Token erneut vorlegen (der Server hat das Cookie gelöscht)
    assert (await client.get("/api/me")).status_code == 401
    assert (await client.delete("/api/me")).status_code == 401


@pytest.mark.asyncio(loop_scope="session")
async def test_full_lifecycle_register_login_delete_then_email_is_free_again(
    client: httpx.AsyncClient, db: Any
) -> None:
    body = {
        "email": "leben@example.org",
        "password": "korrekt-pferd-batterie",
        "display_name": "Leben",
        "consent": True,
    }
    assert (await client.post("/api/auth/register", json=body)).status_code == 202
    login = await client.post(
        "/api/auth/login", json={"email": body["email"], "password": body["password"]}
    )
    assert login.status_code == 200
    assert (await client.delete("/api/me")).status_code == 204
    assert COOKIE_NAME not in client.cookies
    assert await db.fetchval("SELECT count(*) FROM users") == 0
    relogin = await client.post(
        "/api/auth/login", json={"email": body["email"], "password": body["password"]}
    )
    assert relogin.status_code == 401
    assert (await client.post("/api/auth/register", json=body)).status_code == 202
    assert await db.fetchval("SELECT count(*) FROM users") == 1


async def _ride_state(db: Any, ride_id: Any) -> dict[str, Any]:
    row = await db.fetchrow(
        "SELECT status, seats_available, seats_total FROM rides WHERE id=$1", ride_id
    )
    return dict(row)


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_rider_gives_accepted_seat_back_and_reopens_full_ride(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db, email="fahrerin@example.org")
    rider = await create_user(db, email="mitfahrer@example.org")
    ride = await insert_ride(db, driver["id"], seats_total=1, seats_available=0, status="full")
    await insert_request(db, ride, rider["id"], "accepted")

    sign_in(client, rider)
    assert (await client.delete("/api/me")).status_code == 204

    assert await _ride_state(db, ride) == {"status": "open", "seats_available": 1, "seats_total": 1}
    assert await db.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id=$1", ride) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_rider_seat_release_only_counts_accepted_and_keeps_cancelled_ride(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db, email="fahrerin2@example.org")
    rider = await create_user(db, email="mitfahrer2@example.org")
    other = await create_user(db, email="andere@example.org")
    # 3 Plätze, 2 vergeben (meiner + fremder) -> offen, 1 frei; nach Löschung 2 frei
    partial = await insert_ride(db, driver["id"], seats_total=3, seats_available=1, status="open")
    await insert_request(db, partial, rider["id"], "accepted")
    await insert_request(db, partial, other["id"], "accepted")
    # pending/declined/cancelled geben keine Plätze zurück
    untouched = await insert_ride(db, driver["id"], seats_total=2, seats_available=2)
    await insert_request(db, untouched, rider["id"], "pending")
    await insert_request(db, untouched, rider["id"], "declined")
    # stornierte Fahrt bleibt cancelled, Plätze unverändert
    cancelled = await insert_ride(
        db, driver["id"], seats_total=2, seats_available=2, status="cancelled"
    )
    await insert_request(db, cancelled, rider["id"], "accepted")

    sign_in(client, rider)
    assert (await client.delete("/api/me")).status_code == 204

    assert await _ride_state(db, partial) == {
        "status": "open",
        "seats_available": 2,
        "seats_total": 3,
    }
    assert (await _ride_state(db, untouched))["seats_available"] == 2
    assert await _ride_state(db, cancelled) == {
        "status": "cancelled",
        "seats_available": 2,
        "seats_total": 2,
    }


async def _wait_until_blocked(db: Any) -> None:
    """Wartet, bis eine andere Sitzung auf eine Sperre wartet (deterministisch statt sleep)."""
    for _ in range(200):
        if await db.fetchval("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'"):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("DELETE /api/me wartet nicht auf die Fahrtsperre")


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_rider_does_not_double_refund_when_cancel_wins_the_ride_lock(
    client: httpx.AsyncClient, db: Any
) -> None:
    """Race gegen cancel: Der Mitfahrer storniert (Muster aus Phase 5: Fahrt sperren, Platz +1),
    während DELETE /api/me auf die Fahrtsperre wartet. Die Gutschrift darf nicht vor der Sperre
    gezählt werden, sonst bekäme die Fahrt den Platz doppelt (zulasten eines anderen Mitfahrers)."""
    driver = await create_user(db, email="fahrer-race@example.org")
    rider = await create_user(db, email="rider-race@example.org")
    other = await create_user(db, email="other-race@example.org")
    # 3 Plätze, 2 vergeben (rider + other), 1 frei
    ride = await insert_ride(db, driver["id"], seats_total=3, seats_available=1)
    await insert_request(db, ride, rider["id"], "accepted")
    await insert_request(db, ride, other["id"], "accepted")

    holder = await connect(TEST_DB)
    try:
        tx = holder.transaction()
        await tx.start()
        await holder.fetchval("SELECT id FROM rides WHERE id=$1 FOR UPDATE", ride)

        sign_in(client, rider)
        deleting = asyncio.create_task(client.delete("/api/me"))
        await _wait_until_blocked(db)
        assert not deleting.done()

        # cancel gewinnt: Anfrage storniert, Platz zurück (Fahrt gesperrt gehalten)
        await holder.execute(
            "UPDATE ride_requests SET status='cancelled' WHERE ride_id=$1 AND rider_id=$2",
            ride,
            rider["id"],
        )
        await holder.execute(
            "UPDATE rides SET seats_available = seats_available + 1 WHERE id=$1", ride
        )
        await tx.commit()
        assert (await deleting).status_code == 204
    finally:
        await holder.close()

    # 2 frei (1 + eigener Platz), der Platz des anderen Mitfahrers bleibt belegt
    assert await _ride_state(db, ride) == {"status": "open", "seats_available": 2, "seats_total": 3}


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_rider_refunds_seat_when_accept_wins_the_ride_lock(
    client: httpx.AsyncClient, db: Any
) -> None:
    """Race gegen accept: Die Anfrage ist beim Start noch pending und wird angenommen, während
    DELETE /api/me wartet. Der dann belegte Platz muss zurückgegeben werden."""
    driver = await create_user(db, email="fahrer-race2@example.org")
    rider = await create_user(db, email="rider-race2@example.org")
    ride = await insert_ride(db, driver["id"], seats_total=1, seats_available=1)
    await insert_request(db, ride, rider["id"], "pending")

    holder = await connect(TEST_DB)
    try:
        tx = holder.transaction()
        await tx.start()
        await holder.fetchval("SELECT id FROM rides WHERE id=$1 FOR UPDATE", ride)

        sign_in(client, rider)
        deleting = asyncio.create_task(client.delete("/api/me"))
        await _wait_until_blocked(db)
        assert not deleting.done()

        await holder.execute(
            "UPDATE ride_requests SET status='accepted' WHERE ride_id=$1 AND rider_id=$2",
            ride,
            rider["id"],
        )
        await holder.execute("UPDATE rides SET seats_available=0, status='full' WHERE id=$1", ride)
        await tx.commit()
        assert (await deleting).status_code == 204
    finally:
        await holder.close()

    assert await _ride_state(db, ride) == {"status": "open", "seats_available": 1, "seats_total": 1}
    assert await db.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id=$1", ride) == 0
