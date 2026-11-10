"""Einfaches In-Memory-Rate-Limiting (gleitendes Fenster).

Grenzen (bewusst, siehe PLAN.md): Zustand liegt im Prozessspeicher – es läuft genau ein
Uvicorn-Worker; nach einem Neustart sind die Zähler leer. Schlüssel sind mit einem
prozesslokalen Salt gehashte IP-Adressen (keine Klartext-IP im Speicher, nie im Log).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from ipaddress import ip_address

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.errors import ApiError

_SALT = secrets.token_bytes(16)


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = 50_000) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._max_keys = max_keys

    def hit(
        self, key: str, limit: int, window_s: float, now: float | None = None
    ) -> tuple[bool, float]:
        """Zählt einen Zugriff. Liefert (erlaubt, Sekunden bis zum nächsten erlaubten Zugriff)."""
        now = time.monotonic() if now is None else now
        q = self._hits.get(key)
        if q is None:
            if len(self._hits) >= self._max_keys:
                self._evict(now, window_s)
            q = self._hits[key] = deque()
        while q and q[0] <= now - window_s:
            q.popleft()
        if len(q) >= limit:
            return False, max(0.0, q[0] + window_s - now)
        q.append(now)
        return True, 0.0

    def _evict(self, now: float, window_s: float) -> None:
        # Speicherobergrenze: abgelaufene Schlüssel verwerfen, sonst den ältesten
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - window_s]:
            del self._hits[k]
        while len(self._hits) >= self._max_keys:
            self._hits.pop(next(iter(self._hits)))

    def reset(self) -> None:
        self._hits.clear()


limiter = SlidingWindowLimiter()


def client_ip(request: Request, settings: Settings) -> str:
    """Client-Adresse. X-Forwarded-For zählt nur, wenn der direkte Peer ein vertrauter Proxy ist;
    dann gilt der rechteste Eintrag, der selbst kein vertrauter Proxy ist (nicht fälschbar)."""
    peer = request.client.host if request.client else "unknown"
    networks = settings.trusted_proxy_networks
    try:
        if not networks or not any(ip_address(peer) in n for n in networks):
            return peer
    except ValueError:
        return peer
    forwarded = [
        p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
    ]
    for candidate in reversed(forwarded):
        try:
            if not any(ip_address(candidate) in n for n in networks):
                return candidate
        except ValueError:
            return peer
    return peer


def _key(name: str, ip: str) -> str:
    return hashlib.blake2b(f"{name}|{ip}".encode(), key=_SALT, digest_size=16).hexdigest()


def rate_limit(
    name: str, limit_attr: str, window_s: float
) -> Callable[[Request, Settings], Awaitable[None]]:
    """Dependency-Fabrik: ``Depends(rate_limit("login", "rate_limit_login_per_min", 60))``.

    ``limit_attr`` ist der Name des Settings-Feldes (so lassen sich Grenzen in Tests setzen).
    Bei Überschreitung: 429 ``rate_limited`` mit ``Retry-After``.
    """

    async def dependency(request: Request, settings: Settings = Depends(get_settings)) -> None:  # noqa: B008
        if not settings.rate_limit_enabled:
            return
        limit = int(getattr(settings, limit_attr))
        allowed, retry_after = limiter.hit(
            _key(name, client_ip(request, settings)), limit, window_s
        )
        if not allowed:
            raise ApiError(
                429,
                "rate_limited",
                "Zu viele Anfragen – bitte später erneut versuchen.",
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )

    return dependency
