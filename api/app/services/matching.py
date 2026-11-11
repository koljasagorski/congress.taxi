"""Matching: SQL-Vorfilter (PostGIS) + Umwegberechnung (OSRM).

Ablauf (Vertrag, Abschnitt „Matching“):

1. Pickup wird vom Aufrufer per ``snap_or_error`` geprüft.
2. :func:`candidate_statement` – Vorfilter mit ``ST_DWithin`` auf der **nackten** Geography-Spalte
   ``route_geom`` (nur so ist der GiST-Index ``ix_rides_route_geom`` nutzbar), Status, freie Plätze,
   Zeitfenster, fremde Fahrten; nach Luftlinie sortiert, je Fahrer auf
   ``MATCH_MAX_CANDIDATES_PER_DRIVER`` und insgesamt auf ``MATCH_MAX_CANDIDATES`` begrenzt.
3. :func:`compute_detours` – pro Kandidat ``compute_detour`` (parallel, begrenzt). Gerouted wird
   Fahrer-Start → Pickup → **Zielpunkt der Fahrt** (``end_geom``, nicht ``EVENT_*``), weil
   ``route_distance_m`` gegen genau diesen Punkt gemessen wurde.
4. Filter ``acceptable``; 5. Sortierung nach kleinstem Umweg (Fahrzeit, dann Distanz), dann früheste
   Abfahrt, zuletzt Fahrt-ID (deterministisch).

Datenschutz: Die Anzeige-Route wird am Anfang um ``MATCH_ROUTE_TRIM_START_M`` Meter gekürzt (in
Metern entlang der Route, nicht in Grad) und danach vereinfacht. Ist die Route kürzer als die
Kürzung, bleibt nur das **letzte Viertel** (Kürzungsanteil höchstens :data:`MAX_TRIM_FRACTION`); der
Start wird nie ausgegeben. Nicht darstellbare Routen (Länge 0) liefern eine leere Liste.

Bekannte Grenzen:

* Gekürzt wird entlang der Route, nicht nach Luftlinie – eine Route, die später wieder an den
  Start zurückführt (Schleife), zeigt dieses Stück.
* Die Kürzung schützt die **Anzeige**, nicht gegen aktives Abtasten. Vorfilter (``ST_DWithin`` auf
  der vollen ``route_geom``) und Umweg (``compute_detour`` ab dem exakten Fahrer-Start) nutzen
  bewusst die ungekürzte Route: Der Umweg muss real von dort gerechnet werden (Vertrag; Phase 5
  rechnet identisch), sonst wäre er falsch. Wer viele Abholpunkte abfragt, kann aus „Treffer
  ja/nein“ und den Umweg-Werten (≈ 0 auf dem verborgenen Abschnitt) den Start eingrenzen. Die
  **Ausgabe** ist deshalb gröber als die interne Rechnung (:func:`coarse_detour_km` usw.:
  0,5-km-Schritte, ganze Minuten, Verhältnis auf 2 Stellen); Filter und Sortierung bleiben exakt.
  Weitere Gegenmaßnahmen sind das Rate-Limit (Phase 7, ``RATE_LIMIT_MATCH_PER_MIN``) und die
  Anmeldepflicht; Abtasten wird dadurch gebremst, nicht verhindert (PLAN.md, Annahme 18).
"""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Select, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Ride, User
from app.routing import LatLon, NoRouteError, RoutingClient, RoutingUnavailableError
from app.services.detour import Detour, compute_detour
from app.services.geo import (
    geojson_linestring_to_latlon,
    point_wkt,
    simplified_route_geojson,
)

#: Gleichzeitige OSRM-Anfragen je Match-Aufruf.
MAX_PARALLEL_ROUTES = 8
#: Anteil der Route, der bei der Kürzung höchstens entfernt wird (mindestens das letzte Viertel
#: bleibt – bei sehr kurzen Routen wird also nie die Startposition ausgegeben).
MAX_TRIM_FRACTION = 0.75
#: Obergrenze für ``detour_ratio`` in der Antwort (Basisdistanz 0 ergibt ``inf``; JSON kennt
#: kein inf).
MAX_RATIO_OUT = 999.0
#: Web-Mercator ist winkeltreu: Anteile entlang einer Linie entsprechen (lokal) den Anteilen der
#: echten Länge – anders als Grad-Koordinaten, wo ein Ost-West-Stück bei 53° Breite nur ≈ 60 % der
#: Länge eines gleich langen Nord-Süd-Stücks hat.
_METRIC_SRID = 3857


@dataclass(frozen=True, slots=True)
class Candidate:
    ride_id: uuid.UUID
    driver_display_name: str
    departure_time: datetime
    seats_available: int
    start: LatLon
    end: LatLon
    base_distance_m: float
    base_duration_s: float
    route: list[list[float]]


@dataclass(frozen=True, slots=True)
class Match:
    candidate: Candidate
    detour: Detour


