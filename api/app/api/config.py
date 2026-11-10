"""GET /api/config – öffentliche Laufzeitkonfiguration für das Frontend (keine Geheimnisse)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings

router = APIRouter(prefix="/api", tags=["config"])


class EventConfig(BaseModel):
    name: str
    lat: float
    lon: float
    start_date: date
    end_date: date


class MatchingConfig(BaseModel):
    prefilter_radius_m: int
    max_detour_ratio: float
    max_detour_minutes: float
    detour_mode: Literal["any", "all"]
    max_candidates: int


class MapConfig(BaseModel):
    tile_url: str
    attribution: str
    geocoder: Literal["none", "photon", "nominatim"]


class LimitsConfig(BaseModel):
    min_seats: int
    max_seats: int


class PublicConfig(BaseModel):
    event: EventConfig
    matching: MatchingConfig
    map: MapConfig
    limits: LimitsConfig
    retention_days_after_event: int


@router.get("/config", response_model=PublicConfig)
async def get_public_config(settings: Annotated[Settings, Depends(get_settings)]) -> PublicConfig:
    return PublicConfig(
        event=EventConfig(
            name=settings.event_name,
            lat=settings.event_lat,
            lon=settings.event_lon,
            start_date=settings.event_start_date,
            end_date=settings.event_end_date,
        ),
        matching=MatchingConfig(
            prefilter_radius_m=settings.match_prefilter_radius_m,
            max_detour_ratio=settings.match_max_detour_ratio,
            max_detour_minutes=settings.match_max_detour_minutes,
            detour_mode=settings.match_detour_mode,
            max_candidates=settings.match_max_candidates,
        ),
        map=MapConfig(
            tile_url=settings.tile_url,
            attribution=settings.tile_attribution,
            geocoder=settings.geocoder,
        ),
        limits=LimitsConfig(min_seats=1, max_seats=8),
        retention_days_after_event=settings.retention_days_after_event,
    )
