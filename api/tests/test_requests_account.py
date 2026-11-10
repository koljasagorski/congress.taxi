"""Schnittstelle Phase 2/5: Konto-Löschen eines Mitfahrers darf keinen Platz verlieren.

``ride_requests.rider_id`` hat ``ON DELETE CASCADE`` und das Schema kennt keine Trigger. Wer ein
Konto löscht (``DELETE /api/me``, Phase 2), muss vorher ``release_seats_of_rider`` aufrufen –
sonst bleibt ``seats_available`` dauerhaft zu niedrig.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.db import get_sessionmaker
from app.main import app
from app.services.requests import release_seats_of_rider
from tests.helpers import auth_cookies, create_user, insert_ride
from tests.test_requests_common import (
    assert_seat_invariant,
    insert_request,
    request_status,
    ride_row,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_plain_cascade_delete_leaks_the_seat(db: Any) -> None:
    """Belegt den Befund: ohne Freigabe bleibt die Fahrt nach dem Löschen fälschlich voll."""
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=1)
    await insert_request(db, ride_id, rider["id"], status="accepted")

    await db.execute("DELETE FROM users WHERE id = $1", rider["id"])

    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 0, "status": "full"}
    with pytest.raises(AssertionError):
        await assert_seat_invariant(db)


async def test_release_before_delete_returns_seats_and_reopens_ride(db: Any) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    other = await create_user(db)
    full_ride = await insert_ride(db, driver["id"], seats_total=1)
    partial_ride = await insert_ride(db, driver["id"], seats_total=3)
    pending_ride = await insert_ride(db, driver["id"], seats_total=2)
    cancelled_ride = await insert_ride(db, driver["id"], seats_total=2)
    await insert_request(db, full_ride, rider["id"], status="accepted")
    await insert_request(db, partial_ride, rider["id"], status="accepted")
    await insert_request(db, partial_ride, other["id"], status="accepted")
    pending = await insert_request(db, pending_ride, rider["id"], status="pending")
    await insert_request(db, cancelled_ride, rider["id"], status="accepted")
    await db.execute("UPDATE rides SET status = 'cancelled' WHERE id = $1", cancelled_ride)

    async with get_sessionmaker()() as session:
        released = await release_seats_of_rider(session, rider["id"])
        await session.commit()
    await db.execute("DELETE FROM users WHERE id = $1", rider["id"])

    assert released == 3
    assert await ride_row(db, full_ride) == {
        "seats_total": 1,
        "seats_available": 1,
        "status": "open",
    }
    assert await ride_row(db, partial_ride) == {
        "seats_total": 3,
        "seats_available": 2,
        "status": "open",
    }
    assert (await ride_row(db, pending_ride))["seats_available"] == 2
    assert await db.fetchval("SELECT 1 FROM ride_requests WHERE id = $1", pending) is None
    # Abgesagte Fahrt: Status bleibt, Platz wird trotzdem wie bei cancel_request zurückgegeben
    assert (await ride_row(db, cancelled_ride))["status"] == "cancelled"
    await assert_seat_invariant(db)


async def test_release_without_accepted_requests_is_a_noop(db: Any) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=2)
    request_id = await insert_request(db, ride_id, rider["id"], status="pending")

    async with get_sessionmaker()() as session:
        assert await release_seats_of_rider(session, rider["id"]) == 0
        await session.commit()

    assert await request_status(db, request_id) == "pending"
    assert (await ride_row(db, ride_id))["seats_available"] == 2


# --- DELETE /api/me über die HTTP-Schnittstelle (eine Implementierung, dieselbe Transaktion) ---


async def _delete_me(user: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=auth_cookies(user)
    ) as c:
        return await c.delete("/api/me")


async def _post_as(user: dict[str, Any], path: str) -> Any:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=auth_cookies(user)
    ) as c:
        return await c.post(path)


async def test_rider_deletes_account_after_accept_driver_seat_is_free_again(db: Any) -> None:
    """(a) Annehmen über die API, Konto des Mitfahrers löschen -> Sitz wieder frei."""
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=1)
    request_id = await insert_request(db, ride_id, rider["id"])

    accepted = await _post_as(driver, f"/api/requests/{request_id}/accept")
    assert accepted.status_code == 200
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 0, "status": "full"}

    assert (await _delete_me(rider)).status_code == 204

    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 1, "status": "open"}
    assert await db.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id = $1", ride_id) == 0
    await assert_seat_invariant(db)


async def test_deleting_the_account_twice_credits_the_seat_only_once(db: Any) -> None:
    """Zweiter Aufruf (altes Token) scheitert mit 401 und ändert nichts; keine Doppelgutschrift."""
    driver = await create_user(db)
    rider = await create_user(db)
    other = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3)
    await insert_request(db, ride_id, rider["id"], status="accepted")
    await insert_request(db, ride_id, other["id"], status="accepted")

    assert (await _delete_me(rider)).status_code == 204
    assert (await _delete_me(rider)).status_code == 401

    assert (await ride_row(db, ride_id))["seats_available"] == 2
    await assert_seat_invariant(db)


async def test_driver_deletes_account_requests_and_rides_vanish_nothing_orphaned(db: Any) -> None:
    """(b) Fahrer löscht Konto: Fahrten und alle Anfragen darauf weg, fremde Daten bleiben."""
    driver = await create_user(db)
    rider = await create_user(db)
    other_driver = await create_user(db)
    rides = [await insert_ride(db, driver["id"], seats_total=2) for _ in range(2)]
    await insert_request(db, rides[0], rider["id"], status="accepted")
    await insert_request(db, rides[1], rider["id"], status="pending")
    # Der Fahrer ist zugleich Mitfahrer bei einer fremden Fahrt
    foreign = await insert_ride(db, other_driver["id"], seats_total=1)
    await insert_request(db, foreign, driver["id"], status="accepted")
    kept = await insert_ride(db, other_driver["id"], seats_total=2)
    await insert_request(db, kept, rider["id"], status="accepted")

    assert (await _delete_me(driver)).status_code == 204

    assert await db.fetchval("SELECT count(*) FROM rides WHERE driver_id = $1", driver["id"]) == 0
    assert (
        await db.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id = ANY($1)", rides) == 0
    )
    # keine verwaisten Zeilen (FKs sichern das; zusätzlich explizit geprüft)
    assert (
        await db.fetchval(
            "SELECT count(*) FROM ride_requests q "
            "WHERE NOT EXISTS (SELECT 1 FROM rides r WHERE r.id = q.ride_id) "
            "OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id = q.rider_id)"
        )
        == 0
    )
    # Sitz bei der fremden Fahrt wurde zurückgegeben, unbeteiligte Fahrt unberührt
    assert await ride_row(db, foreign) == {"seats_total": 1, "seats_available": 1, "status": "open"}
    assert (await ride_row(db, kept))["seats_available"] == 1
    await assert_seat_invariant(db)


@pytest.mark.parametrize("round_", range(8))
async def test_rider_delete_racing_with_accept_and_cancel_never_miscounts(
    db: Any, round_: int
) -> None:
    """(c) DELETE /api/me des Mitfahrers parallel zu accept und cancel auf derselben Fahrt."""
    driver = await create_user(db)
    rider = await create_user(db)
    bystander = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=2)
    mine = await insert_request(db, ride_id, rider["id"])
    theirs = await insert_request(db, ride_id, bystander["id"])
    if round_ % 2:  # in jeder zweiten Runde ist meine Anfrage schon angenommen
        await _post_as(driver, f"/api/requests/{mine}/accept")

    responses = await asyncio.gather(
        _delete_me(rider),
        _post_as(driver, f"/api/requests/{mine}/accept"),
        _post_as(rider, f"/api/requests/{mine}/cancel"),
        _post_as(driver, f"/api/requests/{theirs}/accept"),
        _post_as(bystander, f"/api/requests/{theirs}/cancel"),
    )

    assert all(r.status_code < 500 for r in responses), [r.text for r in responses]
    assert responses[0].status_code == 204
    row = await ride_row(db, ride_id)
    assert 0 <= row["seats_available"] <= row["seats_total"]
    assert await db.fetchval("SELECT count(*) FROM ride_requests WHERE id = $1", mine) == 0
    await assert_seat_invariant(db)


@pytest.mark.parametrize("round_", range(6))
async def test_driver_delete_racing_with_rider_actions_does_not_fail_or_orphan(
    db: Any, round_: int
) -> None:
    """DELETE /api/me der Fahrerin parallel zu accept/cancel auf ihren Fahrten: kein 5xx, nichts
    verwaist (Sperr-Reihenfolge Fahrt vor Anfrage, eigene Fahrten in der id-Reihenfolge)."""
    driver = await create_user(db)
    riders = [await create_user(db) for _ in range(3)]
    rides = [await insert_ride(db, driver["id"], seats_total=2) for _ in range(3)]
    requests = [await insert_request(db, rides[i], riders[i]["id"]) for i in range(3)]
    jobs: list[Any] = [_delete_me(driver)]
    for i in range(3):
        jobs.append(_post_as(driver, f"/api/requests/{requests[i]}/accept"))
        jobs.append(_post_as(riders[i], f"/api/requests/{requests[i]}/cancel"))

    responses = await asyncio.gather(*jobs)

    assert all(r.status_code < 500 for r in responses), [r.text for r in responses]
    assert responses[0].status_code == 204
    assert await db.fetchval("SELECT count(*) FROM rides WHERE driver_id = $1", driver["id"]) == 0
    assert (
        await db.fetchval("SELECT count(*) FROM ride_requests WHERE ride_id = ANY($1)", rides) == 0
    )
    await assert_seat_invariant(db)