def trimmed_route_expression(trim_m: int) -> Any:
    """SQL-Ausdruck: ``route_geom`` ohne die ersten ``trim_m`` Meter (Geometry, SRID 4326).

    Länge 0 → NULL (kein Anzeigeverlauf). ``trim_m = 0`` → unveränderte Route.
    """
    geom = cast(Ride.route_geom, Geometry)
    if trim_m <= 0:
        return geom
    length_m = func.ST_Length(Ride.route_geom)  # Geography → Meter
    # LEAST ignoriert NULL in PostgreSQL – die Länge-0-Prüfung muss deshalb außen sitzen.
    fraction = func.least(literal(float(trim_m)) / func.nullif(length_m, 0), MAX_TRIM_FRACTION)
    mercator = func.ST_Transform(geom, _METRIC_SRID)
    cut = func.ST_SetSRID(
        func.ST_Transform(func.ST_LineSubstring(mercator, fraction, 1.0), 4326), 4326
    )
    return case((length_m > 0, cut), else_=None)


def candidate_statement(
    *,
    user_id: uuid.UUID,
    pickup: LatLon,
    radius_m: int,
    time_from: datetime,
    time_to: datetime | None,
    min_seats: int,
    max_candidates: int,
    max_per_driver: int,
    trim_m: int,
) -> Select[Any]:
    """Vorfilter-Statement (parametrisiert; exportiert, damit Tests den Plan prüfen können).

    Zwei Stufen, damit die teuren Anzeige-Ausdrücke (Kürzen/Vereinfachen der Route) nur für die
    höchstens ``max_candidates`` Gewinner berechnet werden:

    1. ``ranked``: Vorfilter mit ``ST_DWithin`` auf der nackten Geography-Spalte (GiST-Index),
       je Fahrer nach Luftlinie nummeriert (``row_number() OVER (PARTITION BY driver_id …)``).
    2. ``chosen``: höchstens ``max_per_driver`` Fahrten je Fahrer, nach Luftlinie sortiert, auf
       ``max_candidates`` begrenzt. Ein Konto mit vielen Fahrten am Abholpunkt belegt so höchstens
       ``max_per_driver`` Plätze und verdrängt keine echten Fahrer (SEC-1).
    """
    pickup_geog = func.ST_GeogFromText(point_wkt(pickup))
    beeline = func.ST_Distance(Ride.route_geom, pickup_geog)
    ranked_stmt = select(
        Ride.id.label("ride_id"),
        Ride.driver_id.label("driver_id"),
        Ride.departure_time.label("departure_time"),
        beeline.label("beeline_m"),
        func.row_number()
        .over(partition_by=Ride.driver_id, order_by=(beeline, Ride.departure_time, Ride.id))
        .label("driver_rank"),
    ).where(
        func.ST_DWithin(Ride.route_geom, pickup_geog, radius_m),
        Ride.status == "open",
        Ride.seats_available >= min_seats,
        Ride.departure_time >= time_from,
        Ride.driver_id != user_id,
    )
    if time_to is not None:
        ranked_stmt = ranked_stmt.where(Ride.departure_time <= time_to)
    ranked = ranked_stmt.subquery("ranked")
    chosen = (
        select(ranked.c.ride_id, ranked.c.beeline_m, ranked.c.departure_time)
        .where(ranked.c.driver_rank <= max_per_driver)
        .order_by(ranked.c.beeline_m, ranked.c.departure_time, ranked.c.ride_id)
        .limit(max_candidates)
        .subquery("chosen")
    )

    start = cast(Ride.start_geom, Geometry)
    end = cast(Ride.end_geom, Geometry)
    trimmed = trimmed_route_expression(trim_m)
    route_json = func.coalesce(
        simplified_route_geojson(trimmed),
        func.ST_AsGeoJSON(trimmed, 5),
    )
    return (
        select(
            Ride.id.label("ride_id"),
            User.display_name.label("driver_display_name"),
            Ride.departure_time,
            Ride.seats_available,
            func.ST_Y(start).label("start_lat"),
            func.ST_X(start).label("start_lon"),
            func.ST_Y(end).label("end_lat"),
            func.ST_X(end).label("end_lon"),
            Ride.route_distance_m,
            Ride.route_duration_s,
            route_json.label("route_json"),
        )
        .join(chosen, chosen.c.ride_id == Ride.id)
        .join(User, User.id == Ride.driver_id)
        .order_by(chosen.c.beeline_m, chosen.c.departure_time, chosen.c.ride_id)
    )


def _route_to_latlon(route_json: str | None) -> list[list[float]]:
    """GeoJSON → ``[[lat, lon], …]``; alles außer einer echten Linie (NULL, Punkt) → leer."""
    if not route_json:
        return []
    try:
        return geojson_linestring_to_latlon(route_json)
    except (KeyError, ValueError, TypeError):
        return []


