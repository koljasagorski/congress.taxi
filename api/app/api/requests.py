"""Phase 5 – Anfragen/Accept.

Vertrag: docs/API-CONTRACT.md. Dieses Modul gehört genau einer Phase; die Registrierung in
app/main.py ist bereits erfolgt – hier nur Endpunkte ergänzen.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.deps import CurrentUser, SessionDep, SettingsDep
from app.ratelimit import user_rate_limit
from app.routing import RoutingClient, get_routing_client
from app.schemas.common import Page
from app.schemas.requests import RequestCreate, RequestOut, RequestStatus
from app.services import requests as service

router = APIRouter(prefix="/api", tags=["requests"])

RoutingDep = Annotated[RoutingClient, Depends(get_routing_client)]
StatusQuery = Annotated[RequestStatus | None, Query()]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.post(
    "/rides/{ride_id}/requests",
    response_model=RequestOut,
    status_code=201,
    dependencies=[
        Depends(user_rate_limit("requests_create", "rate_limit_requests_create_per_hour", 3600))
    ],
)
async def create_request(
    ride_id: uuid.UUID,
    body: RequestCreate,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    routing: RoutingDep,
) -> RequestOut:
    return await service.create_request(
        session, settings, routing, user_id=user.id, ride_id=ride_id, pickup=body.pickup
    )


@router.get("/requests/incoming", response_model=Page[RequestOut])
async def incoming_requests(
    user: CurrentUser,
    session: SessionDep,
    status: StatusQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[RequestOut]:
    items, total = await service.list_requests(
        session, user.id, direction="incoming", status=status, limit=limit, offset=offset
    )
    return Page[RequestOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/requests/outgoing", response_model=Page[RequestOut])
async def outgoing_requests(
    user: CurrentUser,
    session: SessionDep,
    status: StatusQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[RequestOut]:
    items, total = await service.list_requests(
        session, user.id, direction="outgoing", status=status, limit=limit, offset=offset
    )
    return Page[RequestOut](items=items, total=total, limit=limit, offset=offset)


@router.post("/requests/{request_id}/accept", response_model=RequestOut)
async def accept_request(
    request_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> RequestOut:
    return await service.accept_request(session, user_id=user.id, request_id=request_id)


@router.post("/requests/{request_id}/decline", response_model=RequestOut)
async def decline_request(
    request_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> RequestOut:
    return await service.decline_request(session, user_id=user.id, request_id=request_id)


@router.post("/requests/{request_id}/cancel", response_model=RequestOut)
async def cancel_request(
    request_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> RequestOut:
    return await service.cancel_request(session, user_id=user.id, request_id=request_id)
