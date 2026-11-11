"""Fahrten: anlegen (inkl. OSRM-Route), auflisten, ändern, stornieren.

Regeln stehen in docs/API-CONTRACT.md (Abschnitt „Fahrten“). Wichtig für die Nebenläufigkeit:
Schreibende Operationen sperren **zuerst die Fahrtzeile** (``SELECT … FOR UPDATE``) und erst danach
Anfragen – dieselbe Reihenfolge wie Phase 5, damit keine Deadlocks entstehen. Einzige Ausnahme: Wer
die Abfahrtszeit ändert (PATCH), sperrt vorher die Nutzerzeile (Obergrenze offener Fahrten, SEC-1),
wie es auch das Löschen eines Kontos tut (Nutzer, dann kaskadierend die Fahrten).

Datenschutz: Dieses Modul loggt nichts (keine Koordinaten, IDs oder Zeiten).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NamedTuple

from geoalchemy2 import Geometry
from sqlalchemy import ColumnElement, Row, cast, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Ride, RideRequest, User
from app.routing import LatLon, RoutingClient
from app.schemas.common import Coordinate, Page
from app.schemas.rides import RideCreate, RideOut, RidePatch
from app.services.geo import (
    geojson_linestring_to_latlon,
    routing_errors,
    simplified_route_geojson,
    snap_or_error,
)

_NOT_FOUND = "Fahrt nicht gefunden"


def _not_found() -> ApiError:
    return ApiError(404, "not_found", _NOT_FOUND)


# --- Geometrie -----------------------------------------------------------------------------------


def _wkt_point(point: LatLon) -> str:
    # Feste Dezimalschreibweise: repr() könnte Exponenten (1e-05) liefern, die WKT-Parser
    # nicht überall akzeptieren. 10 Nachkommastellen entsprechen weniger als 0,1 mm.
    return f"POINT({point[1]:.10f} {point[0]:.10f})"


def _wkt_line(points: Sequence[LatLon]) -> str:
    return "LINESTRING(" + ", ".join(f"{p[1]:.10f} {p[0]:.10f}" for p in points) + ")"


def build_line(geometry: Sequence[LatLon], start: LatLon, end: LatLon) -> list[LatLon]:
    """Gültige Linie (mindestens zwei Punkte) aus der OSRM-Geometrie.

    Aufeinanderfolgende Duplikate werden entfernt. Liefert OSRM nur einen Punkt (Start == Ziel)
    oder gar keinen, wird die Linie aus diesem Punkt bzw. aus Start und Ziel aufgebaut; eine
    Linie mit zwei identischen Punkten ist für PostGIS zulässig (Länge 0).
    """
    points: list[LatLon] = []
    for p in geometry:
        if not points or points[-1] != p:
            points.append(p)
    if not points:
        points = [start] if start == end else [start, end]
    if len(points) == 1:
        points = [points[0], points[0]]
    return points


# --- Lesen ---------------------------------------------------------------------------------------


def _count_requests(status: str) -> ColumnElement[int]:
    return (
        select(func.count())
        .select_from(RideRequest)
        .where(RideRequest.ride_id == Ride.id, RideRequest.status == status)
        .correlate(Ride)
        .scalar_subquery()
    )


def _ride_out_select() -> tuple[Any, ...]:
    geom = cast(Ride.route_geom, Geometry)
    return (
        Ride.id,
        func.ST_Y(cast(Ride.start_geom, Geometry)).label("start_lat"),
        func.ST_X(cast(Ride.start_geom, Geometry)).label("start_lon"),
        func.ST_Y(cast(Ride.end_geom, Geometry)).label("end_lat"),
        func.ST_X(cast(Ride.end_geom, Geometry)).label("end_lon"),
        Ride.departure_time,
        Ride.seats_total,
        Ride.seats_available,
        Ride.status,
        Ride.route_distance_m,
        Ride.route_duration_s,
        # Sehr kurze Linien kann ST_Simplify verwerfen (NULL) → unvereinfachte Linie nehmen.
        func.coalesce(simplified_route_geojson(Ride.route_geom), func.ST_AsGeoJSON(geom, 5)).label(
            "route_geojson"
        ),
        Ride.created_at,
        _count_requests("pending").label("pending_requests"),
        _count_requests("accepted").label("accepted_requests"),
    )


def _to_ride_out(row: Row[Any]) -> RideOut:
    return RideOut(
        id=row.id,
        start=Coordinate(lat=row.start_lat, lon=row.start_lon),
        end=Coordinate(lat=row.end_lat, lon=row.end_lon),
        departure_time=row.departure_time,
        seats_total=row.seats_total,
        seats_available=row.seats_available,
        status=row.status,
        distance_km=round(row.route_distance_m / 1000, 1),
        duration_min=round(row.route_duration_s / 60, 1),
        route=geojson_linestring_to_latlon(row.route_geojson),
        created_at=row.created_at,
        pending_requests=row.pending_requests,
        accepted_requests=row.accepted_requests,
    )


async def _load_ride_out(session: AsyncSession, ride_id: uuid.UUID) -> RideOut:
    row = (await session.execute(select(*_ride_out_select()).where(Ride.id == ride_id))).one()
    return _to_ride_out(row)


async def list_my_rides(
    session: AsyncSession, driver_id: uuid.UUID, *, limit: int, offset: int
) -> Page[RideOut]:
    total = await session.scalar(
        select(func.count()).select_from(Ride).where(Ride.driver_id == driver_id)
    )
    rows = (
        await session.execute(
            select(*_ride_out_select())
            .where(Ride.driver_id == driver_id)
            # id als Tie-Breaker: stabile Reihenfolge für die Pagination
            .order_by(Ride.departure_time.desc(), Ride.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page[RideOut](
        items=[_to_ride_out(r) for r in rows], total=total or 0, limit=limit, offset=offset
    )


# --- Obergrenze offener Fahrten (SEC-1) ----------------------------------------------------------


async def _lock_driver(session: AsyncSession, driver_id: uuid.UUID) -> None:
    """Nutzerzeile sperren (``FOR NO KEY UPDATE``): serialisiert nur Anlegen/Verschieben derselben
    Fahrerin bzw. desselben Fahrers, blockiert aber keine Fremdschlüssel-Prüfungen anderer Tabellen.

    Reihenfolge: Beim Verschieben zuerst die Nutzer-, dann die Fahrtzeile sperren – wie das Löschen
    eines Kontos (Nutzerzeile, dann kaskadierend die Fahrten); die umgekehrte Reihenfolge wäre ein
    Deadlock-Risiko.
    """
    locked = await session.scalar(
        select(User.id).where(User.id == driver_id).with_for_update(key_share=True)
    )
    if locked is None:  # Konto wurde gelöscht, während OSRM antwortete bzw. die Anfrage lief
        await session.rollback()
        raise ApiError(401, "unauthorized", "Anmeldung erforderlich")


async def enforce_open_ride_limit(
    session: AsyncSession,
    settings: Settings,
    driver_id: uuid.UUID,
    *,
    exclude_ride_id: uuid.UUID | None = None,
) -> None:
    """Höchstens ``MAX_OPEN_RIDES_PER_DRIVER`` offene/volle, noch nicht abgefahrene Fahrten.

    Gemeinsam für ``create_ride`` und ``patch_ride``: sperrt die Nutzerzeile (parallele Aufrufe
    können die Grenze nicht gemeinsam überschreiten), zählt und lehnt sonst mit 409
    ``too_many_open_rides`` ab. ``exclude_ride_id`` nimmt die gerade geänderte Fahrt aus der
    Zählung, weil sie nach der Änderung ohnehin einen Platz belegt.
    """
    await _lock_driver(session, driver_id)
    query = (
        select(func.count())
        .select_from(Ride)
        .where(
            Ride.driver_id == driver_id,
            Ride.status.in_(("open", "full")),
            Ride.departure_time > func.now(),
        )
    )
    if exclude_ride_id is not None:
        query = query.where(Ride.id != exclude_ride_id)
    if (await session.scalar(query) or 0) >= settings.max_open_rides_per_driver:
        await session.rollback()
        raise ApiError(
            409,
            "too_many_open_rides",
            f"Es sind höchstens {settings.max_open_rides_per_driver} offene Fahrten "
            "gleichzeitig möglich. Storniere eine Fahrt oder warte, bis sie abgefahren ist.",
        )


# --- Anlegen -------------------------------------------------------------------------------------


async def create_ride(
    session: AsyncSession,
    routing: RoutingClient,
    settings: Settings,
    driver_id: uuid.UUID,
    data: RideCreate,
) -> RideOut:
    # Die Auth-Dependency hat die Nutzerzeile in dieser Session gelesen und damit eine Transaktion
    # (= Pool-Verbindung, „idle in transaction“) geöffnet. Bis zu drei OSRM-Aufrufe à OSRM_TIMEOUT_S
    # dürfen sie nicht festhalten, sonst erschöpfen parallele Anlagen den Pool (5 + 10) für alle
    # Endpunkte. Es werden danach nur ``driver_id`` und ``data`` benutzt, keine ORM-Attribute.
    await session.rollback()
    start = data.start.as_latlon()
    event: LatLon = (settings.event_lat, settings.event_lon)

    # OSRM snappt Punkte außerhalb des Extrakts still auf ferne Straßen → beide Enden prüfen.
    await snap_or_error(routing, settings, start, field="Startpunkt")
    await snap_or_error(routing, settings, event, field="Eventziel")
    # Routing VOR dem ersten Schreibzugriff: bei Fehlern wird nichts gespeichert.
    with routing_errors():
        route = await routing.route([start, event])
    line = build_line(route.geometry, start, event)

    try:
        await enforce_open_ride_limit(session, settings, driver_id)
        ride_id = await session.scalar(
            insert(Ride)
            .values(
                driver_id=driver_id,
                start_geom=func.ST_GeogFromText(_wkt_point(start)),
                end_geom=func.ST_GeogFromText(_wkt_point(event)),
                route_geom=func.ST_GeogFromText(_wkt_line(line)),
                route_distance_m=route.distance_m,
                route_duration_s=route.duration_s,
                seats_total=data.seats,
                seats_available=data.seats,
                departure_time=data.departure_time,
                status="open",
            )
            .returning(Ride.id)
        )
    except IntegrityError:
        # Das Konto wurde gelöscht, während OSRM antwortete (die Nutzerprüfung liegt davor).
        await session.rollback()
        raise ApiError(401, "unauthorized", "Anmeldung erforderlich") from None
    assert ride_id is not None
    out = await _load_ride_out(session, ride_id)
    await session.commit()
    return out


# --- Ändern / Stornieren -------------------------------------------------------------------------


class LockedRide(NamedTuple):
    status: str
    departure_time: datetime
    departed: bool  # Abfahrtszeit <= now() der Datenbank


async def _lock_own_ride(
    session: AsyncSession, ride_id: uuid.UUID, driver_id: uuid.UUID
) -> LockedRide:
    """Fahrtzeile sperren (zuerst!). Fremde und unbekannte Fahrten → 404 (kein Orakel)."""
    row = (
        await session.execute(
            select(
                Ride.id,
                Ride.status,
                Ride.departure_time,
                (Ride.departure_time <= func.now()).label("departed"),
            )
            .where(Ride.id == ride_id, Ride.driver_id == driver_id)
            .with_for_update()
        )
    ).first()
    if row is None:
        raise _not_found()
    return LockedRide(
        status=row.status, departure_time=row.departure_time, departed=bool(row.departed)
    )


async def _count_accepted(session: AsyncSession, ride_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(RideRequest)
        .where(RideRequest.ride_id == ride_id, RideRequest.status == "accepted")
    )
    return count or 0


async def patch_ride(
    session: AsyncSession,
    settings: Settings,
    ride_id: uuid.UUID,
    driver_id: uuid.UUID,
    data: RidePatch,
) -> RideOut:
    if data.departure_time is not None:
        # Nutzerzeile VOR der Fahrtzeile sperren (Reihenfolge wie beim Löschen des Kontos).
        await _lock_driver(session, driver_id)
    ride = await _lock_own_ride(session, ride_id, driver_id)
    if ride.status == "cancelled":
        raise ApiError(409, "ride_cancelled", "Die Fahrt wurde storniert und ist nicht änderbar")
    if ride.departed:
        # Eine abgefahrene Fahrt zählt nicht als offen; sie in die Zukunft zu schieben würde die
        # Obergrenze offener Fahrten (SEC-1) umgehen. Deshalb ist sie überhaupt nicht mehr änderbar.
        raise ApiError(409, "ride_departed", "Die Fahrt ist bereits abgefahren und nicht änderbar")

    accepted = await _count_accepted(session, ride_id)
    values: dict[str, object] = {}

    if data.departure_time is not None and data.departure_time != ride.departure_time:
        if accepted > 0:
            raise ApiError(
                409,
                "has_accepted_requests",
                "Die Abfahrtszeit kann nicht geändert werden, solange angenommene Anfragen "
                "existieren",
            )
        await enforce_open_ride_limit(session, settings, driver_id, exclude_ride_id=ride_id)
        values["departure_time"] = data.departure_time

    if data.seats_total is not None:
        if data.seats_total < accepted:
            raise ApiError(
                409,
                "seats_below_accepted",
                "Die Platzzahl darf nicht unter die Zahl der angenommenen Anfragen sinken",
            )
        available = data.seats_total - accepted
        values.update(
            seats_total=data.seats_total,
            seats_available=available,
            status="full" if available == 0 else "open",
        )

    if values:
        await session.execute(update(Ride).where(Ride.id == ride_id).values(**values))
    out = await _load_ride_out(session, ride_id)
    await session.commit()
    return out


async def cancel_ride(session: AsyncSession, ride_id: uuid.UUID, driver_id: uuid.UUID) -> None:
    """Fahrt stornieren; aktive Anfragen (pending/accepted) werden mit storniert. Idempotent."""
    await _lock_own_ride(session, ride_id, driver_id)  # erst Fahrt, dann Anfragen
    await session.execute(update(Ride).where(Ride.id == ride_id).values(status="cancelled"))
    await session.execute(
        update(RideRequest)
        .where(RideRequest.ride_id == ride_id, RideRequest.status.in_(("pending", "accepted")))
        .values(status="cancelled", decided_at=func.now())
    )
    await session.commit()
