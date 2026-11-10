"""PATCH und DELETE /api/rides/{id} – Regeln, Autorisierung, Stornierung, Sperrreihenfolge."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest

from tests.helpers import create_user, future, insert_ride
from tests.test_rides_common import ALL_RIDE_KEYS, auth_headers, insert_request

pytestmark = pytest.mark.asyncio(loop_scope="session")


def url(ride_id: uuid.UUID | str) -> str:
    return f"/api/rides/{ride_id}"


async def _ride_row(db: Any, ride_id: uuid.UUID | str) -> Any:
    return await db.fetchrow(
        "SELECT seats_total, seats_available, status, departure_time, route_distance_m, "
        "ST_NPoints(route_geom::geometry) AS n FROM rides WHERE id = $1::uuid",
        str(ride_id),
    )


# --- PATCH: Erfolg -------------------------------------------------------------------------------


async def test_patch_departure_time(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], departure_time=future(10))
    before = await _ride_row(db, ride_id)
    new_time = future(70).replace(microsecond=0)
    resp = await client.patch(
        url(ride_id), json={"departure_time": new_time.isoformat()}, headers=auth_headers(driver)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == ALL_RIDE_KEYS
    assert body["id"] == str(ride_id)
    row = await _ride_row(db, ride_id)
    assert row["departure_time"] == new_time
    # Die Zeitänderung ändert Route und Plätze nicht
    assert row["route_distance_m"] == before["route_distance_m"]
    assert row["n"] == before["n"]
    assert (row["seats_total"], row["seats_available"]) == (3, 3)


async def test_patch_departure_time_with_pending_requests_is_allowed(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    await insert_request(db, ride_id, rider["id"], "pending")
    resp = await client.patch(
        url(ride_id), json={"departure_time": future(99).isoformat()}, headers=auth_headers(driver)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pending_requests"] == 1


async def test_patch_seats_total_recomputes_available_and_status(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    riders = [await create_user(db) for _ in range(2)]
    ride_id = await insert_ride(db, driver["id"], seats_total=3, seats_available=1)
    for r in riders:
        await insert_request(db, ride_id, r["id"], "accepted")
    headers = auth_headers(driver)

    # 3 → 5: available = 5 − 2 = 3
    resp = await client.patch(url(ride_id), json={"seats_total": 5}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["seats_total"], body["seats_available"], body["status"]) == (5, 3, "open")
    assert body["accepted_requests"] == 2

    # 5 → 2 (== angenommene): available 0 → full
    resp = await client.patch(url(ride_id), json={"seats_total": 2}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["seats_total"], body["seats_available"], body["status"]) == (2, 0, "full")
    row = await _ride_row(db, ride_id)
    assert (row["seats_total"], row["seats_available"], row["status"]) == (2, 0, "full")

    # full → open, sobald wieder Platz ist
    resp = await client.patch(url(ride_id), json={"seats_total": 8}, headers=headers)
    body = resp.json()
    assert (body["seats_total"], body["seats_available"], body["status"]) == (8, 6, "open")


async def test_patch_seats_total_without_accepted_resets_available(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3)
    resp = await client.patch(url(ride_id), json={"seats_total": 1}, headers=auth_headers(driver))
    body = resp.json()
    assert (body["seats_total"], body["seats_available"], body["status"]) == (1, 1, "open")


async def test_patch_ignores_pending_for_seat_math(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=2)
    await insert_request(db, ride_id, rider["id"], "pending")
    resp = await client.patch(url(ride_id), json={"seats_total": 1}, headers=auth_headers(driver))
    assert resp.status_code == 200, resp.text
    assert resp.json()["seats_available"] == 1


async def test_patch_both_fields_at_once(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=2)
    new_time = future(40).replace(microsecond=0)
    resp = await client.patch(
        url(ride_id),
        json={"departure_time": new_time.isoformat(), "seats_total": 4},
        headers=auth_headers(driver),
    )
    assert resp.status_code == 200, resp.text
    row = await _ride_row(db, ride_id)
    assert row["departure_time"] == new_time
    assert (row["seats_total"], row["seats_available"]) == (4, 4)


async def test_patch_empty_body_changes_nothing(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    before = await _ride_row(db, ride_id)
    resp = await client.patch(url(ride_id), json={}, headers=auth_headers(driver))
    assert resp.status_code == 200
    assert dict(await _ride_row(db, ride_id)) == dict(before)


# --- PATCH: gesperrte Änderungen -----------------------------------------------------------------


async def test_patch_departure_time_blocked_with_accepted_requests(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3, seats_available=2)
    await insert_request(db, ride_id, rider["id"], "accepted")
    before = await _ride_row(db, ride_id)
    resp = await client.patch(
        url(ride_id), json={"departure_time": future(99).isoformat()}, headers=auth_headers(driver)
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "has_accepted_requests"
    assert dict(await _ride_row(db, ride_id)) == dict(before)


async def test_patch_blocked_time_change_is_atomic_with_seats(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3, seats_available=2)
    await insert_request(db, ride_id, rider["id"], "accepted")
    before = await _ride_row(db, ride_id)
    resp = await client.patch(
        url(ride_id),
        json={"departure_time": future(99).isoformat(), "seats_total": 6},
        headers=auth_headers(driver),
    )
    assert resp.status_code == 409
    assert dict(await _ride_row(db, ride_id)) == dict(before)


async def test_patch_unchanged_departure_time_with_accepted_requests_is_allowed(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    departure = future(20).replace(microsecond=0)
    ride_id = await insert_ride(
        db, driver["id"], departure_time=departure, seats_total=3, seats_available=2
    )
    await insert_request(db, ride_id, rider["id"], "accepted")
    resp = await client.patch(
        url(ride_id),
        json={"departure_time": departure.isoformat(), "seats_total": 4},
        headers=auth_headers(driver),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["seats_available"] == 3


async def test_patch_seats_below_accepted_is_blocked(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    riders = [await create_user(db) for _ in range(3)]
    ride_id = await insert_ride(db, driver["id"], seats_total=4, seats_available=1)
    for r in riders:
        await insert_request(db, ride_id, r["id"], "accepted")
    before = await _ride_row(db, ride_id)
    resp = await client.patch(url(ride_id), json={"seats_total": 2}, headers=auth_headers(driver))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "seats_below_accepted"
    assert dict(await _ride_row(db, ride_id)) == dict(before)


async def test_patch_cancelled_ride_is_blocked(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], status="cancelled")
    before = await _ride_row(db, ride_id)
    for body in ({"seats_total": 2}, {"departure_time": future(5).isoformat()}, {}):
        resp = await client.patch(url(ride_id), json=body, headers=auth_headers(driver))
        assert resp.status_code == 409, body
        assert resp.json()["error"]["code"] == "ride_cancelled"
    assert dict(await _ride_row(db, ride_id)) == dict(before)


# --- PATCH: Validierung und Autorisierung --------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"seats_total": 0},
        {"seats_total": 9},
        {"seats_total": -3},
        {"seats_total": 2.5},
        {"seats_total": "3"},
        {"seats_total": None},
        {"departure_time": None},
        {"departure_time": "2020-01-01T10:00:00Z"},
        {"departure_time": "2999-01-01T10:00:00"},
        {"departure_time": "bald"},
        {"status": "cancelled"},
        {"seats_available": 1},
        {"seats_total": 2, "driver_id": "x"},
    ],
    ids=lambda b: ",".join(f"{k}={v}" for k, v in b.items()),
)
async def test_patch_validation_errors(
    client: httpx.AsyncClient, db: Any, body: dict[str, Any]
) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    before = await _ride_row(db, ride_id)
    resp = await client.patch(url(ride_id), json=body, headers=auth_headers(driver))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "validation_error"
    assert dict(await _ride_row(db, ride_id)) == dict(before)


async def test_patch_foreign_ride_is_404_and_unchanged(client: httpx.AsyncClient, db: Any) -> None:
    owner = await create_user(db)
    other = await create_user(db)
    ride_id = await insert_ride(db, owner["id"])
    before = await _ride_row(db, ride_id)
    resp = await client.patch(url(ride_id), json={"seats_total": 5}, headers=auth_headers(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert dict(await _ride_row(db, ride_id)) == dict(before)


async def test_patch_unknown_ride_is_404(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    resp = await client.patch(
        url(uuid.uuid4()), json={"seats_total": 5}, headers=auth_headers(user)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patch_malformed_id_is_a_validation_error(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    resp = await client.patch(url("kein-uuid"), json={}, headers=auth_headers(user))
    assert resp.status_code == 422


async def test_patch_and_delete_require_login(client: httpx.AsyncClient, db: Any) -> None:
    owner = await create_user(db)
    ride_id = await insert_ride(db, owner["id"])
    patch = await client.patch(url(ride_id), json={"seats_total": 5})
    delete = await client.delete(url(ride_id))
    assert patch.status_code == 401
    assert delete.status_code == 401
    assert (await _ride_row(db, ride_id))["status"] == "open"


# --- DELETE (Stornierung) ------------------------------------------------------------------------


async def test_delete_cancels_ride_and_active_requests(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    riders = [await create_user(db) for _ in range(4)]
    other_driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=3, seats_available=2)
    other_ride = await insert_ride(db, other_driver["id"])
    r_pending = await insert_request(db, ride_id, riders[0]["id"], "pending")
    r_accepted = await insert_request(db, ride_id, riders[1]["id"], "accepted")
    r_declined = await insert_request(db, ride_id, riders[2]["id"], "declined")
    r_cancelled = await insert_request(db, ride_id, riders[3]["id"], "cancelled")
    r_other = await insert_request(db, other_ride, riders[0]["id"], "pending")
    decided_before = {
        rid: await db.fetchval("SELECT decided_at FROM ride_requests WHERE id = $1", rid)
        for rid in (r_declined, r_cancelled)
    }

    resp = await client.delete(url(ride_id), headers=auth_headers(driver))
    assert resp.status_code == 204
    assert resp.content == b""

    assert (await _ride_row(db, ride_id))["status"] == "cancelled"
    rows = {r["id"]: r for r in await db.fetch("SELECT id, status, decided_at FROM ride_requests")}
    assert rows[r_pending]["status"] == "cancelled" and rows[r_pending]["decided_at"] is not None
    assert rows[r_accepted]["status"] == "cancelled" and rows[r_accepted]["decided_at"] is not None
    assert rows[r_declined]["status"] == "declined"
    assert rows[r_declined]["decided_at"] == decided_before[r_declined]
    assert rows[r_cancelled]["status"] == "cancelled"
    assert rows[r_cancelled]["decided_at"] == decided_before[r_cancelled]
    # Fremde Fahrt und deren Anfrage bleiben unberührt
    assert (await _ride_row(db, other_ride))["status"] == "open"
    assert rows[r_other]["status"] == "pending" and rows[r_other]["decided_at"] is None

    # Die Fahrt ist weiterhin in „meine Fahrten“ sichtbar – als storniert, ohne offene Anfragen
    (ride,) = (await client.get("/api/rides/mine", headers=auth_headers(driver))).json()["items"]
    assert ride["status"] == "cancelled"
    assert ride["pending_requests"] == 0
    assert ride["accepted_requests"] == 0


async def test_delete_is_idempotent(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    headers = auth_headers(driver)
    assert (await client.delete(url(ride_id), headers=headers)).status_code == 204
    assert (await client.delete(url(ride_id), headers=headers)).status_code == 204
    assert (await _ride_row(db, ride_id))["status"] == "cancelled"


async def test_delete_foreign_ride_is_404_and_changes_nothing(
    client: httpx.AsyncClient, db: Any
) -> None:
    owner = await create_user(db)
    other = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, owner["id"])
    request_id = await insert_request(db, ride_id, rider["id"], "pending")
    resp = await client.delete(url(ride_id), headers=auth_headers(other))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert (await _ride_row(db, ride_id))["status"] == "open"
    assert await db.fetchval("SELECT status FROM ride_requests WHERE id = $1", request_id) == (
        "pending"
    )


async def test_delete_unknown_ride_is_404(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    resp = await client.delete(url(uuid.uuid4()), headers=auth_headers(user))
    assert resp.status_code == 404


async def test_a_requester_cannot_cancel_the_ride_of_the_driver(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    await insert_request(db, ride_id, rider["id"], "accepted")
    resp = await client.delete(url(ride_id), headers=auth_headers(rider))
    assert resp.status_code == 404
    assert (await _ride_row(db, ride_id))["status"] == "open"


# --- Sperrreihenfolge: erst die Fahrt, dann die Anfragen -----------------------------------------


async def test_delete_waits_for_the_row_lock_on_the_ride(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    request_id = await insert_request(db, ride_id, rider["id"], "pending")

    async with db.transaction():
        # Eine parallele Transaktion (z. B. Phase 5: accept) hält die Fahrtzeile
        await db.fetchrow("SELECT id FROM rides WHERE id = $1 FOR UPDATE", ride_id)
        task = asyncio.create_task(client.delete(url(ride_id), headers=auth_headers(driver)))
        await asyncio.sleep(0.4)
        assert not task.done(), "DELETE muss auf die Sperre der Fahrtzeile warten"
        await db.execute("UPDATE ride_requests SET status = 'accepted' WHERE id = $1", request_id)
    resp = await asyncio.wait_for(task, timeout=10)
    assert resp.status_code == 204
    # Die zwischenzeitlich angenommene Anfrage wurde mit storniert (kein Lost Update)
    assert await db.fetchval("SELECT status FROM ride_requests WHERE id = $1", request_id) == (
        "cancelled"
    )


async def test_patch_counts_accepted_requests_after_taking_the_ride_lock(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(db, driver["id"], seats_total=2, seats_available=2)
    request_id = await insert_request(db, ride_id, rider["id"], "pending")

    async with db.transaction():
        await db.fetchrow("SELECT id FROM rides WHERE id = $1 FOR UPDATE", ride_id)
        task = asyncio.create_task(
            client.patch(url(ride_id), json={"seats_total": 5}, headers=auth_headers(driver))
        )
        await asyncio.sleep(0.4)
        assert not task.done()
        await db.execute("UPDATE ride_requests SET status = 'accepted' WHERE id = $1", request_id)
        await db.execute("UPDATE rides SET seats_available = 1 WHERE id = $1", ride_id)
    resp = await asyncio.wait_for(task, timeout=10)
    assert resp.status_code == 200, resp.text
    # 5 Plätze minus die erst nach dem Warten sichtbare angenommene Anfrage
    assert resp.json()["seats_available"] == 4
