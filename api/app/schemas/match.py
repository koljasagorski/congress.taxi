"""Schemas für ``GET /api/match`` (Vertrag: docs/API-CONTRACT.md, Abschnitt „Matching“)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.routing import LatLon


class MatchQuery(BaseModel):
    """Query-Parameter. Zeiten müssen eine Zeitzone tragen (naive Zeiten → 422)."""

    model_config = ConfigDict(extra="ignore")

    lat: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    lon: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
    time_from: AwareDatetime | None = None
    time_to: AwareDatetime | None = None
    min_seats: Annotated[int, Field(ge=1, le=8)] = 1
    limit: Annotated[int, Field(ge=1, le=50)] = 20

    @model_validator(mode="after")
    def _window_is_ordered(self) -> Self:
        if (
            self.time_from is not None
            and self.time_to is not None
            and self.time_to < self.time_from
        ):
            raise ValueError("time_to darf nicht vor time_from liegen")
        return self

    @property
    def pickup(self) -> LatLon:
        return (self.lat, self.lon)


class MatchOut(BaseModel):
    """Treffer. Bewusst ohne Fahrer-ID, E-Mail und exakte Startposition (Datenschutz)."""

    ride_id: uuid.UUID
    driver_display_name: str
    departure_time: datetime
    seats_available: int
    #: Gröber als intern gerechnet (Standort-Orakel): km in 0,5-Schritten, Minuten ganzzahlig,
    #: Verhältnis auf 2 Stellen. Sortierung/Filter im Server nutzen die exakten Werte.
    detour_km: float
    detour_minutes: int
    #: Mehrdistanz relativ zur Basisroute; bei Basisdistanz 0 auf 999 gedeckelt (kein inf in JSON).
    detour_ratio: float
    #: Vereinfachte Route ``[[lat, lon], …]``, am Anfang gekürzt (``MATCH_ROUTE_TRIM_START_M``).
    route: list[list[float]]


class MatchList(BaseModel):
    items: list[MatchOut]
    #: Anzahl aller akzeptablen Treffer unter den Kandidaten (``limit`` kürzt nur ``items``).
    total: int
