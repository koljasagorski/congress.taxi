"""GET /api/requests/incoming|outgoing: Filter, Pagination, Sichtbarkeit, E-Mail-Freigabe."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from tests.helpers import auth_cookies, create_user, insert_ride
from tests.test_requests_common import PICKUP, insert_request

pytestmark = pytest.mark.asyncio(loop_scope="session")

STATUSES = ["pending", "accepted", "declined", "cancelled"]


async def _get(
    client: httpx.AsyncClient,
    user: dict[str, Any] | None,
    path: str,
    **params: Any,
) -> httpx.Response:
    client.cookies.clear()
    if user is not None:
        for k, v in auth_cookies(user).items():
            client.cookies.set(k, v)
    return await client.get(path, params=params)


@pytest.mark.parametrize("path", ["/api/requests/incoming", "/api/requests/outgoing"])
async def test_lists_require_authentication(client: httpx.AsyncClient, path: str) -> None:
    r = await _get(client, None, path)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("path", ["/api/requests/incoming", "/api/requests/outgoing"])
async def test_empty_list_has_page_shape(client: httpx.AsyncClient, db: Any, path: str) -> None:
    user = await create_user(db)
    r = await _get(client, user, path)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


@pytest.mark.parametrize(
    "params",
    [
        {"status": "bogus"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"limit": "x"},
    ],
)
@pytest.mark.parametrize("path", ["/api/requests/incoming", "/api/requests/outgoing"])
async def test_invalid_query_is_422(
    client: httpx.AsyncClient, db: Any, path: str, params: dict[str, Any]
) -> None:
    user = await create_user(db)
    r = await _get(client, user, path, **params)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


async def test_incoming_and_outgoing_only_show_own_data(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db, display_name="Dora Fahrerin")
    rider = await create_user(db, display_name="Rainer Mitfahrer")
    other_driver = await create_user(db)
    other_rider = await create_user(db)
    ride = await insert_ride(db, driver["id"])
    other_ride = await insert_ride(db, other_driver["id"])
    mine = await insert_request(db, ride, rider["id"])
    await insert_request(db, ride, other_rider["id"])
    await insert_request(db, other_ride, other_rider["id"])

    inc = (await _get(client, driver, "/api/requests/incoming")).json()
    assert inc["total"] == 2
    assert {i["ride_id"] for i in inc["items"]} == {str(ride)}

    out = (await _get(client, rider, "/api/requests/outgoing")).json()
    assert [i["id"] for i in out["items"]] == [str(mine)]
    assert out["items"][0]["ride"]["driver_display_name"] == "Dora Fahrerin"
    assert out["items"][0]["rider"]["display_name"] == "Rainer Mitfahrer"

    # Rollen sind getrennt: Fahrer hat nichts "outgoing", Mitfahrer nichts "incoming"
    assert (await _get(client, driver, "/api/requests/outgoing")).json()["total"] == 0
    assert (await _get(client, rider, "/api/requests/incoming")).json()["total"] == 0
    # Dritte sehen nichts
    stranger = await create_user(db)
    for path in ("/api/requests/incoming", "/api/requests/outgoing"):
        assert (await _get(client, stranger, path)).json()["total"] == 0


async def test_pickup_and_detour_visible_to_driver_and_rider_only(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    stranger = await create_user(db)
    ride = await insert_ride(db, driver["id"])
    await insert_request(db, ride, rider["id"], detour_distance_m=4_500, detour_duration_s=330)
    for user, path in ((driver, "incoming"), (rider, "outgoing")):
        item = (await _get(client, user, f"/api/requests/{path}")).json()["items"][0]
        assert item["pickup"] == {"lat": pytest.approx(PICKUP[0]), "lon": pytest.approx(PICKUP[1])}
        assert item["detour_km"] == 4.5
        assert item["detour_minutes"] == 6  # 330 s = 5,5 min -> ganze Minuten (kaufm.)
    for path in ("incoming", "outgoing"):
        r = await _get(client, stranger, f"/api/requests/{path}")
        assert str(PICKUP[0]) not in r.text


async def test_status_filter(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride = await insert_ride(db, driver["id"], seats_total=8)
    riders = [await create_user(db) for _ in STATUSES]
    ids = {
        s: await insert_request(db, ride, u["id"], status=s)
        for s, u in zip(STATUSES, riders, strict=True)
    }
    for s in STATUSES:
        inc = (await _get(client, driver, "/api/requests/incoming", status=s)).json()
        assert [i["id"] for i in inc["items"]] == [str(ids[s])]
        assert inc["total"] == 1
        assert inc["items"][0]["status"] == s
    assert (await _get(client, driver, "/api/requests/incoming")).json()["total"] == 4
    out = await _get(client, riders[1], "/api/requests/outgoing", status="pending")
    assert out.json()["total"] == 0  # Filter greift auch outgoing


async def test_ordering_and_pagination(client: httpx.AsyncClient, db: Any) -> None:
    driver = await create_user(db)
    ride = await insert_ride(db, driver["id"], seats_total=8)
    base = datetime.now(UTC) - timedelta(hours=1)
    ids: list[uuid.UUID] = []
    for i in range(5):
        rider = await create_user(db)
        ids.append(
            await insert_request(db, ride, rider["id"], created_at=base + timedelta(minutes=i))
        )
    expected = [str(x) for x in reversed(ids)]  # neueste zuerst

    full = (await _get(client, driver, "/api/requests/incoming")).json()
    assert [i["id"] for i in full["items"]] == expected
    assert full["total"] == 5

    p1 = (await _get(client, driver, "/api/requests/incoming", limit=2, offset=0)).json()
    p2 = (await _get(client, driver, "/api/requests/incoming", limit=2, offset=2)).json()
    p3 = (await _get(client, driver, "/api/requests/incoming", limit=2, offset=4)).json()
    p4 = (await _get(client, driver, "/api/requests/incoming", limit=2, offset=6)).json()
    assert [i["id"] for i in p1["items"] + p2["items"] + p3["items"]] == expected
    assert (p1["total"], p1["limit"], p1["offset"]) == (5, 2, 0)
    assert p4["items"] == [] and p4["total"] == 5


# --- E-Mail-Freigabe: Rolle x Status x Endpunkt ---------------------------------------------


@pytest.mark.parametrize("status", STATUSES)
async def test_counterpart_email_matrix(client: httpx.AsyncClient, db: Any, status: str) -> None:
    driver = await create_user(db, display_name="Dora Fahrerin")
    rider = await create_user(db, display_name="Rainer Mitfahrer")
    stranger = await create_user(db)
    ride = await insert_ride(db, driver["id"], seats_total=2)
    request_id = await insert_request(db, ride, rider["id"], status=status)

    # Fahrer sieht (nur bei accepted) die E-Mail des Mitfahrers – und nie seine eigene
    inc = await _get(client, driver, "/api/requests/incoming")
    item = inc.json()["items"][0]
    assert item["id"] == str(request_id)
    assert item["counterpart_email"] == (rider["email"] if status == "accepted" else None)
    assert driver["email"] not in inc.text
    if status != "accepted":
        assert rider["email"] not in inc.text

    # Mitfahrer sieht (nur bei accepted) die E-Mail des Fahrers
    out = await _get(client, rider, "/api/requests/outgoing")
    item = out.json()["items"][0]
    assert item["counterpart_email"] == (driver["email"] if status == "accepted" else None)
    assert rider["email"] not in out.text
    if status != "accepted":
        assert driver["email"] not in out.text

    # Dritte: weder in Listen noch irgendwo anders E-Mails
    for path in ("incoming", "outgoing"):
        r = await _get(client, stranger, f"/api/requests/{path}")
        assert driver["email"] not in r.text and rider["email"] not in r.text

    # Aktionen von Dritten verraten nichts
    r = await client.post(f"/api/requests/{request_id}/cancel")
    assert r.status_code == 404
    assert driver["email"] not in r.text and rider["email"] not in r.text


async def test_email_released_only_after_accept_in_action_responses(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride = await insert_ride(db, driver["id"], seats_total=2)
    request_id = await insert_request(db, ride, rider["id"])

    # vor Annahme: Mitfahrer-Sicht (Liste) ohne E-Mail
    out = await _get(client, rider, "/api/requests/outgoing")
    assert out.json()["items"][0]["counterpart_email"] is None

    # Annahme durch den Fahrer: Antwort enthält die Mitfahrer-E-Mail, nicht die eigene
    client.cookies.clear()
    for k, v in auth_cookies(driver).items():
        client.cookies.set(k, v)
    acc = await client.post(f"/api/requests/{request_id}/accept")
    assert acc.status_code == 200
    assert acc.json()["counterpart_email"] == rider["email"]
    assert driver["email"] not in acc.text

    # danach sieht der Mitfahrer die Fahrer-E-Mail
    out = await _get(client, rider, "/api/requests/outgoing")
    assert out.json()["items"][0]["counterpart_email"] == driver["email"]

    # nach Stornierung wieder nichts
    cancel = await client.post(f"/api/requests/{request_id}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["counterpart_email"] is None
    out = await _get(client, rider, "/api/requests/outgoing")
    assert out.json()["items"][0]["counterpart_email"] is None
    assert driver["email"] not in out.text


async def test_cancelled_ride_shows_in_lists_without_contact_data(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver = await create_user(db)
    rider = await create_user(db)
    ride = await insert_ride(db, driver["id"], status="cancelled")
    await insert_request(db, ride, rider["id"], status="cancelled")
    out = await _get(client, rider, "/api/requests/outgoing")
    assert out.json()["total"] == 1
    assert out.json()["items"][0]["counterpart_email"] is None
    assert driver["email"] not in out.text
