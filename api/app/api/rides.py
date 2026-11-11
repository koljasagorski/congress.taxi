"""Phase 3 – Fahrten anlegen/ändern/löschen.

Vertrag: docs/API-CONTRACT.md. Alle Endpunkte erfordern Anmeldung; fremde oder unbekannte Fahrten
liefern 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.auth.deps import CurrentUser, SessionDep, SettingsDep
from app.ratelimit import user_rate_limit
from app.routing import RoutingClient, get_routing_client
from app.schemas.common import Page
from app.schemas.rides import RideCreate, RideOut, RidePatch
from app.services import rides as service

router = APIRouter(prefix="/api/rides", tags=["rides"])

RoutingDep = Annotated[RoutingClient, Depends(get_routing_client)]


@router.post(
    "",
    status_code=201,
    dependencies=[
        Depends(user_rate_limit("rides_create", "rate_limit_rides_create_per_hour", 3600))
    ],
)
async def create_ride(
    body: RideCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    routing: RoutingDep,
) -> RideOut:
    return await service.create_ride(session, routing, settings, user.id, body)


# Muss vor "/{ride_id}" stehen, damit "mine" nicht als ID interpretiert wird.
@router.get("/mine")
async def my_rides(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> Page[RideOut]:
    return await service.list_my_rides(session, user.id, limit=limit, offset=offset)


@router.patch("/{ride_id}")
async def patch_ride(
    ride_id: uuid.UUID,
    body: RidePatch,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> RideOut:
    return await service.patch_ride(session, settings, ride_id, user.id, body)


@router.delete("/{ride_id}", status_code=204)
async def cancel_ride(ride_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> Response:
    await service.cancel_ride(session, ride_id, user.id)
    return Response(status_code=204)
