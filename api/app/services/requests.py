"""Geschäftslogik der Anfragen (Phase 5).

Nebenläufigkeit: Schreibende Übergänge (accept/decline/cancel) sperren **immer zuerst die Fahrt**
(``SELECT … FOR UPDATE`` auf ``rides``) und danach die Anfrage (``ride_requests``) – dieselbe
Reihenfolge wie die Fahrten-Phase, damit keine Deadlocks entstehen. ``seats_available`` wird nur
unter der Fahrt-Sperre verändert. Während OSRM-Aufrufen werden keine Zeilensperren gehalten.

Datenschutz: keine Koordinaten, E-Mails oder Namen in Logs oder Fehlermeldungen.

Standort-Orakel: ``detour_km``/``detour_minutes`` in ``RequestOut`` sind wie bei ``/api/match``
vergröbert (0,5-km-Schritte, ganze Minuten; Hilfsfunktionen aus ``app.services.matching``),
gespeichert werden die exakten Werte. Das ``422 detour_too_large`` entspricht dem Filter
``acceptable`` von ``/api/match`` (gleiche Ja/Nein-Information, kein Mehr). Verbleibende Grenze
(Abtasten über viele Abholpunkte) und Gegenmaßnahmen: PLAN.md, Annahme 18; ein enges Rate-Limit
für ``POST /api/rides/{id}/requests`` folgt in Phase 7.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import RowMapping, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.routing import RoutingClient
from app.schemas.common import Coordinate
from app.schemas.requests import RequestOut, RequestStatus, RiderSummary, RideSummary
from app.services.detour import compute_detour
from app.services.geo import point_wkt, routing_errors, snap_or_error
from app.services.matching import coarse_detour_km, coarse_detour_minutes

_UNIQUE_VIOLATION = "23505"
_FK_VIOLATION = "23503"

# --- Fehler ------------------------------------------------------------------------------------


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Nicht gefunden")


def _invalid_state() -> ApiError:
    return ApiError(409, "invalid_state", "Die Anfrage hat dafür nicht den richtigen Status")


def _ride_full() -> ApiError:
    return ApiError(409, "ride_full", "Die Fahrt ist voll")


def _ride_cancelled() -> ApiError:
    return ApiError(409, "ride_cancelled", "Die Fahrt wurde abgesagt")


def _already_requested() -> ApiError:
    return ApiError(409, "already_requested", "Für diese Fahrt besteht bereits eine aktive Anfrage")


# --- Lesen / Abbilden --------------------------------------------------------------------------

_SELECT_REQUEST = """
SELECT rr.id, rr.ride_id, rr.rider_id, rr.status,
       ST_Y(rr.pickup_geom::geometry) AS pickup_lat,
       ST_X(rr.pickup_geom::geometry) AS pickup_lon,
       rr.detour_distance_m, rr.detour_duration_s, rr.created_at, rr.decided_at,
       r.driver_id, r.departure_time, r.seats_available,
       d.display_name AS driver_display_name, d.email AS driver_email,
       u.display_name AS rider_display_name, u.email AS rider_email
