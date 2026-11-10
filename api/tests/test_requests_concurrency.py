"""Echte Parallelität gegen PostGIS: Row-Locking beim Annehmen, Duplikate, Accept vs. Cancel.

Jeder parallele Aufruf nutzt einen eigenen HTTP-Client und damit (über den Engine-Pool der App)
eine eigene DB-Verbindung/Session. Nach jedem Szenario wird die Konsistenz-Invariante direkt in
der Datenbank geprüft: ``seats_available == seats_total − #accepted``, nie negativ.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from tests.fakes import FakeRouting
from tests.helpers import auth_cookies, create_user, insert_ride
from tests.test_requests_common import (
    BASE_DISTANCE_M,
    BASE_DURATION_S,
    assert_seat_invariant,
    insert_request,
    pickup_json,
    request_status,
    ride_row,
    small_detour,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

PARALLEL = 8


@asynccontextmanager
async def _client_for(user: dict[str, Any]) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=auth_cookies(user)
    ) as c:
        yield c


async def _post(user: dict[str, Any], path: str, json: Any = None) -> httpx.Response:
    async with _client_for(user) as c:
        return await c.post(path, json=json)


def _codes(responses: list[httpx.Response]) -> Counter[str]:
    return Counter("ok" if r.status_code < 300 else r.json()["error"]["code"] for r in responses)


async def _pending_riders(
    db: Any, ride_id: uuid.UUID, n: int
) -> list[tuple[dict[str, Any], uuid.UUID]]:
    out = []
    for _ in range(n):
        rider = await create_user(db)
        out.append((rider, await insert_request(db, ride_id, rider["id"])))
    return out


async def test_parallel_accepts_for_one_free_seat_exactly_one_wins(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=1)
    riders = await _pending_riders(db, ride_id, PARALLEL)

    responses = await asyncio.gather(
        *(_post(driver, f"/api/requests/{rid}/accept") for _, rid in riders)
    )

    assert _codes(list(responses)) == Counter({"ok": 1, "ride_full": PARALLEL - 1})
    assert {r.status_code for r in responses} == {200, 409}
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 0, "status": "full"}
    statuses = Counter([await request_status(db, rid) for _, rid in riders])
    assert statuses == Counter({"accepted": 1, "pending": PARALLEL - 1})
    await assert_seat_invariant(db)


async def test_parallel_accepts_with_several_seats_never_overbook(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3)
    riders = await _pending_riders(db, ride_id, 12)

    responses = await asyncio.gather(
        *(_post(driver, f"/api/requests/{rid}/accept") for _, rid in riders)
    )

    assert _codes(list(responses)) == Counter({"ok": 3, "ride_full": 9})
    assert await ride_row(db, ride_id) == {"seats_total": 3, "seats_available": 0, "status": "full"}
    await assert_seat_invariant(db)


async def test_repeated_rounds_of_contention_stay_consistent(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    for _ in range(5):
        ride_id = await insert_ride(db, driver["id"], seats_total=1)
        riders = await _pending_riders(db, ride_id, 6)
        responses = await asyncio.gather(
            *(_post(driver, f"/api/requests/{rid}/accept") for _, rid in riders)
        )
        assert _codes(list(responses)) == Counter({"ok": 1, "ride_full": 5})
        await assert_seat_invariant(db)


async def test_parallel_accept_of_the_same_request_decrements_once(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3)
    [(_, request_id)] = await _pending_riders(db, ride_id, 1)

    responses = await asyncio.gather(
        *(_post(driver, f"/api/requests/{request_id}/accept") for _ in range(6))
    )

    assert _codes(list(responses)) == Counter({"ok": 1, "invalid_state": 5})
    assert (await ride_row(db, ride_id))["seats_available"] == 2
    await assert_seat_invariant(db)


async def test_parallel_cancel_of_an_accepted_request_returns_the_seat_once(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=2)
    request_id = await insert_request(db, ride_id, rider["id"], status="accepted")

    responses = await asyncio.gather(
        *(_post(rider if i % 2 else driver, f"/api/requests/{request_id}/cancel") for i in range(6))
    )

    assert _codes(list(responses)) == Counter({"ok": 1, "invalid_state": 5})
    assert await ride_row(db, ride_id) == {"seats_total": 2, "seats_available": 2, "status": "open"}
    await assert_seat_invariant(db)


async def test_parallel_duplicate_requests_create_exactly_one(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    driver = await create_user(db)
    rider = await create_user(db)
    for _ in range(3):
        ride_id = await insert_ride(
            db, driver["id"], distance_m=BASE_DISTANCE_M, duration_s=BASE_DURATION_S
        )
        responses = await asyncio.gather(
            *(
                _post(rider, f"/api/rides/{ride_id}/requests", pickup_json())
                for _ in range(PARALLEL)
            )
        )
        assert _codes(list(responses)) == Counter({"ok": 1, "already_requested": PARALLEL - 1})
        assert {r.status_code for r in responses} == {201, 409}
        assert (
            await db.fetchval(
                "SELECT count(*) FROM ride_requests WHERE ride_id = $1 AND rider_id = $2",
                ride_id,
                rider["id"],
            )
            == 1
        )
    await assert_seat_invariant(db)


async def test_parallel_creates_by_different_riders_all_succeed_and_book_nothing(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = small_detour
    driver = await create_user(db)
    ride_id = await insert_ride(
        db, driver["id"], seats_total=1, distance_m=BASE_DISTANCE_M, duration_s=BASE_DURATION_S
    )
    riders = [await create_user(db) for _ in range(PARALLEL)]
    responses = await asyncio.gather(
        *(_post(r, f"/api/rides/{ride_id}/requests", pickup_json()) for r in riders)
    )
    assert _codes(list(responses)) == Counter({"ok": PARALLEL})
    assert (await ride_row(db, ride_id))["seats_available"] == 1
    await assert_seat_invariant(db)


@pytest.mark.parametrize("round_", range(6))
async def test_accept_racing_with_cancel_of_the_same_pending_request(
    client: httpx.AsyncClient, db: Any, round_: int
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=1)
    request_id = await insert_request(db, ride_id, rider["id"])

    accept, cancel = await asyncio.gather(
        _post(driver, f"/api/requests/{request_id}/accept"),
        _post(rider, f"/api/requests/{request_id}/cancel"),
    )

    # Zwei zulässige Reihenfolgen – nie eine inkonsistente Mischung:
    #  (a) accept zuerst: beide 200 (cancel gibt den Platz zurück) -> cancelled, Fahrt offen
    #  (b) cancel zuerst: cancel 200, accept 409 invalid_state -> cancelled, Fahrt offen
    assert cancel.status_code == 200
    assert accept.status_code in (200, 409)
    if accept.status_code == 409:
        assert accept.json()["error"]["code"] == "invalid_state"
    assert await request_status(db, request_id) == "cancelled"
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 1, "status": "open"}
    await assert_seat_invariant(db)


@pytest.mark.parametrize("round_", range(6))
async def test_cancel_of_booked_seat_racing_with_accept_of_the_next_rider(
    client: httpx.AsyncClient, db: Any, round_: int
) -> None:
    driver = await create_user(db)
    first = await create_user(db)
    second = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=1)
    first_req = await insert_request(db, ride_id, first["id"], status="accepted")
    second_req = await insert_request(db, ride_id, second["id"])

    cancel, accept = await asyncio.gather(
        _post(first, f"/api/requests/{first_req}/cancel"),
        _post(driver, f"/api/requests/{second_req}/accept"),
    )

    assert cancel.status_code == 200
    assert await request_status(db, first_req) == "cancelled"
    if accept.status_code == 200:  # Platz war schon zurückgegeben
        assert await request_status(db, second_req) == "accepted"
        assert (await ride_row(db, ride_id))["status"] == "full"
    else:  # accept kam vor dem Cancel: Fahrt war noch voll
        assert accept.status_code == 409
        assert accept.json()["error"]["code"] == "ride_full"
        assert await request_status(db, second_req) == "pending"
        assert (await ride_row(db, ride_id))["status"] == "open"
    await assert_seat_invariant(db)


async def test_mixed_storm_keeps_invariant(client: httpx.AsyncClient, db: Any) -> None:
    """Accepts, Declines und Cancels gleichzeitig auf mehreren Fahrten mit knappen Plätzen."""
    driver = await create_user(db)
    rides = [await insert_ride(db, driver["id"], seats_total=2) for _ in range(3)]
    jobs: list[Any] = []
    for ride_id in rides:
        for i, (rider, request_id) in enumerate(await _pending_riders(db, ride_id, 5)):
            if i < 3:
                jobs.append(_post(driver, f"/api/requests/{request_id}/accept"))
                jobs.append(_post(rider, f"/api/requests/{request_id}/cancel"))
            else:
                jobs.append(_post(driver, f"/api/requests/{request_id}/decline"))
    responses = await asyncio.gather(*jobs)

    assert all(r.status_code in (200, 409) for r in responses), [r.text for r in responses]
    await assert_seat_invariant(db)
    for ride_id in rides:
        assert (await ride_row(db, ride_id))["seats_available"] >= 0