async def load_candidates(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    pickup: LatLon,
    time_from: datetime,
    time_to: datetime | None,
    min_seats: int,
) -> list[Candidate]:
    stmt = candidate_statement(
        user_id=user_id,
        pickup=pickup,
        radius_m=settings.match_prefilter_radius_m,
        time_from=time_from,
        time_to=time_to,
        min_seats=min_seats,
        max_candidates=settings.match_max_candidates,
        max_per_driver=settings.match_max_candidates_per_driver,
        trim_m=settings.match_route_trim_start_m,
    )
    rows = (await session.execute(stmt)).all()
    return [
        Candidate(
            ride_id=row.ride_id,
            driver_display_name=row.driver_display_name,
            departure_time=row.departure_time,
            seats_available=row.seats_available,
            start=(row.start_lat, row.start_lon),
            end=(row.end_lat, row.end_lon),
            base_distance_m=row.route_distance_m,
            base_duration_s=row.route_duration_s,
            route=_route_to_latlon(row.route_json),
        )
        for row in rows
    ]


async def compute_detours(
    routing: RoutingClient,
    settings: Settings,
    pickup: LatLon,
    candidates: list[Candidate],
) -> list[Match]:
    """Umweg je Kandidat (parallel, begrenzt).

    * ``NoRouteError`` für einen Kandidaten → Kandidat überspringen.
    * ``RoutingUnavailableError`` → 503 ``routing_unavailable`` für die ganze Anfrage; weitere noch
      nicht gestartete Kandidaten werden dann gar nicht mehr geroutet.
    """
    semaphore = asyncio.Semaphore(MAX_PARALLEL_ROUTES)
    unavailable = False

    async def one(candidate: Candidate) -> Match | None:
        nonlocal unavailable
        async with semaphore:
            if unavailable:
                return None
            try:
                detour = await compute_detour(
                    routing,
                    settings,
                    driver_start=candidate.start,
                    pickup=pickup,
                    event=candidate.end,
                    base_distance_m=candidate.base_distance_m,
                    base_duration_s=candidate.base_duration_s,
                )
            except NoRouteError:
                return None
            except RoutingUnavailableError:
                unavailable = True
                raise
        return Match(candidate, detour)

    results = await asyncio.gather(*(one(c) for c in candidates), return_exceptions=True)
    matches: list[Match] = []
    for result in results:
        if isinstance(result, RoutingUnavailableError):
            raise ApiError(503, "routing_unavailable", "Routing-Dienst nicht erreichbar") from None
        if isinstance(result, BaseException):
            raise result
        if result is not None:
            matches.append(result)
    return matches


def rank(matches: list[Match]) -> list[Match]:
    """Nur akzeptable Umwege; kleinster Umweg zuerst, dann früheste Abfahrt, dann Fahrt-ID."""
    acceptable = [m for m in matches if m.detour.acceptable]
    return sorted(
        acceptable,
        key=lambda m: (
            m.detour.duration_s,
            m.detour.distance_m,
            m.candidate.departure_time,
            str(m.candidate.ride_id),
        ),
    )


def finite_ratio(ratio: float) -> float:
    return ratio if math.isfinite(ratio) else MAX_RATIO_OUT


# Ausgabe-Rundung (Standort-Orakel entschärfen): Die exakten Umweg-Werte erlauben, über viele
# Abholpunkte den Fahrer-Start einzugrenzen. Die Antwort gibt deshalb nur gröbere Werte aus; Filter
# (``acceptable``) und Sortierung (``rank``) arbeiten weiter mit den exakten Werten.
DETOUR_KM_STEP = 0.5


def coarse_detour_km(distance_m: float) -> float:
    """Kilometer in 0,5-km-Schritten (kaufmännisch gerundet, nie negativ)."""
    return math.floor(max(distance_m, 0.0) / 1000.0 / DETOUR_KM_STEP + 0.5) * DETOUR_KM_STEP


def coarse_detour_minutes(duration_s: float) -> int:
    """Ganze Minuten (kaufmännisch gerundet, nie negativ)."""
    return math.floor(max(duration_s, 0.0) / 60.0 + 0.5)


def coarse_detour_ratio(ratio: float) -> float:
    """Verhältnis auf 2 Nachkommastellen; ``inf`` wird auf :data:`MAX_RATIO_OUT` gedeckelt."""
    return round(finite_ratio(ratio), 2)


async def find_matches(
    session: AsyncSession,
    routing: RoutingClient,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    pickup: LatLon,
    time_from: datetime | None,
    time_to: datetime | None,
    min_seats: int,
    now: datetime,
) -> list[Match]:
    """Schritte 2–5. Der Pickup muss vorher per ``snap_or_error`` geprüft sein.

    Untergrenze des Zeitfensters ist ``max(time_from, now)``: Bereits abgefahrene Fahrten werden
    nie vorgeschlagen.
    """
    lower = now if time_from is None else max(time_from, now)
    candidates = await load_candidates(
        session,
        settings,
        user_id=user_id,
        pickup=pickup,
        time_from=lower,
        time_to=time_to,
        min_seats=min_seats,
    )
    # Lesende Transaktion beenden, bevor die (bis zu ``MATCH_MAX_CANDIDATES``) OSRM-Aufrufe laufen:
    # sonst hängt die Pool-Verbindung währenddessen als „idle in transaction“.
    await session.rollback()
    if not candidates:
        return []
    return rank(await compute_detours(routing, settings, pickup, candidates))
