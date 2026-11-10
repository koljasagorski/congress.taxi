from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Coordinate(BaseModel):
    """Koordinate im JSON: ``{"lat": 53.55, "lon": 9.99}`` – WGS84, Grad."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lat: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
    lon: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]

    def as_latlon(self) -> tuple[float, float]:
        return (self.lat, self.lon)


class Page(BaseModel, Generic[T]):  # noqa: UP046 - pydantic erwartet Generic[T] für Schemas
    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    message: str
