"""Adresssuche (Geocoding) über einen Nominatim-Server – ausschließlich serverseitig.

Datenschutz: Der Browser fragt nur die eigene API (``GET /api/geocode``). Der Geocoder sieht
deshalb nur die Server-Adresse, nie die der Nutzer, und bekommt weder Cookies noch Referer.
Der Suchtext (eine Adresse!) wird nicht gespeichert und nicht protokolliert: weder im
Anfrage-Log (nur das Routen-Template) noch im httpx-Log (auf WARNING gestellt, siehe
``app.middleware.security``). Der Zwischenspeicher liegt nur im Arbeitsspeicher, ist kurz, gilt
je Nutzer (sonst verriete ein schneller Treffer, was andere gesucht haben) und ist unter einem
Hash abgelegt (kein Klartext-Schlüssel).

Nutzungsregeln des öffentlichen Nominatim-Dienstes, die hier eingehalten werden: höchstens eine
Anfrage pro Sekunde (``_reserve_slot``), eine erkennbare Anwendungskennung als User-Agent,
Zwischenspeicherung gleicher Anfragen und keine Suche bei jedem Tastendruck (Button im Frontend).
Für viel Last gehört ein eigener Server her (``GEOCODER_URL``).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
from fastapi import Depends

from app.config import Settings, get_settings

#: Mehr Treffer sind in der Oberfläche nicht sinnvoll.
MAX_RESULTS = 5
_MAX_LABEL_LEN = 200
_CACHE_MAX_ENTRIES = 500


class GeocoderError(Exception):
    """Basis der Geocoder-Fehler."""


class GeocoderUnavailableError(GeocoderError):
    """Der Geocoder ist nicht erreichbar oder antwortet unbrauchbar (→ API 503)."""


class GeocoderBusyError(GeocoderError):
    """Die Warteschlange der Drossel ist zu lang (→ API 503, bitte später erneut)."""


@dataclass(frozen=True)
class GeocodeHit:
    label: str
    lat: float
    lon: float


class Geocoder:
    """Schnittstelle; Tests ersetzen sie über ``app.dependency_overrides[get_geocoder]``."""

    async def search(  # pragma: no cover - Schnittstelle
        self, query: str, *, scope: str = ""
    ) -> list[GeocodeHit]:
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - Schnittstelle
        return None


def _normalize(query: str) -> str:
    return " ".join(query.casefold().split())


def _parse_hit(entry: Any) -> GeocodeHit | None:
    if not isinstance(entry, dict):
        return None
    try:
        lat = float(entry["lat"])
        lon = float(entry["lon"])
        label = entry["display_name"]
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if not isinstance(label, str) or not label.strip():
        return None
    return GeocodeHit(label=label.strip()[:_MAX_LABEL_LEN], lat=lat, lon=lon)


class NominatimClient(Geocoder):
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 5.0,
        user_agent: str,
        country_codes: str = "de",
        cache_ttl_s: float = 600.0,
        min_interval_s: float = 1.0,
        max_wait_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            # keine Cookies, keine Weiterleitungen auf fremde Hosts
            follow_redirects=False,
            transport=transport,
        )
        self._country_codes = country_codes
        self._cache_ttl_s = cache_ttl_s
        self._min_interval_s = min_interval_s
        self._max_wait_s = max_wait_s
        self._clock = clock
        self._sleep = sleep
        self._next_slot = 0.0
        self._cache: OrderedDict[str, tuple[float, list[GeocodeHit]]] = OrderedDict()

    # --- Zwischenspeicher ---------------------------------------------------------------------

    @staticmethod
    def _cache_key(query: str, scope: str) -> str:
        # Der Schlüssel enthält den Nutzer (``scope``): Treffer gibt es nur für dieselbe Person.
        # Ein gemeinsamer Cache würde verraten (schneller Treffer statt Drossel-Wartezeit), dass
        # jemand anderes diese Adresse kürzlich gesucht hat.
        return hashlib.sha256(f"{scope}\0{_normalize(query)}".encode()).hexdigest()

    def _cache_get(self, key: str) -> list[GeocodeHit] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires, hits = entry
        if self._clock() >= expires:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return hits

    def _cache_put(self, key: str, hits: list[GeocodeHit]) -> None:
        self._cache[key] = (self._clock() + self._cache_ttl_s, hits)
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)

    # --- Drossel ------------------------------------------------------------------------------

    def _reserve_slot(self) -> float:
        """Reserviert den nächsten freien Abfragezeitpunkt und gibt die Wartezeit zurück.

        Synchron (kein ``await``): parallele Anfragen bekommen verschiedene Plätze. Ist der
        Platz weiter weg als ``max_wait_s``, wird nichts reserviert (→ ``GeocoderBusyError``).
        """
        now = self._clock()
        slot = max(now, self._next_slot)
        wait = slot - now
        if wait > self._max_wait_s:
            raise GeocoderBusyError("Geocoder ausgelastet")
        self._next_slot = slot + self._min_interval_s
        return wait

    # --- Suche --------------------------------------------------------------------------------

    async def search(self, query: str, *, scope: str = "") -> list[GeocodeHit]:
        key = self._cache_key(query, scope)
        cached = self._cache_get(key)
        if cached is not None:
            return list(cached)

        wait = self._reserve_slot()
        if wait > 0:
            await self._sleep(wait)

        params: dict[str, str | int] = {
            "q": query,
            "format": "jsonv2",
            "limit": MAX_RESULTS,
            "countrycodes": self._country_codes,
            "accept-language": "de",
            "addressdetails": 0,
        }
        try:
            response = await self._client.get("/search", params=params)
        except httpx.HTTPError as exc:
            raise GeocoderUnavailableError("Geocoder nicht erreichbar") from exc
        if response.status_code != 200:
            raise GeocoderUnavailableError(f"Geocoder-Status {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise GeocoderUnavailableError("Geocoder lieferte kein JSON") from exc
        if not isinstance(data, list):
            raise GeocoderUnavailableError("Unerwartete Geocoder-Antwort")

        hits = [h for h in (_parse_hit(e) for e in data) if h is not None][:MAX_RESULTS]
        self._cache_put(key, hits)
        return list(hits)

    async def aclose(self) -> None:
        await self._client.aclose()


# --- FastAPI-Dependency -----------------------------------------------------------------------

_client: Geocoder | None = None


def build_geocoder(settings: Settings) -> Geocoder | None:
    """``None`` = Adresssuche aus. ``photon`` ist (noch) nicht angebunden und wirkt wie ``none``."""
    if settings.geocoder != "nominatim":
        return None
    return NominatimClient(
        settings.geocoder_url,
        timeout_s=settings.geocoder_timeout_s,
        user_agent=settings.geocoder_user_agent,
        country_codes=settings.geocoder_country_codes,
        cache_ttl_s=settings.geocoder_cache_ttl_s,
    )


def get_geocoder(settings: Annotated[Settings, Depends(get_settings)]) -> Geocoder | None:
    """FastAPI-Dependency; in Tests per ``app.dependency_overrides`` ersetzbar."""
    global _client
    if settings.geocoder != "nominatim":
        return None
    if _client is None:
        _client = build_geocoder(settings)
    return _client


async def close_geocoder() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
