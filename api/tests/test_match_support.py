"""Gemeinsame Hilfen der Matching-Tests (enthält selbst keine Tests)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from app.auth.tokens import COOKIE_NAME
from app.config import Settings, get_settings
from app.routing.base import LatLon
from tests.fakes import FakeRouting, haversine_m
from tests.helpers import EVENT, NORTH, auth_cookies, insert_ride

# Punkte der Hamburg-Ebene für FakeRouting (Luftlinie × 1.3, 60 km/h)
MID_NORTH_EVENT: LatLon = (53.655, 9.969)  # liegt (fast) auf der Geraden NORTH → EVENT
SOUTH_WEST: LatLon = (53.40, 9.70)


@contextmanager
def override_settings(**update: Any) -> Iterator[Settings]:
    """Überschreibt Settings für die App (Kopie der echten, damit z. B. JWT_SECRET passt)."""
    from app.main import app

    settings = get_settings().model_copy(update=update)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield settings
    finally:
        app.dependency_overrides.pop(get_settings, None)


def login(client: Any, user: dict[str, Any]) -> None:
    """Setzt das Anmelde-Cookie am Client (vermeidet die httpx-Deprecation von ``cookies=``)."""
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, auth_cookies(user)[COOKIE_NAME])


async def add_ride(
    db: Any,
    driver_id: uuid.UUID,
    *,
    start: LatLon = NORTH,
    end: LatLon = EVENT,
    route: Sequence[LatLon] | None = None,
    departure_time: datetime | None = None,
    seats_total: int = 3,
    seats_available: int | None = None,
    status: str = "open",
) -> uuid.UUID:
    """Fahrt mit zur FakeRouting-Basisroute passenden ``route_distance_m``/``route_duration_s``."""
    # eigener Fake: zählt nicht in ``route_calls`` des Routings unter Test
    base = await FakeRouting().route([start, end], geometry=True)
    line = list(route) if route is not None else list(base.geometry)
    return await insert_ride(
        db,
        driver_id,
        start=start,
        end=end,
        route=line,
        distance_m=base.distance_m,
        duration_s=base.duration_s,
        seats_total=seats_total,
        seats_available=seats_available,
        departure_time=departure_time,
        status=status,
    )


def dist_m(a: LatLon, b: Sequence[float]) -> float:
    return haversine_m(a, (b[0], b[1]))
