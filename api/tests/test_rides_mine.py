"""GET /api/rides/mine – Pagination, Sortierung, Isolation, Zählwerte, Routenvereinfachung."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.helpers import EVENT, NORTH, create_user, future, insert_ride
from tests.test_rides_common import ALL_RIDE_KEYS, auth_headers, insert_request

pytestmark = pytest.mark.asyncio(loop_scope="session")

MINE = "/api/rides/mine"


async def test_requires_login(client: httpx.AsyncClient) -> None:
    resp = await client.get(MINE)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_empty_list(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    resp = await client.get(MINE, headers=auth_headers(user))
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_sorted_newest_departure_first_with_pagination(
    client: httpx.AsyncClient, db: Any
) -> None:
    user = await create_user(db)
    times = [future(h) for h in (10, 50, 30, 20, 40)]
    ids = {}
    for t in times:
        ids[t] = await insert_ride(db, user["id"], departure_time=t)
    expected = [str(ids[t]) for t in sorted(times, reverse=True)]

    headers = auth_headers(user)
    full = (await client.get(MINE, headers=headers)).json()
    assert [r["id"] for r in full["items"]] == expected
    assert full["total"] == 5

    page1 = (await client.get(MINE, params={"limit": 2}, headers=headers)).json()
    page2 = (await client.get(MINE, params={"limit": 2, "offset": 2}, headers=headers)).json()
    page3 = (await client.get(MINE, params={"limit": 2, "offset": 4}, headers=headers)).json()
    assert [r["id"] for r in page1["items"]] == expected[0:2]
    assert [r["id"] for r in page2["items"]] == expected[2:4]
    assert [r["id"] for r in page3["items"]] == expected[4:5]
    assert (page1["total"], page1["limit"], page1["offset"]) == (5, 2, 0)
    assert (page2["total"], page2["limit"], page2["offset"]) == (5, 2, 2)
    beyond = (await client.get(MINE, params={"offset": 10}, headers=headers)).json()
    assert beyond["items"] == [] and beyond["total"] == 5


async def test_only_own_rides_are_listed(client: httpx.AsyncClient, db: Any) -> None:
    alice = await create_user(db)
    bob = await create_user(db)
    a1 = await insert_ride(db, alice["id"])
    a2 = await insert_ride(db, alice["id"], departure_time=future(5))
    b1 = await insert_ride(db, bob["id"])

    alice_page = (await client.get(MINE, headers=auth_headers(alice))).json()
    bob_page = (await client.get(MINE, headers=auth_headers(bob))).json()
    assert {r["id"] for r in alice_page["items"]} == {str(a1), str(a2)}
    assert alice_page["total"] == 2
    assert [r["id"] for r in bob_page["items"]] == [str(b1)]
    assert bob_page["total"] == 1


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": "abc"},
        {"offset": -1},
        {"offset": "x"},
    ],
)
async def test_invalid_pagination_params(
    client: httpx.AsyncClient, db: Any, params: dict[str, Any]
) -> None:
    user = await create_user(db)
    resp = await client.get(MINE, params=params, headers=auth_headers(user))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_limit_boundaries(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    await insert_ride(db, user["id"])
    for limit in (1, 100):
        resp = await client.get(MINE, params={"limit": limit}, headers=auth_headers(user))
        assert resp.status_code == 200
        assert resp.json()["limit"] == limit


async def test_ride_shape_values_and_request_counts(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    riders = [await create_user(db) for _ in range(4)]
    departure = future(12)
    ride_id = await insert_ride(
        db,
        driver["id"],
        start=NORTH,
        end=EVENT,
        distance_m=30_149.0,
        duration_s=1_830.0,
        seats_total=4,
        seats_available=3,
        departure_time=departure,
    )
    await insert_request(db, ride_id, riders[0]["id"], "pending")
    await insert_request(db, ride_id, riders[1]["id"], "pending")
    await insert_request(db, ride_id, riders[2]["id"], "accepted")
    await insert_request(db, ride_id, riders[3]["id"], "declined")

    resp = await client.get(MINE, headers=auth_headers(driver))
    assert resp.status_code == 200
    (ride,) = resp.json()["items"]
    assert set(ride) == ALL_RIDE_KEYS
    assert ride["id"] == str(ride_id)
    assert ride["start"] == {"lat": NORTH[0], "lon": NORTH[1]}
    assert ride["end"] == {"lat": EVENT[0], "lon": EVENT[1]}
    assert (ride["seats_total"], ride["seats_available"], ride["status"]) == (4, 3, "open")
    assert ride["distance_km"] == pytest.approx(30.1, abs=0.051)
    assert ride["duration_min"] == pytest.approx(30.5, abs=0.051)
    assert ride["pending_requests"] == 2
    assert ride["accepted_requests"] == 1
    assert ride["route"][0] == [pytest.approx(NORTH[0]), pytest.approx(NORTH[1])]
    assert ride["route"][-1] == [pytest.approx(EVENT[0]), pytest.approx(EVENT[1])]


async def test_cancelled_rides_are_listed_with_status(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    await insert_ride(db, user["id"], status="cancelled")
    (ride,) = (await client.get(MINE, headers=auth_headers(user))).json()["items"]
    assert ride["status"] == "cancelled"


async def test_request_counts_are_per_ride(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    r1 = await insert_ride(db, driver["id"], departure_time=future(1))
    r2 = await insert_ride(db, driver["id"], departure_time=future(2))
    await insert_request(db, r2, rider["id"], "pending")
    items = (await client.get(MINE, headers=auth_headers(driver))).json()["items"]
    by_id = {r["id"]: r for r in items}
    assert by_id[str(r1)]["pending_requests"] == 0
    assert by_id[str(r2)]["pending_requests"] == 1


async def test_route_is_simplified_but_keeps_endpoints(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    # 200 fast kollineare Punkte (Abweichung ≪ Toleranz) → Vereinfachung auf wenige Punkte
    n = 200
    route = [
        (
            NORTH[0] + (EVENT[0] - NORTH[0]) * i / n,
            NORTH[1] + (EVENT[1] - NORTH[1]) * i / n + (0.00001 if i % 2 else 0.0),
        )
        for i in range(n + 1)
    ]
    await insert_ride(db, user["id"], start=NORTH, end=EVENT, route=route)
    (ride,) = (await client.get(MINE, headers=auth_headers(user))).json()["items"]
    assert 2 <= len(ride["route"]) < 20
    assert ride["route"][0] == [
        pytest.approx(NORTH[0], abs=1e-5),
        pytest.approx(NORTH[1], abs=1e-5),
    ]
    assert ride["route"][-1] == [
        pytest.approx(EVENT[0], abs=1e-5),
        pytest.approx(EVENT[1], abs=1e-5),
    ]


async def test_very_short_route_is_still_returned(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    await insert_ride(db, user["id"], start=EVENT, end=EVENT, route=[EVENT, EVENT])
    (ride,) = (await client.get(MINE, headers=auth_headers(user))).json()["items"]
    assert len(ride["route"]) >= 2


async def test_equal_departure_times_have_a_stable_order(
    client: httpx.AsyncClient, db: Any
) -> None:
    user = await create_user(db)
    same = future(8)
    for _ in range(4):
        await insert_ride(db, user["id"], departure_time=same)
    headers = auth_headers(user)
    first = [r["id"] for r in (await client.get(MINE, headers=headers)).json()["items"]]
    paged: list[str] = []
    for offset in range(4):
        page = (
            await client.get(MINE, params={"limit": 1, "offset": offset}, headers=headers)
        ).json()
        paged += [r["id"] for r in page["items"]]
    assert paged == first
    assert len(set(first)) == 4
