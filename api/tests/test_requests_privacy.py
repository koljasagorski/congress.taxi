"""Datenschutz der Anfragen: Umweg-Orakel (grobe Werte) und Kontaktfreigabe (E-Mail).

* Antworten geben den Umweg exakt so grob aus wie ``/api/match`` (0,5-km-Schritte, ganze Minuten,
  dieselben Hilfsfunktionen); gespeichert werden die exakten Werte, Filter rechnen exakt.
* Die E-Mail der Gegenseite erscheint erst nach dem Annehmen (``accepted``) und nur für die beiden
  Beteiligten – in keiner Antwort vorher, für Dritte nie.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from app.services.matching import coarse_detour_km, coarse_detour_minutes
from tests.fakes import FakeRouting
from tests.helpers import auth_cookies, create_user, insert_ride
from tests.test_requests_common import (
    BASE_DISTANCE_M,
    BASE_DURATION_S,
    insert_request,
    pickup_json,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _call(
    client: httpx.AsyncClient, user: dict[str, Any] | None, method: str, path: str, **kw: Any
) -> httpx.Response:
    client.cookies.clear()
    if user is not None:
        for k, v in auth_cookies(user).items():
            client.cookies.set(k, v)
    return await client.request(method, path, **kw)


@pytest.mark.parametrize(
    ("extra_m", "extra_s"),
    [(1_240.0, 29.0), (1_260.0, 31.0), (3_333.0, 337.0), (4_749.0, 89.0), (0.0, 0.0)],
)
async def test_every_response_shows_the_same_coarse_detour_as_match(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting, extra_m: float, extra_s: float
) -> None:
    routing.route_override = lambda _wp: (BASE_DISTANCE_M + extra_m, BASE_DURATION_S + extra_s)
    driver = await create_user(db)
    rider = await create_user(db)
    ride_id = await insert_ride(
        db, driver["id"], distance_m=BASE_DISTANCE_M, duration_s=BASE_DURATION_S
    )
    want_km, want_min = coarse_detour_km(extra_m), coarse_detour_minutes(extra_s)

    created = await _call(
        client, rider, "POST", f"/api/rides/{ride_id}/requests", json=pickup_json()
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    # exakte Werte bleiben intern gespeichert, nach außen geht nur die grobe Fassung
    stored = await db.fetchrow(
        "SELECT detour_distance_m, detour_duration_s FROM ride_requests WHERE id = $1",
        uuid.UUID(request_id),
    )
    assert stored["detour_distance_m"] == pytest.approx(extra_m)
    assert stored["detour_duration_s"] == pytest.approx(extra_s)

    responses = [
        created,
        await _call(client, rider, "GET", "/api/requests/outgoing"),
        await _call(client, driver, "GET", "/api/requests/incoming"),
        await _call(client, driver, "POST", f"/api/requests/{request_id}/accept"),
        await _call(client, rider, "GET", "/api/requests/outgoing"),
        await _call(client, rider, "POST", f"/api/requests/{request_id}/cancel"),
    ]
    for r in responses:
        assert r.status_code == 200 or r is created, r.text
        body = r.json()
        for item in body.get("items", [body]):
            assert item["detour_km"] == want_km
            assert item["detour_minutes"] == want_min
            assert isinstance(item["detour_minutes"], int)
            assert (item["detour_km"] * 2).is_integer()  # Vielfaches von 0,5


async def test_email_is_released_only_after_accept_and_only_to_both_parties(
    client: httpx.AsyncClient, db: Any, routing: FakeRouting
) -> None:
    routing.route_override = lambda _wp: (BASE_DISTANCE_M + 2_000.0, BASE_DURATION_S + 120.0)
    driver = await create_user(db, email="fahrerin-privat@example.org")
    rider = await create_user(db, email="mitfahrer-privat@example.org")
    third = await create_user(db, email="dritte-privat@example.org")
    ride_id = await insert_ride(
        db, driver["id"], distance_m=BASE_DISTANCE_M, duration_s=BASE_DURATION_S
    )
    mails = (driver["email"], rider["email"], third["email"])

    def assert_no_mail(r: httpx.Response, *, allow: tuple[str, ...] = ()) -> None:
        for mail in mails:
            if mail not in allow:
                assert mail not in r.text, (mail, r.text)

    # Anfrage stellen: noch keine Freigabe, weder in der Antwort noch in den Listen
    created = await _call(
        client, rider, "POST", f"/api/rides/{ride_id}/requests", json=pickup_json()
    )
    assert created.status_code == 201
    assert_no_mail(created)
    request_id = created.json()["id"]
    for who, path in ((driver, "incoming"), (rider, "outgoing"), (third, "incoming")):
        assert_no_mail(await _call(client, who, "GET", f"/api/requests/{path}"))

    # Unbeteiligte können nichts auslösen oder abrufen
    for action in ("accept", "decline", "cancel"):
        r = await _call(client, third, "POST", f"/api/requests/{request_id}/{action}")
        assert r.status_code == 404
        assert_no_mail(r)
    # Der Mitfahrer kann nicht selbst annehmen (und dadurch keine Freigabe erzwingen)
    forced = await _call(client, rider, "POST", f"/api/requests/{request_id}/accept")
    assert forced.status_code == 404
    assert_no_mail(forced)

    # Fahrer nimmt an: erst jetzt sehen genau die beiden Beteiligten die E-Mail der Gegenseite
    accepted = await _call(client, driver, "POST", f"/api/requests/{request_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["counterpart_email"] == rider["email"]
    assert_no_mail(accepted, allow=(rider["email"],))
    mine = await _call(client, rider, "GET", "/api/requests/outgoing")
    assert mine.json()["items"][0]["counterpart_email"] == driver["email"]
    assert_no_mail(mine, allow=(driver["email"],))
    assert_no_mail(await _call(client, third, "GET", "/api/requests/incoming"))
    assert_no_mail(await _call(client, third, "GET", "/api/requests/outgoing"))

    # Nach dem Stornieren ist die Freigabe wieder weg
    cancelled = await _call(client, rider, "POST", f"/api/requests/{request_id}/cancel")
    assert cancelled.status_code == 200
    assert_no_mail(cancelled)
    assert_no_mail(await _call(client, driver, "GET", "/api/requests/incoming"))


async def test_pending_and_declined_requests_never_expose_email_in_raw_sql_rows(
    client: httpx.AsyncClient, db: Any
) -> None:
    """Auch bei Dritten, die eine fremde Anfrage-ID raten, kommt nichts zurück (404 statt Daten)."""
    driver = await create_user(db)
    rider = await create_user(db)
    third = await create_user(db)
    ride_id = await insert_ride(db, driver["id"])
    for status in ("pending", "declined"):
        request_id = await insert_request(db, ride_id, rider["id"], status=status)
        r = await _call(client, third, "POST", f"/api/requests/{request_id}/cancel")
        assert r.status_code == 404
        assert driver["email"] not in r.text and rider["email"] not in r.text
