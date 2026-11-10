"""accept / decline / cancel (Vertrag: Abschnitt „Anfragen“), inkl. Autorisierungsmatrix."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from tests.helpers import auth_cookies, create_user, insert_ride
from tests.test_requests_common import (
    assert_seat_invariant,
    insert_request,
    request_status,
    ride_row,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")

STATUSES = ["pending", "accepted", "declined", "cancelled"]


async def _setup(
    db: Any, *, seats_total: int = 2, status: str = "pending", ride_status: str = "open"
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], uuid.UUID, uuid.UUID]:
    """Fahrer, Mitfahrer, Dritte, Fahrt, Anfrage im gewünschten Status."""
    driver = await create_user(db, display_name="Dora Fahrerin")
    rider = await create_user(db, display_name="Rainer Mitfahrer")
    third = await create_user(db, display_name="Dritte Person")
    ride_id = await insert_ride(db, driver["id"], seats_total=seats_total, status=ride_status)
    request_id = await insert_request(db, ride_id, rider["id"], status=status)
    return driver, rider, third, ride_id, request_id


async def _act(
    client: httpx.AsyncClient, user: dict[str, Any] | None, request_id: uuid.UUID, action: str
) -> httpx.Response:
    client.cookies.clear()
    if user is not None:
        for k, v in auth_cookies(user).items():
            client.cookies.set(k, v)
    return await client.post(f"/api/requests/{request_id}/{action}")


# --- Anmeldung ------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_actions_require_authentication(
    client: httpx.AsyncClient, db: Any, action: str
) -> None:
    _d, _r, _t, _ride, request_id = await _setup(db)
    r = await _act(client, None, request_id, action)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert await request_status(db, request_id) == "pending"


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
async def test_unknown_request_is_404(client: httpx.AsyncClient, db: Any, action: str) -> None:
    user = await create_user(db)
    r = await _act(client, user, uuid.uuid4(), action)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_malformed_request_id_is_validation_error(client: httpx.AsyncClient, db: Any) -> None:
    user = await create_user(db)
    client.cookies.set("access_token", auth_cookies(user)["access_token"])
    r = await client.post("/api/requests/nicht-gueltig/accept")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


# --- Accept ---------------------------------------------------------------------------------


async def test_accept_occupies_a_seat_and_releases_contact(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, rider, _t, ride_id, request_id = await _setup(db, seats_total=3)
    r = await _act(client, driver, request_id, "accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "accepted"
    assert body["id"] == str(request_id)
    assert body["decided_at"] is not None
    assert body["counterpart_email"] == rider["email"]  # Fahrer sieht die E-Mail des Mitfahrers
    assert body["ride"]["seats_available"] == 2
    assert await ride_row(db, ride_id) == {"seats_total": 3, "seats_available": 2, "status": "open"}
    await assert_seat_invariant(db)


async def test_accepting_the_last_seat_marks_ride_full(client: httpx.AsyncClient, db: Any) -> None:
    driver, _rider, _t, ride_id, request_id = await _setup(db, seats_total=1)
    assert (await _act(client, driver, request_id, "accept")).status_code == 200
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 0, "status": "full"}
    await assert_seat_invariant(db)


async def test_accept_on_full_ride_is_409_ride_full_and_request_stays_pending(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, _rider, _t, ride_id, first = await _setup(db, seats_total=1)
    other = await create_user(db)
    second = await insert_request(db, ride_id, other["id"])
    assert (await _act(client, driver, first, "accept")).status_code == 200
    r = await _act(client, driver, second, "accept")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_full"
    assert await request_status(db, second) == "pending"
    assert (await ride_row(db, ride_id))["seats_available"] == 0
    await assert_seat_invariant(db)


async def test_accept_on_cancelled_ride_is_409_ride_cancelled(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, _rider, _t, ride_id, request_id = await _setup(db, ride_status="cancelled")
    r = await _act(client, driver, request_id, "accept")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_cancelled"
    assert await request_status(db, request_id) == "pending"
    assert (await ride_row(db, ride_id))["seats_available"] == 2


@pytest.mark.parametrize("status", ["accepted", "declined", "cancelled"])
async def test_accept_of_non_pending_request_is_409_invalid_state(
    client: httpx.AsyncClient, db: Any, status: str
) -> None:
    driver, _rider, _t, ride_id, request_id = await _setup(db, status=status)
    before = await ride_row(db, ride_id)
    r = await _act(client, driver, request_id, "accept")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state"
    assert await request_status(db, request_id) == status
    assert await ride_row(db, ride_id) == before


async def test_same_transition_twice_is_invalid_state_and_changes_nothing(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, _rider, _t, ride_id, request_id = await _setup(db, seats_total=3)
    assert (await _act(client, driver, request_id, "accept")).status_code == 200
    again = await _act(client, driver, request_id, "accept")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "invalid_state"
    assert (await ride_row(db, ride_id))["seats_available"] == 2  # nur einmal dekrementiert
    await assert_seat_invariant(db)


# --- Decline --------------------------------------------------------------------------------


async def test_decline_sets_status_without_touching_seats(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, rider, _t, ride_id, request_id = await _setup(db)
    r = await _act(client, driver, request_id, "decline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "declined"
    assert body["decided_at"] is not None
    assert body["counterpart_email"] is None
    assert rider["email"] not in r.text
    assert (await ride_row(db, ride_id))["seats_available"] == 2
    again = await _act(client, driver, request_id, "decline")
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"


async def test_new_request_possible_again_after_decline(client: httpx.AsyncClient, db: Any) -> None:
    driver, rider, _t, _ride_id, request_id = await _setup(db)
    assert (await _act(client, driver, request_id, "decline")).status_code == 200
    assert (
        await db.fetchval(
            "SELECT count(*) FROM ride_requests WHERE rider_id = $1 AND status IN "
            "('pending','accepted')",
            rider["id"],
        )
        == 0
    )


# --- Cancel ---------------------------------------------------------------------------------


async def test_rider_cancels_pending_request_seats_unchanged(
    client: httpx.AsyncClient, db: Any
) -> None:
    _d, rider, _t, ride_id, request_id = await _setup(db)
    r = await _act(client, rider, request_id, "cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert r.json()["decided_at"] is not None
    assert r.json()["counterpart_email"] is None
    assert (await ride_row(db, ride_id))["seats_available"] == 2
    await assert_seat_invariant(db)


@pytest.mark.parametrize("canceller", ["rider", "driver"])
async def test_cancelling_accepted_request_returns_seat_and_reopens_ride(
    client: httpx.AsyncClient, db: Any, canceller: str
) -> None:
    driver, rider, _t, ride_id, request_id = await _setup(db, seats_total=1, status="accepted")
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 0, "status": "full"}
    r = await _act(client, driver if canceller == "driver" else rider, request_id, "cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert r.json()["counterpart_email"] is None  # nach dem Stornieren keine Freigabe mehr
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 1, "status": "open"}
    await assert_seat_invariant(db)
    again = await _act(client, rider, request_id, "cancel")
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state"
    assert (await ride_row(db, ride_id))["seats_available"] == 1  # nicht doppelt zurückgegeben


async def test_cancel_accepted_on_partially_booked_ride_keeps_it_open(
    client: httpx.AsyncClient, db: Any
) -> None:
    _d, rider, _t, ride_id, request_id = await _setup(db, seats_total=3, status="accepted")
    assert (await _act(client, rider, request_id, "cancel")).status_code == 200
    assert await ride_row(db, ride_id) == {"seats_total": 3, "seats_available": 3, "status": "open"}


async def test_driver_cannot_cancel_pending_request(client: httpx.AsyncClient, db: Any) -> None:
    driver, _r, _t, _ride, request_id = await _setup(db)
    r = await _act(client, driver, request_id, "cancel")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state"
    assert await request_status(db, request_id) == "pending"


# --- Autorisierungsmatrix: Rolle x Aktion x Status ------------------------------------------


def _expected(role: str, action: str, status: str) -> tuple[int, str | None]:
    """(HTTP-Status, Fehlercode oder Zielstatus) laut Vertrag."""
    if role == "anonymous":
        return 401, "unauthorized"
    if role == "third":
        return 404, "not_found"
    if action in ("accept", "decline"):
        if role == "rider":
            return 404, "not_found"  # fremde Rolle: Existenz nicht verraten
        return (
            (200, "accepted" if action == "accept" else "declined")
            if status == "pending"
            else (
                409,
                "invalid_state",
            )
        )
    # cancel
    allowed = {"pending", "accepted"} if role == "rider" else {"accepted"}
    return (200, "cancelled") if status in allowed else (409, "invalid_state")


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
@pytest.mark.parametrize("role", ["driver", "rider", "third", "anonymous"])
async def test_authorization_matrix(
    client: httpx.AsyncClient, db: Any, role: str, action: str, status: str
) -> None:
    driver, rider, third, _ride_id, request_id = await _setup(db, seats_total=2, status=status)
    actor = {"driver": driver, "rider": rider, "third": third, "anonymous": None}[role]
    expected_http, expected_value = _expected(role, action, status)

    r = await _act(client, actor, request_id, action)

    assert r.status_code == expected_http, (role, action, status, r.text)
    if expected_http == 200:
        assert r.json()["status"] == expected_value
    else:
        assert r.json()["error"]["code"] == expected_value
        # Fehlschläge ändern nichts
        assert await request_status(db, request_id) == status
    await assert_seat_invariant(db)
    # Fremde erfahren nichts: weder E-Mails noch Namen
    if role == "third":
        assert driver["email"] not in r.text and rider["email"] not in r.text


async def test_logs_contain_no_personal_data(
    client: httpx.AsyncClient, db: Any, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    driver, rider, _t, _ride, request_id = await _setup(db)
    with caplog.at_level(logging.DEBUG):
        assert (await _act(client, driver, request_id, "accept")).status_code == 200
        assert (await _act(client, driver, request_id, "accept")).status_code == 409
        assert (await _act(client, rider, request_id, "cancel")).status_code == 200
    logged = caplog.text
    for secret in (driver["email"], rider["email"], "Dora", "Rainer", "53.65", "9.97"):
        assert secret not in logged


# --- Prüf-Reihenfolge beim Annehmen: ride_cancelled → invalid_state → ride_full --------------


async def test_precedence_accept_cancelled_ride_beats_invalid_state(
    client: httpx.AsyncClient, db: Any
) -> None:
    driver, _rider, _t, _ride, request_id = await _setup(
        db, status="declined", ride_status="cancelled"
    )
    r = await _act(client, driver, request_id, "accept")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ride_cancelled"
    assert await request_status(db, request_id) == "declined"


async def test_precedence_accept_invalid_state_beats_ride_full(
    client: httpx.AsyncClient, db: Any
) -> None:
    # Fahrt voll (der einzige Platz ist durch die Anfrage selbst belegt), Anfrage nicht pending
    driver, _rider, _t, ride_id, request_id = await _setup(db, seats_total=1, status="accepted")
    assert (await ride_row(db, ride_id))["status"] == "full"
    r = await _act(client, driver, request_id, "accept")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_state"
    assert await ride_row(db, ride_id) == {"seats_total": 1, "seats_available": 0, "status": "full"}


async def test_precedence_accept_by_non_driver_is_404_even_on_cancelled_ride(
    client: httpx.AsyncClient, db: Any
) -> None:
    _driver, rider, third, _ride, request_id = await _setup(db, ride_status="cancelled")
    for stranger in (rider, third):
        r = await _act(client, stranger, request_id, "accept")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
