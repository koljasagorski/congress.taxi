"""Schemas für Fahrten (Vertrag: docs/API-CONTRACT.md, Abschnitt „Fahrten“)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StrictInt,
    field_validator,
    model_validator,
)

from app.schemas.common import Coordinate

MIN_SEATS = 1
MAX_SEATS = 8

RideStatus = Literal["open", "full", "cancelled"]


def _require_future(value: datetime) -> datetime:
    # Keine Eingabewerte in der Meldung (Fehlerantworten spiegeln nichts zurück).
    if value <= datetime.now(UTC):
        raise ValueError("Die Abfahrtszeit muss in der Zukunft liegen")
    return value.astimezone(UTC)


def _check_seats(value: int) -> int:
    if not MIN_SEATS <= value <= MAX_SEATS:
        raise ValueError(f"Die Platzzahl muss zwischen {MIN_SEATS} und {MAX_SEATS} liegen")
    return value


class RideCreate(BaseModel):
    """Body von ``POST /api/rides``. ``departure_time`` braucht eine Zeitzone."""

    model_config = ConfigDict(extra="forbid")

    start: Coordinate
    departure_time: AwareDatetime
    seats: StrictInt

    @field_validator("departure_time")
    @classmethod
    def _future(cls, value: datetime) -> datetime:
        return _require_future(value)

    @field_validator("seats")
    @classmethod
    def _seats_range(cls, value: int) -> int:
        return _check_seats(value)


class RidePatch(BaseModel):
    """Body von ``PATCH /api/rides/{id}``: beide Felder optional, ``null`` ist nicht erlaubt."""

    model_config = ConfigDict(extra="forbid")

    departure_time: AwareDatetime | None = None
    seats_total: StrictInt | None = None

    @model_validator(mode="after")
    def _no_explicit_null(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} darf nicht null sein")
        return self

    @field_validator("departure_time")
    @classmethod
    def _future(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_future(value)

    @field_validator("seats_total")
    @classmethod
    def _seats_range(cls, value: int | None) -> int | None:
        return None if value is None else _check_seats(value)


class RideOut(BaseModel):
    """Sicht der Fahrerin/des Fahrers auf die eigene Fahrt."""

    id: uuid.UUID
    start: Coordinate
    end: Coordinate
    departure_time: datetime
    seats_total: int
    seats_available: int
    status: RideStatus
    distance_km: float
    duration_min: float
    route: list[list[float]]
    created_at: datetime
    pending_requests: int
    accepted_requests: int
