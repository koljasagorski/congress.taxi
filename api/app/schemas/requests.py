"""Schemas der Anfragen (Phase 5). Form: docs/API-CONTRACT.md, Abschnitt „Anfragen“."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.common import Coordinate

RequestStatus = Literal["pending", "accepted", "declined", "cancelled"]


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pickup: Coordinate


class RideSummary(BaseModel):
    departure_time: datetime
    seats_available: int
    driver_display_name: str


class RiderSummary(BaseModel):
    display_name: str


class RequestOut(BaseModel):
    id: UUID
    ride_id: UUID
    status: RequestStatus
    pickup: Coordinate
    #: Vergröbert wie ``/api/match`` (0,5-km-Schritte, ganze Minuten) – Standort-Orakel.
    detour_km: float
    detour_minutes: int
    created_at: datetime
    decided_at: datetime | None
    ride: RideSummary
    rider: RiderSummary
    #: Nur bei ``accepted`` und nur für Beteiligte gesetzt (Datenschutz), sonst ``None``.
    counterpart_email: str | None
