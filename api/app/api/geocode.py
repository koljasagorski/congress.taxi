"""Adresssuche: ``GET /api/geocode?q=…`` (Vertrag: docs/API-CONTRACT.md).

Nur für angemeldete Nutzer und nur, wenn ``GEOCODER=nominatim`` gesetzt ist. Der Suchtext ist
personenbezogen (eine Adresse): Er wird weder gespeichert noch protokolliert und nicht in
Fehlermeldungen wiederholt. Gespeichert wird später nur der gewählte Kartenpunkt.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.deps import CurrentUser
from app.errors import ApiError
from app.geocoding import (
    Geocoder,
    GeocoderBusyError,
    GeocoderUnavailableError,
    get_geocoder,
)
from app.ratelimit import user_rate_limit

router = APIRouter(prefix="/api/geocode", tags=["geocode"])


class GeocodeItem(BaseModel):
    label: str
    lat: float
    lon: float


class GeocodeList(BaseModel):
    items: list[GeocodeItem]


@router.get(
    "",
    response_model=GeocodeList,
    dependencies=[
        Depends(user_rate_limit("geocode_user", "rate_limit_geocode_user_per_min", 60)),
    ],
)
async def geocode(
    user: CurrentUser,
    q: Annotated[str, Query(min_length=3, max_length=200)],
    geocoder: Annotated[Geocoder | None, Depends(get_geocoder)],
) -> GeocodeList:
    if geocoder is None:
        raise ApiError(404, "geocoder_disabled", "Die Adresssuche ist nicht aktiviert.")
    query = " ".join(q.split())
    if len(query) < 3:
        raise ApiError(422, "validation_error", "Suchtext zu kurz")
    try:
        hits = await geocoder.search(query)
    except GeocoderBusyError:
        raise ApiError(
            503,
            "geocoder_busy",
            "Die Adresssuche ist gerade ausgelastet – bitte gleich noch einmal versuchen.",
            headers={"Retry-After": "5"},
        ) from None
    except GeocoderUnavailableError:
        raise ApiError(
            503,
            "geocoder_unavailable",
            "Die Adresssuche ist gerade nicht erreichbar.",
        ) from None
    return GeocodeList(items=[GeocodeItem(label=h.label, lat=h.lat, lon=h.lon) for h in hits])
