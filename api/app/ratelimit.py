"""Einfaches In-Memory-Rate-Limiting (gleitendes Fenster).

Grenzen (bewusst, siehe PLAN.md): Zustand liegt im Prozessspeicher – es läuft genau ein
Uvicorn-Worker; nach einem Neustart sind die Zähler leer. Schlüssel sind mit einem
prozesslokalen Salt gehashte Bezeichner (IP-Adresse, E-Mail, Nutzer-ID): keine Klartextdaten im
Speicher, nie im Log.

Härtung (Phase 7):

* **Eine Limiter-Instanz je Name** (``login``, ``register``, ``api`` …, siehe ``LimiterRegistry``):
  Eine Flut neuer Adressen im allgemeinen API-Limit kann Zähler von Login oder Registrierung nicht
  berühren – die Tabellen sind getrennt.
* **Aktive Zähler werden nie verdrängt.** Ist eine Tabelle voll, werden zuerst abgelaufene Schlüssel
  entfernt (jeder mit seinem *eigenen* Fensterende). Hilft das nicht, gilt **fail-closed**: neue
  Schlüssel teilen sich einen gemeinsamen Überlauf-Eimer mit demselben Limit (bekannte Clients mit
  bestehendem Zähler bleiben unberührt, Unbekannte werden gemeinsam gedrosselt). Ein Angreifer mit
  vielen Adressen kann so keinen Zähler zurücksetzen, nur neue Clients vorübergehend gemeinsam
  einbremsen (höchstens bis zum Ablauf des Fensters).
* IPv6-Clients erhalten ein ganzes /64-Präfix – wer innerhalb seines Präfixes die Adresse wechselt,
  umgeht das Limit nicht. IPv4-gemappte IPv6-Adressen zählen als IPv4, Zonen-Kennungen
  (``fe80::1%eth0``) werden entfernt.
* ``X-Forwarded-For`` wird aus **allen** Header-Zeilen zusammengesetzt (mehrere Zeilen sind
  gleichbedeutend mit einer kommagetrennten Zeile) und nur ausgewertet, wenn der direkte Peer
  ein vertrauter Proxy ist.
* Zusätzlich zum IP-Limit gibt es Limits je **normalisierter E-Mail** (Login) und je **Nutzer**
  (``/api/match``, Fahrt anlegen, Anfrage senden): ``check_limit`` und ``user_rate_limit``.
* Die Zeitquelle ist austauschbar (``clock``), damit Tests ohne ``sleep`` auskommen.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from fastapi import Depends, Request

from app.auth.deps import CurrentUser
from app.config import Settings, get_settings
from app.errors import ApiError

logger = logging.getLogger("app.ratelimit")

_SALT = secrets.token_bytes(16)
#: Aufräumen abgelaufener Schlüssel höchstens einmal pro Sekunde (sonst O(n) pro Zugriff).
_SWEEP_INTERVAL_S = 1.0
#: Schlüssel je Limiter-Instanz. Speicher im Höchstfall: Instanzen × Schlüssel × Limit Zeitstempel.
DEFAULT_MAX_KEYS = 50_000


class SlidingWindowLimiter:
    """Zähler für genau eine Art von Limit (eigene Tabelle, eigene Obergrenze)."""

    def __init__(
        self,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hits: dict[str, deque[float]] = {}
        #: Fensterende je Schlüssel (jeder Schlüssel hat sein eigenes Fenster)
        self._expiry: dict[str, float] = {}
        #: gemeinsamer Eimer für neue Schlüssel bei voller Tabelle (zählt nicht zu ``max_keys``)
        self._overflow: deque[float] = deque()
        self._max_keys = max_keys
        self._clock = clock
        self._last_sweep = float("-inf")
        self.overflowed = False

    def hit(
        self, key: str, limit: int, window_s: float, now: float | None = None
    ) -> tuple[bool, float]:
        """Zählt einen Zugriff. Liefert (erlaubt, Sekunden bis zum nächsten erlaubten Zugriff)."""
        now = self._clock() if now is None else now
        q = self._hits.get(key)
        if q is None:
            if len(self._hits) >= self._max_keys:
                self._sweep(now)
            if len(self._hits) >= self._max_keys:
                # fail-closed: bekannte Zähler bleiben, neue Schlüssel teilen sich einen Eimer
                self._note_overflow()
                return self._count(self._overflow, limit, window_s, now)
            q = self._hits[key] = deque()
        allowed, retry_after = self._count(q, limit, window_s, now)
        if allowed:
            self._expiry[key] = max(self._expiry.get(key, float("-inf")), now + window_s)
        return allowed, retry_after

    @staticmethod
    def _count(q: deque[float], limit: int, window_s: float, now: float) -> tuple[bool, float]:
        while q and q[0] <= now - window_s:
            q.popleft()
        if len(q) >= limit:
            return False, max(0.0, q[0] + window_s - now)
        q.append(now)
        return True, 0.0

    def _note_overflow(self) -> None:
        if not self.overflowed:
            self.overflowed = True
            logger.warning(
                "Rate-Limit-Tabelle voll (%d Schlüssel): neue Clients werden bis zum Ablauf der "
                "Fenster gemeinsam gedrosselt.",
                self._max_keys,
            )

    def _sweep(self, now: float) -> None:
        """Entfernt abgelaufene Schlüssel (gedrosselt). Aktive Schlüssel bleiben immer."""
        if now - self._last_sweep < _SWEEP_INTERVAL_S and now >= self._last_sweep:
            return
        self._last_sweep = now
        for k in [k for k, exp in self._expiry.items() if exp <= now]:
            del self._hits[k]
            del self._expiry[k]
        # Schlüssel ohne Treffer (limit <= 0 o. ä.) haben kein Fensterende
        for k in [k for k, q in self._hits.items() if not q]:
            del self._hits[k]
        if len(self._hits) < self._max_keys:
            self.overflowed = False

    def reset(self) -> None:
        self._hits.clear()
        self._expiry.clear()
        self._overflow.clear()
        self._last_sweep = float("-inf")
        self.overflowed = False


class LimiterRegistry:
    """Eine eigene :class:`SlidingWindowLimiter`-Instanz je Name (``login``, ``api`` …)."""

    def __init__(
        self,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_keys = max_keys
        self._clock = clock
        self._instances: dict[str, SlidingWindowLimiter] = {}

    def get(self, name: str) -> SlidingWindowLimiter:
        inst = self._instances.get(name)
        if inst is None:
            inst = self._instances[name] = SlidingWindowLimiter(self._max_keys, self._clock)
        return inst

    def hit(
        self, name: str, key: str, limit: int, window_s: float, now: float | None = None
    ) -> tuple[bool, float]:
        return self.get(name).hit(key, limit, window_s, now)

    def reset(self) -> None:
        """Leert alle Instanzen (Tests)."""
        for inst in self._instances.values():
            inst.reset()


limiter = LimiterRegistry()


def _parse_ip(text: str) -> IPv4Address | IPv6Address:
    """Liest eine Adresse tolerant: ohne Zone (``%eth0``), ohne Port, IPv4-gemappt → IPv4.

    Wirft ``ValueError`` bei allem, was keine Adresse ist.
    """
    text = text.strip()
    if text.startswith("["):  # [v6]:port
        end = text.find("]")
        if end == -1:
            raise ValueError("ungültige Adresse")
        text = text[1:end]
    elif text.count(":") == 1:  # v4:port
        text = text.split(":", 1)[0]
    text = text.split("%", 1)[0]
    addr = ip_address(text)
    if isinstance(addr, IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def normalize_ip(raw: str) -> str:
    """Identität für das Rate-Limit: IPv4 wie sie ist, IPv6 als /64-Präfix, sonst der Rohtext."""
    try:
        addr = _parse_ip(raw)
    except ValueError:
        return raw.strip()[:64] or "unknown"
    if isinstance(addr, IPv6Address):
        return str(ip_network(f"{addr}/64", strict=False))
    return str(addr)


def _forwarded_chain(request: Request) -> list[str]:
    # Mehrere X-Forwarded-For-Zeilen sind gleichbedeutend mit einer kommagetrennten Zeile.
    joined = ",".join(request.headers.getlist("x-forwarded-for"))
    return [p.strip() for p in joined.split(",") if p.strip()]


def client_ip(request: Request, settings: Settings) -> str:
    """Client-Adresse. X-Forwarded-For zählt nur, wenn der direkte Peer ein vertrauter Proxy ist;
    dann gilt der rechteste Eintrag, der selbst kein vertrauter Proxy ist (nicht fälschbar)."""
    peer = request.client.host if request.client else "unknown"
    networks = settings.trusted_proxy_networks
    if not networks:
        return peer
    try:
        peer_addr = _parse_ip(peer)
    except ValueError:
        return peer
    if not any(peer_addr in n for n in networks):
        return peer
    for candidate in reversed(_forwarded_chain(request)):
        try:
            addr = _parse_ip(candidate)
        except ValueError:
            return peer
        if not any(addr in n for n in networks):
            return str(addr)
    return peer


def _key(name: str, ip: str) -> str:
    """Salt-Hash der normalisierten Client-Adresse (IPv6 → /64)."""
    return _digest(name, normalize_ip(ip))


def _digest(name: str, value: str) -> str:
    return hashlib.blake2b(f"{name}|{value}".encode(), key=_SALT, digest_size=16).hexdigest()


def rate_limited_error(retry_after: float) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Zu viele Anfragen – bitte später erneut versuchen.",
        headers={"Retry-After": str(max(1, round(retry_after)))},
    )


def check_limit(name: str, identity: str, limit: int, window_s: float) -> None:
    """Zählt einen Zugriff auf ``identity`` (z. B. E-Mail, Nutzer-ID) im Limit ``name``.

    Die Identität wird nur als Salt-Hash gespeichert. Bei Überschreitung: 429 ``rate_limited``.
    """
    allowed, retry_after = limiter.hit(name, _digest(name, identity), limit, window_s)
    if not allowed:
        raise rate_limited_error(retry_after)


def rate_limit(
    name: str, limit_attr: str, window_s: float
) -> Callable[[Request, Settings], Awaitable[None]]:
    """Dependency-Fabrik: ``Depends(rate_limit("login", "rate_limit_login_per_min", 60))``.

    Limit je Client-IP. ``limit_attr`` ist der Name des Settings-Feldes (so lassen sich Grenzen in
    Tests setzen). Bei Überschreitung: 429 ``rate_limited`` mit ``Retry-After``.
    """

    async def dependency(request: Request, settings: Settings = Depends(get_settings)) -> None:  # noqa: B008
        if not settings.rate_limit_enabled:
            return
        limit = int(getattr(settings, limit_attr))
        check_limit_ip(name, client_ip(request, settings), limit, window_s)

    return dependency


def check_limit_ip(name: str, ip: str, limit: int, window_s: float) -> None:
    allowed, retry_after = limiter.hit(name, _key(name, ip), limit, window_s)
    if not allowed:
        raise rate_limited_error(retry_after)


def user_rate_limit(
    name: str, limit_attr: str, window_s: float
) -> Callable[[CurrentUser, Settings], Awaitable[None]]:
    """Dependency-Fabrik für Limits **je angemeldetem Nutzer** (zusätzlich zum IP-Limit).

    Hängt von ``CurrentUser`` ab: Anonyme bekommen zuerst 401, nie 429. So lässt sich das Limit
    nicht durch Wechsel der IP-Adresse umgehen.
    """

    async def dependency(user: CurrentUser, settings: Settings = Depends(get_settings)) -> None:  # noqa: B008
        if not settings.rate_limit_enabled:
            return
        check_limit(name, str(user.id), int(getattr(settings, limit_attr)), window_s)

    return dependency
