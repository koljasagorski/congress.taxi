"""Security-Schicht der API (Phase 7): Header, Origin-Prüfung, globales Rate-Limit.

Reine ASGI-Middleware (kein ``BaseHTTPMiddleware``): Sie sitzt innerhalb der Request-Log-Middleware
aus ``app/main.py`` und vor den Routern. Reihenfolge je Request:

1. **Origin-Prüfung** (nur ``POST/PUT/PATCH/DELETE``): Trägt die Anfrage einen ``Origin``-Header,
   muss er in ``Settings.allowed_origin_list`` stehen (Vergleich ohne Groß-/Kleinschreibung und ohne
   abschließenden ``/``); sonst ``403 forbidden``. ``Origin: null`` ist fremd. **Ohne** ``Origin``
   kein Fehler (Skripte, Tests; die Cookies sind ``SameSite=Strict``).
2. **Rate-Limit** je Client-IP (nur wenn ``rate_limit_enabled``): ``rate_limit_api_per_min`` für
   alles unter ``/api`` außer der Liveness ``/api/health`` (Docker-Healthcheck); zusätzlich
   ``rate_limit_match_per_min`` für ``/api/match`` und ``READY_PER_MIN`` für
   ``/api/health/ready``. Überschreitung: ``429 rate_limited`` mit ``Retry-After``.
3. **Body-Obergrenze** (``MAX_BODY_BYTES``): Ein größerer ``Content-Length`` wird sofort mit
   ``413 payload_too_large`` abgewiesen, ohne den Body zu lesen. Fehlt der Header (chunked) oder
   lügt er, zählt ein ``receive``-Wrapper die gelesenen Bytes mit und bricht bei Überschreitung ab
   (FastAPI liest sonst jeden Body vollständig in den Speicher). Das gilt unabhängig davon, ob ein
   Caddy davor sitzt (der Rand-Caddy in ``deploy/hel/`` begrenzt nichts).
4. Router. Fehler, die dort nicht behandelt werden, rendert der registrierte
   ``Exception``-Handler (``500 internal_error``) – hier, damit auch diese Antwort die Header trägt
   und der Fehler nicht weiter nach oben propagiert (Server-Tracebacks können SQL-Parameter, also
   E-Mail-Adressen oder Koordinaten, enthalten).

Die Header stehen an **jeder** Antwort (auch 403/429/404/500). ``Cache-Control: no-store`` gilt
für alles unter ``/api``.

Es wird nichts geloggt: weder IP noch Query noch Origin. Einzige Ausnahme: ein einmaliger
Hinweis (``app.security``, ohne Adressen), wenn ``TRUSTED_PROXIES`` leer ist, aber Anfragen von
privaten Adressen mit ``X-Forwarded-For`` ankommen (typisch: Caddy im Docker-Netz) – dann teilen
sich alle Nutzer ein Rate-Limit.

``install_security`` schaltet außerdem die Logger ``httpx`` und ``httpcore`` auf WARNING: httpx
protokolliert auf INFO jede Anfrage mit vollständiger URL, bei OSRM also mit Koordinaten.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from ipaddress import ip_network
from typing import Any

from fastapi import FastAPI, Request
from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, get_settings
from app.errors import error_response
from app.ratelimit import _key, _parse_ip, client_ip, limiter

logger = logging.getLogger("app.security")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
}
API_CACHE_CONTROL = "no-store"

_API_WINDOW_S = 60.0

#: Obergrenze für Request-Bodies. Alle JSON-Bodies der API sind winzig; passend zum Caddy-Limit.
MAX_BODY_BYTES = 64 * 1024
#: ``/api/health/ready`` belegt eine DB-Verbindung und ruft OSRM auf: eigenes, strenges Limit.
READY_PER_MIN = 10

#: Logger, die Anfrage-URLs (mit Koordinaten) protokollieren würden.
_QUIET_LOGGERS = ("httpx", "httpcore")

_proxy_warning_emitted = False
#: Adressen, hinter denen ein Reverse Proxy im selben (Docker-/LAN-)Netz stehen kann.
#: (``ipaddress.is_private`` zählt auch Dokumentationsnetze wie 198.51.100.0/24 dazu.)
_PROXY_PEER_NETWORKS = tuple(
    ip_network(n)
    for n in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
        "::1/128", "fc00::/7", "fe80::/10",
    )
)  # fmt: skip


class _BodyTooLargeError(Exception):
    """Wird vom ``receive``-Wrapper geworfen, sobald der Body die Obergrenze überschreitet."""


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_liveness(path: str) -> bool:
    """Nur die Liveness ist vom Limit ausgenommen (Docker-Healthcheck), nichts darunter."""
    return path.rstrip("/") == "/api/health"


def _is_ready(path: str) -> bool:
    return path.rstrip("/") == "/api/health/ready"


def _is_match(path: str) -> bool:
    return path.rstrip("/") == "/api/match"


def _too_large_response() -> Response:
    return error_response(
        413, "payload_too_large", "Die Anfrage ist zu groß.", headers={"Connection": "close"}
    )


def _warn_if_proxy_not_trusted(request: Request, settings: Settings) -> None:
    """Einmaliger Hinweis (ohne Adressen): Peer ist privat und schickt X-Forwarded-For, aber
    ``TRUSTED_PROXIES`` ist leer – hinter einem Reverse Proxy zählen dann alle als ein Client."""
    global _proxy_warning_emitted
    if _proxy_warning_emitted or settings.trusted_proxy_networks:
        return
    if "x-forwarded-for" not in request.headers or request.client is None:
        return
    try:
        peer = _parse_ip(request.client.host)
    except ValueError:
        return
    if any(peer in net for net in _PROXY_PEER_NETWORKS):
        _proxy_warning_emitted = True
        logger.warning(
            "X-Forwarded-For von einer privaten Adresse, aber TRUSTED_PROXIES ist leer: "
            "alle Clients teilen sich ein Rate-Limit. TRUSTED_PROXIES auf das Netz des "
            "Reverse Proxys setzen (nur dessen feste Adresse, nie ein ganzes Docker-Netz; "
            "mit dem Compose-Profil proxy setzt docker-compose.proxy.yml das automatisch)."
        )


def _norm_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


class SecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings_provider: Callable[[], Settings],
        unhandled_handler: Callable[[], Callable[[Request, Exception], Awaitable[Response]] | None],
    ) -> None:
        self.app = app
        self._settings = settings_provider
        self._unhandled_handler = unhandled_handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        api = _is_api(path)
        started = False
        too_large = False
        total = 0

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if api:
                    headers["Cache-Control"] = API_CACHE_CONTROL
            await send(message)

        async def send_guarded(message: Message) -> None:
            # Wurde der Body abgebrochen, ist die Antwort der App (FastAPI macht aus dem Abbruch
            # ein 400) wertlos – sie wird durch das 413 unten ersetzt.
            if too_large and not started:
                return
            await send_with_headers(message)

        async def limited_receive() -> Message:
            nonlocal total, too_large
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > MAX_BODY_BYTES:
                    too_large = True
                    raise _BodyTooLargeError
            return message

        settings = self._settings()
        request = Request(scope, receive)

        blocked = (
            self._check_origin(request, settings)
            or self._check_rate_limit(request, settings, path)
            or self._check_content_length(request)
        )
        if blocked is not None:
            await blocked(scope, receive, send_with_headers)
            return

        try:
            await self.app(scope, limited_receive, send_guarded)
        except Exception as exc:
            if started:
                raise  # Antwort läuft schon – nichts mehr zu retten
            if too_large:
                response: Response = _too_large_response()
            else:
                # Der registrierte Handler loggt nur den Exception-Typ (Standardformat).
                handler = self._unhandled_handler()
                if handler is None:
                    response = error_response(500, "internal_error", "Interner Fehler")
                else:
                    response = await handler(request, exc)
            await response(scope, receive, send_with_headers)
        else:
            if too_large and not started:
                await _too_large_response()(scope, receive, send_with_headers)

    @staticmethod
    def _check_content_length(request: Request) -> Response | None:
        declared = request.headers.get("content-length")
        if declared is None:
            return None
        try:
            size = int(declared)
        except ValueError:
            return None  # kaputter Header: der Server lehnt ihn ab, sonst zählt der Wrapper mit
        return _too_large_response() if size > MAX_BODY_BYTES else None

    @staticmethod
    def _check_origin(request: Request, settings: Settings) -> Response | None:
        if request.method not in UNSAFE_METHODS:
            return None
        origins = request.headers.getlist("origin")
        if not origins:
            return None
        allowed = {_norm_origin(o) for o in settings.allowed_origin_list}
        if all(_norm_origin(o) in allowed for o in origins):
            return None
        return error_response(403, "forbidden", "Anfrage von dieser Herkunft ist nicht erlaubt.")

    @staticmethod
    def _check_rate_limit(request: Request, settings: Settings, path: str) -> Response | None:
        if not settings.rate_limit_enabled or not _is_api(path) or _is_liveness(path):
            return None
        _warn_if_proxy_not_trusted(request, settings)
        ip = client_ip(request, settings)
        checks = [("api", settings.rate_limit_api_per_min)]
        if _is_match(path):
            checks.append(("match", settings.rate_limit_match_per_min))
        if _is_ready(path):
            checks.append(("ready", READY_PER_MIN))
        for name, limit in checks:
            allowed, retry_after = limiter.hit(f"mw-{name}", _key(name, ip), limit, _API_WINDOW_S)
            if not allowed:
                return error_response(
                    429,
                    "rate_limited",
                    "Zu viele Anfragen – bitte später erneut versuchen.",
                    headers={"Retry-After": str(max(1, round(retry_after)))},
                )
        return None


def install_security(app: FastAPI) -> None:
    """Hängt die Security-Middleware ein (Aufruf in ``app/main.py``)."""
    for name in _QUIET_LOGGERS:
        quiet = logging.getLogger(name)
        if quiet.level < logging.WARNING:  # inkl. NOTSET (0)
            quiet.setLevel(logging.WARNING)

    def settings_provider() -> Settings:
        # Dependency-Overrides gelten auch hier (so schalten Tests Limits gezielt ein).
        provider: Any = app.dependency_overrides.get(get_settings, get_settings)
        settings: Settings = provider()
        return settings

    def unhandled_handler() -> Callable[[Request, Exception], Awaitable[Response]] | None:
        handler: Any = app.exception_handlers.get(Exception)
        return handler  # type: ignore[no-any-return]

    app.add_middleware(
        SecurityMiddleware,
        settings_provider=settings_provider,
        unhandled_handler=unhandled_handler,
    )
