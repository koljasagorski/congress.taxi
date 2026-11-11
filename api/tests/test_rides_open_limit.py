"""SEC-1: Obergrenze offener Fahrten je Fahrer (409 ``too_many_open_rides``)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.config import Settings
from tests.helpers import create_user, future, insert_ride
from tests.test_match_support import override_settings
from tests.test_rides_common import RIDES, auth_headers, create_body

pytestmark = pytest.mark.asyncio(loop_scope="session")

LIMIT = 3


async def _post(
    client: httpx.AsyncClient, user: dict[str, Any], hours: float = 30
) -> httpx.Response:
    return await client.post(
        RIDES, json=create_body(future(hours).isoformat()), headers=auth_headers(user)
    )


async def _open_rides(db: Any, driver_id: Any) -> int:
    return int(await db.fetchval("SELECT count(*) FROM rides WHERE driver_id = $1", driver_id))


async def test_default_limit_is_five() -> None:
    assert Settings.model_fields["max_open_rides_per_driver"].default == 5


async def test_fourth_open_ride_is_rejected_with_409(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    with override_settings(max_open_rides_per_driver=LIMIT):
        for k in range(LIMIT):
            resp = await _post(client, driver, 30 + k)
            assert resp.status_code == 201, resp.text
        resp = await _post(client, driver, 40)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "too_many_open_rides"
    assert await _open_rides(db, driver["id"]) == LIMIT


async def test_limit_is_per_driver(client: httpx.AsyncClient, db: Any) -> None:
    first = await create_user(db)
    second = await create_user(db)
    with override_settings(max_open_rides_per_driver=LIMIT):
        for k in range(LIMIT):
            assert (await _post(client, first, 30 + k)).status_code == 201
        assert (await _post(client, first, 50)).status_code == 409
        assert (await _post(client, second, 30)).status_code == 201


async def test_cancelled_and_departed_rides_do_not_count(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    await insert_ride(db, driver["id"], status="cancelled", departure_time=future(30))
    await insert_ride(db, driver["id"], departure_time=future(-5))  # bereits abgefahren
    await insert_ride(db, driver["id"], departure_time=future(31))  # zählt
    with override_settings(max_open_rides_per_driver=LIMIT):
        assert (await _post(client, driver, 32)).status_code == 201
        assert (await _post(client, driver, 33)).status_code == 201
        assert (await _post(client, driver, 34)).status_code == 409


async def test_full_rides_count_as_open(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    for k in range(LIMIT):
        await insert_ride(
            db, driver["id"], seats_available=0, status="full", departure_time=future(30 + k)
        )
    with override_settings(max_open_rides_per_driver=LIMIT):
        resp = await _post(client, driver, 40)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "too_many_open_rides"


async def test_cancelling_frees_a_slot(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    with override_settings(max_open_rides_per_driver=LIMIT):
        ids = []
        for k in range(LIMIT):
            resp = await _post(client, driver, 30 + k)
            ids.append(resp.json()["id"])
        assert (await _post(client, driver, 40)).status_code == 409
        deleted = await client.delete(f"{RIDES}/{ids[0]}", headers=auth_headers(driver))
        assert deleted.status_code == 204
        assert (await _post(client, driver, 40)).status_code == 201


async def test_parallel_creates_cannot_exceed_the_limit(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    with override_settings(max_open_rides_per_driver=LIMIT):
        results = await asyncio.gather(*(_post(client, driver, 30 + k) for k in range(8)))
    codes = sorted(r.status_code for r in results)
    assert codes == [201] * LIMIT + [409] * (8 - LIMIT), codes
    assert await _open_rides(db, driver["id"]) == LIMIT


# --- PATCH darf das Limit nicht umgehen (abgefahrene Fahrt in die Zukunft schieben) -------------


async def _patch(client: httpx.AsyncClient, user: dict[str, Any], ride_id: Any, body: dict) -> Any:
    return await client.patch(f"{RIDES}/{ride_id}", json=body, headers=auth_headers(user))


async def _counted_rides(db: Any, driver_id: Any) -> int:
    return int(
        await db.fetchval(
            "SELECT count(*) FROM rides WHERE driver_id = $1 AND status IN ('open', 'full') "
            "AND departure_time > now()",
            driver_id,
        )
    )


async def test_patch_departed_ride_into_future_is_rejected(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    for k in range(LIMIT):
        await insert_ride(db, driver["id"], departure_time=future(30 + k))
    departed = await insert_ride(db, driver["id"], departure_time=future(-5))
    before = await db.fetchval("SELECT departure_time FROM rides WHERE id = $1", departed)
    with override_settings(max_open_rides_per_driver=LIMIT):
        resp = await _patch(client, driver, departed, {"departure_time": future(60).isoformat()})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "ride_departed"
    assert await db.fetchval("SELECT departure_time FROM rides WHERE id = $1", departed) == before
    assert await _counted_rides(db, driver["id"]) == LIMIT


async def test_patch_departed_ride_is_rejected_even_below_the_limit(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    departed = await insert_ride(db, driver["id"], departure_time=future(-5))
    for body in ({"departure_time": future(60).isoformat()}, {"seats_total": 5}, {}):
        resp = await _patch(client, driver, departed, body)
        assert resp.status_code == 409, (body, resp.text)
        assert resp.json()["error"]["code"] == "ride_departed"
    assert await _counted_rides(db, driver["id"]) == 0


async def test_patch_open_ride_at_the_limit_still_works(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ids = [await insert_ride(db, driver["id"], departure_time=future(30 + k)) for k in range(LIMIT)]
    with override_settings(max_open_rides_per_driver=LIMIT):
        resp = await _patch(client, driver, ids[0], {"departure_time": future(70).isoformat()})
    assert resp.status_code == 200, resp.text
    assert await _counted_rides(db, driver["id"]) == LIMIT


async def test_patch_departure_time_uses_the_same_limit_check(
    client: httpx.AsyncClient, db: Any
) -> None:
    # Gemeinsamer Helfer: Die Abfahrtsänderung zählt die übrigen offenen Fahrten wie das Anlegen,
    # auch hier greift 409 too_many_open_rides (hier: Grenze nachträglich gesenkt).
    driver = await create_user(db)
    ids = [await insert_ride(db, driver["id"], departure_time=future(30 + k)) for k in range(LIMIT)]
    with override_settings(max_open_rides_per_driver=LIMIT - 1):
        resp = await _patch(client, driver, ids[0], {"departure_time": future(70).isoformat()})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "too_many_open_rides"
    assert await _counted_rides(db, driver["id"]) == LIMIT
