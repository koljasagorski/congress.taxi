"""Phase 4 – Matching: ``GET /api/match``.

Vertrag: docs/API-CONTRACT.md. Die Antwort enthält keine Kontaktdaten, keine Fahrer-ID und keine
exakte Startposition. Logs enthalten keine Koordinaten (nur das Routen-Template wird geloggt).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.deps import CurrentUser, SessionDep, SettingsDep
from app.routing import RoutingClient, get_routing_client
from app.schemas.match import MatchList, MatchOut, MatchQuery
from app.services.geo import snap_or_error
from app.services.matching import (
    coarse_detour_km,
    coarse_detour_minutes,
    coarse_detour_ratio,
    find_matches,
)

router = APIRouter(prefix="/api/match", tags=["match"])


@router.get("", response_model=MatchList)
async def match(
    query: Annotated[MatchQuery, Query()],
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    routing: Annotated[RoutingClient, Depends(get_routing_client)],
) -> MatchList:
    pickup = query.pickup
    # CurrentUser hat per session.get eine Transaktion begonnen. Sie darf nicht während der
    # OSRM-Aufrufe offen bleiben (Pool-Verbindung „idle in transaction“): ID kopieren, beenden.
    # (rollback() expiriert die ORM-Objekte; ``user`` wird danach nicht mehr gelesen.)
    user_id = user.id
    await session.rollback()
    await snap_or_error(routing, settings, pickup, field="Abholpunkt")
    matches = await find_matches(
        session,
        routing,
        settings,
        user_id=user_id,
        pickup=pickup,
        time_from=query.time_from,
        time_to=query.time_to,
        min_seats=query.min_seats,
        now=datetime.now(UTC),
    )
    items = [
        MatchOut(
            ride_id=m.candidate.ride_id,
            driver_display_name=m.candidate.driver_display_name,
            departure_time=m.candidate.departure_time,
            seats_available=m.candidate.seats_available,
            detour_km=coarse_detour_km(m.detour.distance_m),
            detour_minutes=coarse_detour_minutes(m.detour.duration_s),
            detour_ratio=coarse_detour_ratio(m.detour.ratio),
            route=m.candidate.route,
        )
        for m in matches[: query.limit]
    ]
    return MatchList(items=items, total=len(matches))