FROM ride_requests rr
JOIN rides r ON r.id = rr.ride_id
JOIN users d ON d.id = r.driver_id
JOIN users u ON u.id = rr.rider_id
"""


def _to_out(row: RowMapping, viewer_id: uuid.UUID) -> RequestOut:
    """Baut ``RequestOut`` aus Sicht von ``viewer_id``.

    ``counterpart_email`` nur bei ``accepted`` und nur für die beiden Beteiligten.
    """
    email: str | None = None
    if row["status"] == "accepted":
        if viewer_id == row["driver_id"]:
            email = row["rider_email"]
        elif viewer_id == row["rider_id"]:
            email = row["driver_email"]
    return RequestOut(
        id=row["id"],
        ride_id=row["ride_id"],
        status=row["status"],
        pickup=Coordinate(lat=row["pickup_lat"], lon=row["pickup_lon"]),
        detour_km=coarse_detour_km(row["detour_distance_m"]),
        detour_minutes=coarse_detour_minutes(row["detour_duration_s"]),
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        ride=RideSummary(
            departure_time=row["departure_time"],
            seats_available=row["seats_available"],
            driver_display_name=row["driver_display_name"],
        ),
        rider=RiderSummary(display_name=row["rider_display_name"]),
        counterpart_email=email,
    )


async def _load_out(
    session: AsyncSession, request_id: uuid.UUID, viewer_id: uuid.UUID
) -> RequestOut:
    result = await session.execute(text(_SELECT_REQUEST + " WHERE rr.id = :id"), {"id": request_id})
    row = result.mappings().first()
    if row is None:  # darf nicht vorkommen (gerade geschrieben/gesperrt)
        raise _not_found()
    return _to_out(row, viewer_id)


async def list_requests(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    direction: str,
    status: RequestStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[RequestOut], int]:
    """``incoming`` = Anfragen an meine Fahrten, ``outgoing`` = meine Anfragen."""
    owner = "r.driver_id" if direction == "incoming" else "rr.rider_id"  # feste Literale
    where = f"{owner} = :uid" + (" AND rr.status = :status" if status else "")
    params: dict[str, Any] = {"uid": user_id}
    if status:
        params["status"] = status
    total = (
        await session.execute(
            text(
                "SELECT count(*) FROM ride_requests rr JOIN rides r ON r.id = rr.ride_id "  # noqa: S608
                f"WHERE {where}"
            ),
            params,
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                text(
                    _SELECT_REQUEST + f" WHERE {where} ORDER BY rr.created_at DESC, rr.id DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": limit, "offset": offset},
            )
        )
        .mappings()
        .all()
    )
    return [_to_out(r, user_id) for r in rows], int(total)


# --- Anfrage anlegen ---------------------------------------------------------------------------


def _check_ride_open(ride: RowMapping, user_id: uuid.UUID) -> None:
    """Reihenfolge: unbekannt/abgesagt → eigene Fahrt → abgefahren → voll."""
    if ride["status"] == "cancelled":
        raise _not_found()
    if ride["driver_id"] == user_id:
        raise ApiError(403, "own_ride", "Die eigene Fahrt kann nicht angefragt werden")
    if ride["departure_time"] <= datetime.now(UTC):
        raise ApiError(409, "ride_departed", "Die Fahrt ist bereits abgefahren")
    if ride["status"] == "full" or ride["seats_available"] < 1:
        raise _ride_full()


async def create_request(
    session: AsyncSession,
    settings: Settings,
    routing: RoutingClient,
    *,
    user_id: uuid.UUID,
    ride_id: uuid.UUID,
    pickup: Coordinate,
) -> RequestOut:
    try:
        return await _create_request(
            session, settings, routing, user_id=user_id, ride_id=ride_id, pickup=pickup
        )
    except Exception:
        await session.rollback()
        raise


async def _create_request(
    session: AsyncSession,
    settings: Settings,
    routing: RoutingClient,
    *,
    user_id: uuid.UUID,
    ride_id: uuid.UUID,
    pickup: Coordinate,
) -> RequestOut:
    ride = (
        (
            await session.execute(
                text(
                    "SELECT driver_id, status, seats_available, departure_time, "
                    "route_distance_m, route_duration_s, "
                    "ST_Y(start_geom::geometry) AS start_lat, "
                    "ST_X(start_geom::geometry) AS start_lon, "
                    "ST_Y(end_geom::geometry) AS end_lat, ST_X(end_geom::geometry) AS end_lon "
                    "FROM rides WHERE id = :id"
                ),
                {"id": ride_id},
            )
        )
        .mappings()
        .first()
    )
    if ride is None:
        raise _not_found()
    _check_ride_open(ride, user_id)

    active = await session.execute(
        text(
            "SELECT 1 FROM ride_requests WHERE ride_id = :rid AND rider_id = :uid "
            "AND status IN ('pending', 'accepted')"
        ),
        {"rid": ride_id, "uid": user_id},
    )
    if active.first() is not None:
        raise _already_requested()

    # Routing ohne offene Zeilensperren; die Transaktion hält bis hierher keine.
    point = pickup.as_latlon()
    await snap_or_error(routing, settings, point, field="pickup")
    with routing_errors():
        detour = await compute_detour(
            routing,
            settings,
            driver_start=(ride["start_lat"], ride["start_lon"]),
            pickup=point,
            event=(ride["end_lat"], ride["end_lon"]),
            base_distance_m=ride["route_distance_m"],
            base_duration_s=ride["route_duration_s"],
        )
    if not detour.acceptable:
        raise ApiError(
            422, "detour_too_large", "Der Umweg für die Fahrerin bzw. den Fahrer wäre zu groß"
        )

    # Zustand der Fahrt unter Lesesperre erneut prüfen (Absage/Vollbelegung während des Routings);
    # FOR SHARE serialisiert gegen Absage (FOR UPDATE), Anfragen untereinander bleiben parallel.
    locked = (
        (
            await session.execute(
                text(
                    "SELECT driver_id, status, seats_available, departure_time "
                    "FROM rides WHERE id = :id FOR SHARE"
                ),
                {"id": ride_id},
            )
        )
        .mappings()
        .first()
    )
    if locked is None:
        raise _not_found()
    _check_ride_open(locked, user_id)

    request_id = uuid.uuid4()
    try:
        await session.execute(
            text(
                "INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, "
                "detour_distance_m, detour_duration_s, status, created_at) VALUES "
                "(:id, :rid, :uid, ST_GeogFromText(:wkt), :dd, :ds, 'pending', clock_timestamp())"
            ),
            {
                "id": request_id,
                "rid": ride_id,
                "uid": user_id,
                "wkt": point_wkt(point),
                "dd": detour.distance_m,
                "ds": detour.duration_s,
            },
        )
    except IntegrityError as exc:
        state = _sqlstate(exc)
        if state == _UNIQUE_VIOLATION:
            raise _already_requested() from None
        if state == _FK_VIOLATION:  # Fahrt/Konto zwischenzeitlich gelöscht
            raise _not_found() from None
        raise
    out = await _load_out(session, request_id, user_id)
    await session.commit()
    return out


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


# --- Übergänge (accept / decline / cancel) -----------------------------------------------------


async def _lock_ride_then_request(
    session: AsyncSession, request_id: uuid.UUID
) -> tuple[RowMapping, RowMapping]:
    """Sperrt Fahrt, danach Anfrage. Gibt (Fahrt, Anfrage) zurück; 404, wenn es sie nicht gibt."""
    pre = (
        await session.execute(
            text("SELECT ride_id FROM ride_requests WHERE id = :id"), {"id": request_id}
        )
    ).first()  # ride_id ist unveränderlich, die Vorab-Lesung braucht keine Sperre
    if pre is None:
        raise _not_found()
    ride = (
        (
            await session.execute(
                text(
                    "SELECT id, driver_id, status, seats_available, seats_total "
                    "FROM rides WHERE id = :id FOR UPDATE"
                ),
                {"id": pre[0]},
            )
        )
        .mappings()
        .first()
    )
    req = (
        (
            await session.execute(
                text("SELECT id, rider_id, status FROM ride_requests WHERE id = :id FOR UPDATE"),
                {"id": request_id},
            )
        )
        .mappings()
        .first()
    )
    if ride is None or req is None:
        raise _not_found()
    return ride, req


async def _set_status(session: AsyncSession, request_id: uuid.UUID, status: str) -> None:
    await session.execute(
        text(
            "UPDATE ride_requests SET status = :status, decided_at = clock_timestamp() "
            "WHERE id = :id"
        ),
        {"status": status, "id": request_id},
    )


async def accept_request(
    session: AsyncSession, *, user_id: uuid.UUID, request_id: uuid.UUID
) -> RequestOut:
    try:
        ride, req = await _lock_ride_then_request(session, request_id)
        if ride["driver_id"] != user_id:
            raise _not_found()
        if ride["status"] == "cancelled":
            raise _ride_cancelled()
        if req["status"] != "pending":
            raise _invalid_state()
        if ride["status"] == "full" or ride["seats_available"] < 1:
            raise _ride_full()
        await session.execute(
            text(
                "UPDATE rides SET seats_available = seats_available - 1, "
                "status = CASE WHEN seats_available - 1 = 0 THEN 'full' ELSE status END "
                "WHERE id = :id"
            ),
            {"id": ride["id"]},
        )
        await _set_status(session, request_id, "accepted")
        out = await _load_out(session, request_id, user_id)
        await session.commit()
        return out
    except Exception:
        await session.rollback()
        raise


async def decline_request(
    session: AsyncSession, *, user_id: uuid.UUID, request_id: uuid.UUID
) -> RequestOut:
    try:
        ride, req = await _lock_ride_then_request(session, request_id)
        if ride["driver_id"] != user_id:
            raise _not_found()
        if req["status"] != "pending":
            raise _invalid_state()
        await _set_status(session, request_id, "declined")
        out = await _load_out(session, request_id, user_id)
        await session.commit()
        return out
    except Exception:
        await session.rollback()
        raise


async def cancel_request(
    session: AsyncSession, *, user_id: uuid.UUID, request_id: uuid.UUID
) -> RequestOut:
    """Mitfahrer: ``pending`` oder ``accepted``; Fahrer: nur ``accepted``."""
    try:
        ride, req = await _lock_ride_then_request(session, request_id)
        is_driver = ride["driver_id"] == user_id
        is_rider = req["rider_id"] == user_id
        if not (is_driver or is_rider):
            raise _not_found()
        allowed = ("accepted",) if is_driver else ("pending", "accepted")
        if req["status"] not in allowed:
            raise _invalid_state()
        if req["status"] == "accepted":
            # Platz zurückgeben; bei abgesagter Fahrt bleibt ihr Status unberührt
            await session.execute(
                text(
                    "UPDATE rides SET seats_available = seats_available + 1, "
                    "status = CASE WHEN status = 'full' THEN 'open' ELSE status END "
                    "WHERE id = :id"
                ),
                {"id": ride["id"]},
            )
        await _set_status(session, request_id, "cancelled")
        out = await _load_out(session, request_id, user_id)
        await session.commit()
        return out
    except Exception:
        await session.rollback()
        raise


# --- Schnittstelle zur Konto-Löschung (Phase 2) -------------------------------------------------


async def release_seats_of_rider(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Einzige Implementierung der Platzrückgabe bei Konto-Löschung (``DELETE /api/me``).

    ``ride_requests.rider_id`` löscht per ``ON DELETE CASCADE``, ohne ``seats_available`` zu
    korrigieren (keine Trigger). Der Aufrufer ruft diese Funktion deshalb **vor** dem Löschen des
    Kontos in derselben Transaktion auf; sie committet nicht. Rückgabe: Zahl der freigegebenen
    ``accepted``-Anfragen.

    Race-Sicherheit gegen accept/cancel/decline (Vertrag: Fahrt vor Anfrage):

    1. Zuerst *alle* Fahrten sperren, auf denen der Nutzer Fahrer ist oder irgendeine Anfrage hat
       (auch ``pending`` – ein gleichzeitiges ``accept`` kann sie noch annehmen), in **einer**
       id-sortierten Anweisung. Eigene Fahrten gehören dazu, damit das anschließende CASCADE-Löschen
       in dieselbe globale Reihenfolge fällt und nicht gegen andere Löschungen deadlockt.
    2. Erst *nach* den Sperren die ``accepted``-Anfragen per ``UPDATE … RETURNING`` auf
       ``cancelled`` setzen. Der Zustand wird so nach dem Warten gelesen; ein vorher gezählter Wert
       wäre durch ein gleichzeitiges cancel/accept veraltet (Doppel- bzw. Fehlgutschrift). Weil
       die Anfrage danach nicht mehr ``accepted`` ist, kann nichts doppelt gutgeschrieben werden.
    3. Je Fahrt wird einmal gutgeschrieben (``LEAST(seats_total, …)``); ``full`` → ``open``.
       Abgesagte Fahrten bleiben unverändert (ihre Plätze zählen nicht mehr).

    Restfenster: Legt der Nutzer parallel mit demselben Token eine *neue* Anfrage an, die der
    Fahrer noch vor dem UPDATE annimmt, ist ihre Fahrt nicht gesperrt; die Gutschrift sperrt sie
    dann erst beim ``UPDATE rides`` (Reihenfolge Anfrage → Fahrt). Der Platz geht trotzdem nicht
    verloren; im theoretischen Deadlock-Fall bricht Postgres eine Transaktion ab (Rollback).
    """
    await session.execute(
        text(
            "SELECT id FROM rides WHERE driver_id = :uid "
            "OR id IN (SELECT ride_id FROM ride_requests WHERE rider_id = :uid) "
            "ORDER BY id FOR UPDATE"
        ),
        {"uid": user_id},
    )
    released = (
        (
            await session.execute(
                text(
                    "UPDATE ride_requests SET status = 'cancelled', decided_at = clock_timestamp() "
                    "WHERE rider_id = :uid AND status = 'accepted' RETURNING ride_id"
                ),
                {"uid": user_id},
            )
        )
        .scalars()
        .all()
    )
    per_ride = Counter(released)
    for ride_id in sorted(per_ride):
        await session.execute(
            text(
                "UPDATE rides SET seats_available = LEAST(seats_total, seats_available + :n), "
                "status = CASE WHEN status = 'full' THEN 'open' ELSE status END "
                "WHERE id = :id AND status <> 'cancelled'"
            ),
            {"n": per_ride[ride_id], "id": ride_id},
        )
    return len(released)
