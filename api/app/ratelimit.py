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
  entfernt (jeder mit seinem *eigenen* Fensterende). Hilft das nicht, gilt **fail-closed**, aber
  abgestuft: neue Schlüssel (Unbekannte) zählen in einem **Überlauf** – je Gruppe (IPv6: /48,
  IPv4: die Adresse) in einem kleinen Teil-Eimer mit dem ``OVERFLOW_GROUP_FACTOR``-fachen
  Einzel-Limit und zusätzlich in einem gemeinsamen Eimer mit dem ``OVERFLOW_FACTOR``-fachen
  Einzel-Limit. Bekannte Clients mit bestehendem Zähler bleiben unberührt.

  Warum Vielfache? Früher teilten sich *alle* Neuen einen Eimer mit dem Limit eines *einzelnen*
  Schlüssels: Wer die Tabelle mit vielen Adressen füllte, drosselte damit jeden neuen Client
  (Registrierung bis zu 1 h). Jetzt reicht der gemeinsame Eimer für ein Vielfaches des
  realistischen Andrangs (Kongress: wenige Hundert Nutzer), und ein einzelner Angreifer – eine
  IPv6-Zuweisung (/48) oder eine IPv4-Adresse – kann ihn wegen der Gruppen-Eimer nicht allein
  leeren. Der Speicher bleibt strikt begrenzt: höchstens
  ``max_keys × Limit + OVERFLOW_FACTOR × Limit + MAX_OVERFLOW_GROUPS × OVERFLOW_GROUP_FACTOR ×
  Limit`` Zeitstempel je Instanz. Die Warnung im Log nennt nur den Namen der Tabelle, nie eine
  Adresse. Rest-Risiko (PLAN.md): Sehr viele *verschiedene* /48-Präfixe bzw. IPv4-Adressen können
  den gemeinsamen Eimer weiterhin ausschöpfen.
* IPv6-Clients erhalten ein ganzes /64-Präfix – wer innerhalb seines Präfixes die Adresse wechselt,
  umgeht das Limit nicht. IPv4-gemappte IPv6-Adressen zählen als IPv4, Zonen-Kennungen
  (``fe80::1%eth0``) werden entfernt.
* ``X-Forwarded-For`` wird aus **allen** Header-Zeilen zusammengesetzt (mehrere Zeilen sind
  gleichbedeutend mit einer kommagetrennten Zeile) und nur ausgewertet, wenn der direkte Peer
  ein vertrauter Proxy ist.
* Zusätzlich zum IP-Limit gibt es Limits je **Nutzer** (``/api/match``, Fahrt anlegen, Anfrage
  senden): ``check_limit`` und ``user_rate_limit``.
* Der Login zählt je **E-Mail** (und je E-Mail+IP) nur **Fehlversuche**: ``reserve_slots`` belegt
  die Plätze *vor* der (awaiten) Passwortprüfung – so können parallele Versuche das Limit nicht
  unterlaufen – und ``Reservation.release`` gibt sie bei richtigem Passwort zurück.
* Die Zeitquelle ist austauschbar (``clock``), damit Tests ohne ``sleep`` auskommen.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
#: Überlauf bei voller Tabelle: gemeinsamer Eimer für alle neuen Schlüssel = Vielfaches des
#: Einzel-Limits. 50 ×: Login 10/min → 500/min, Registrierung 10/h → 500/h, API 120/min → 6000/min
#: für *alle* Unbekannten zusammen – weit über dem Andrang auf dem Kongress, aber endlich.
OVERFLOW_FACTOR = 50
#: Teil-Eimer je Gruppe (IPv6 /48, IPv4-Adresse) im Überlauf: ein Angreifer allein erschöpft nur
#: sein 5-faches Einzel-Limit, nicht den gemeinsamen Eimer (erst zehn Gruppen leeren ihn).
OVERFLOW_GROUP_FACTOR = 5
#: Höchstzahl der Teil-Eimer je Instanz (Speichergrenze); weitere Gruppen zählen nur gemeinsam.
MAX_OVERFLOW_GROUPS = 256


class _Token:
    """Beleg eines gezählten Zugriffs: in welchen Eimern der Zeitstempel steht (für ``release``)."""

    __slots__ = ("buckets", "key", "now")

    def __init__(self, key: str, now: float, buckets: tuple[deque[float], ...]) -> None:
        self.key = key
        self.now = now
        self.buckets = buckets


class SlidingWindowLimiter:
    """Zähler für genau eine Art von Limit (eigene Tabelle, eigene Obergrenze)."""

    def __init__(
        self,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
        *,
        name: str = "",
        overflow_factor: int = OVERFLOW_FACTOR,
        group_factor: int = OVERFLOW_GROUP_FACTOR,
        max_groups: int = MAX_OVERFLOW_GROUPS,
    ) -> None:
        self._hits: dict[str, deque[float]] = {}
        #: Fensterende je Schlüssel (jeder Schlüssel hat sein eigenes Fenster)
        self._expiry: dict[str, float] = {}
        #: gemeinsamer Eimer für neue Schlüssel bei voller Tabelle (zählt nicht zu ``max_keys``)
        self._overflow: deque[float] = deque()
        #: Teil-Eimer je Gruppe im Überlauf (höchstens ``max_groups``) samt Fensterende
        self._groups: dict[str, deque[float]] = {}
        self._group_expiry: dict[str, float] = {}
        self._max_keys = max_keys
        self._clock = clock
        self._name = name
        self._overflow_factor = max(1, overflow_factor)
        self._group_factor = max(1, group_factor)
        self._max_groups = max_groups
        self._last_sweep = float("-inf")
        self.overflowed = False

    def hit(
        self,
        key: str,
        limit: int,
        window_s: float,
        now: float | None = None,
        group: str | None = None,
    ) -> tuple[bool, float]:
        """Zählt einen Zugriff. Liefert (erlaubt, Sekunden bis zum nächsten erlaubten Zugriff)."""
        token, retry_after = self.reserve(key, limit, window_s, now, group)
        return token is not None, retry_after

    def reserve(
        self,
        key: str,
        limit: int,
        window_s: float,
        now: float | None = None,
        group: str | None = None,
    ) -> tuple[_Token | None, float]:
        """Wie :meth:`hit`, liefert aber einen Beleg, mit dem sich der Zugriff zurücknehmen lässt.

        ``group`` (z. B. IPv6-/48) wirkt nur im Überlauf: dort zählt der Zugriff zusätzlich in
        einem Teil-Eimer dieser Gruppe.
        """
        now = self._clock() if now is None else now
        q = self._hits.get(key)
        if q is None:
            if len(self._hits) >= self._max_keys:
                self._sweep(now)
            if len(self._hits) >= self._max_keys:
                # fail-closed: bekannte Zähler bleiben, neue Schlüssel zählen im Überlauf
                self._note_overflow()
                return self._count_overflow(key, limit, window_s, now, group)
            q = self._hits[key] = deque()
        allowed, retry_after = self._count(q, limit, window_s, now)
        if not allowed:
            return None, retry_after
        self._expiry[key] = max(self._expiry.get(key, float("-inf")), now + window_s)
        return _Token(key, now, (q,)), 0.0

    def release(self, token: _Token) -> None:
        """Nimmt einen mit :meth:`reserve` gezählten Zugriff zurück (aus allen Eimern)."""
        for q in token.buckets:
            try:
                q.remove(token.now)
            except ValueError:  # bereits aus dem Fenster gefallen
                continue
        mine = self._hits.get(token.key)
        if mine is not None and not mine and any(mine is b for b in token.buckets):
            # leere Zähler nicht stehen lassen (Speicher, und ``_sweep`` bleibt konsistent)
            del self._hits[token.key]
            self._expiry.pop(token.key, None)

    @staticmethod
    def _count(q: deque[float], limit: int, window_s: float, now: float) -> tuple[bool, float]:
        while q and q[0] <= now - window_s:
            q.popleft()
        if len(q) >= limit:
            return False, max(0.0, q[0] + window_s - now)
        q.append(now)
        return True, 0.0

    def _count_overflow(
        self, key: str, limit: int, window_s: float, now: float, group: str | None
    ) -> tuple[_Token | None, float]:
        """Zählt in Gruppen- und gemeinsamem Eimer; nur wenn *beide* erlauben, wird gezählt."""
        buckets: list[tuple[deque[float], int]] = []
        if group is not None:
            g = self._groups.get(group)
            if g is None and len(self._groups) < self._max_groups:
                g = self._groups[group] = deque()
            if g is not None:
                self._group_expiry[group] = max(
                    self._group_expiry.get(group, float("-inf")), now + window_s
                )
                buckets.append((g, limit * self._group_factor))
        buckets.append((self._overflow, limit * self._overflow_factor))
        retry_after = 0.0
        denied = False
        for q, cap in buckets:
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= cap:
                denied = True
                retry_after = max(retry_after, q[0] + window_s - now)
        if denied:
            return None, max(0.0, retry_after)
        for q, _ in buckets:
            q.append(now)
        return _Token(key, now, tuple(q for q, _ in buckets)), 0.0

    def _note_overflow(self) -> None:
        if not self.overflowed:
            self.overflowed = True
            logger.warning(
                "Rate-Limit-Tabelle '%s' voll (%d Schlüssel): neue Clients zählen im Überlauf "
                "(gemeinsames Limit, je Gruppe begrenzt) bis zum Ablauf der Fenster.",
                self._name,
                self._max_keys,
            )

    def _sweep(self, now: float) -> None:
        """Entfernt abgelaufene Schlüssel (gedrosselt). Aktive Schlüssel bleiben immer."""
        if now - self._last_sweep < _SWEEP_INTERVAL_S and now >= self._last_sweep:
            return
        self._last_sweep = now
        for k in [k for k, exp in self._expiry.items() if exp <= now]:
            self._hits.pop(k, None)
            del self._expiry[k]
        # Schlüssel ohne Treffer (limit <= 0 o. ä.) haben kein Fensterende
        for k in [k for k, q in self._hits.items() if not q]:
            del self._hits[k]
            self._expiry.pop(k, None)
        for g in [g for g, exp in self._group_expiry.items() if exp <= now]:
            self._groups.pop(g, None)
            del self._group_expiry[g]
        if len(self._hits) < self._max_keys:
            self.overflowed = False

    def reset(self) -> None:
        self._hits.clear()
        self._expiry.clear()
        self._overflow.clear()
        self._groups.clear()
        self._group_expiry.clear()
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
            inst = self._instances[name] = SlidingWindowLimiter(
                self._max_keys, self._clock, name=name
            )
        return inst

    def hit(
        self,
        name: str,
        key: str,
        limit: int,
        window_s: float,
        now: float | None = None,
        group: str | None = None,
    ) -> tuple[bool, float]:
        return self.get(name).hit(key, limit, window_s, now, group)

    def reserve(
        self,
        name: str,
        key: str,
        limit: int,
        window_s: float,
        now: float | None = None,
        group: str | None = None,
    ) -> tuple[_Token | None, float]:
        return self.get(name).reserve(key, limit, window_s, now, group)

    def release(self, name: str, token: _Token) -> None:
        self.get(name).release(token)

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


def overflow_group(name: str, ip: str) -> str:
    """Gruppe für den Überlauf bei voller Tabelle: IPv6 grob als /48, IPv4 die Adresse selbst.

    Wer eine ganze IPv6-Zuweisung besitzt, erzeugt beliebig viele /64-Schlüssel – im Überlauf
    zählen sie alle in *einem* Teil-Eimer. Salt-Hash wie alle Schlüssel (nie die Adresse im
    Speicher oder Log).
    """
    try:
        addr = _parse_ip(ip)
    except ValueError:
        return _digest(name, "group|" + (ip.strip()[:64] or "unknown"))
    if isinstance(addr, IPv6Address):
        return _digest(name, "group|" + str(ip_network(f"{addr}/48", strict=False)))
    return _digest(name, "group|" + str(addr))


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
    allowed, retry_after = limiter.hit(
        name, _key(name, ip), limit, window_s, group=overflow_group(name, ip)
    )
    if not allowed:
        raise rate_limited_error(retry_after)


@dataclass(frozen=True)
class Slot:
    """Ein Zähler für :func:`reserve_slots`: Limit ``name`` für ``identity`` (+ optional IP)."""

    name: str
    identity: str
    limit: int
    window_s: float
    #: Wenn gesetzt, zählt die Identität je IP (``identity|IP/64``) und gruppiert im Überlauf
    ip: str | None = None


class Reservation:
    """Belegte Plätze; :meth:`release` gibt sie zurück (z. B. nach erfolgreicher Anmeldung)."""

    def __init__(self) -> None:
        self._taken: list[tuple[str, _Token]] = []

    def release(self) -> None:
        taken, self._taken = self._taken, []
        for name, token in taken:
            limiter.release(name, token)


def reserve_slots(slots: list[Slot]) -> Reservation:
    """Belegt der Reihe nach je einen Platz; verweigert ein Zähler, wird alles zurückgenommen.

    Gedacht für „nur Fehlversuche zählen“: Plätze **vor** der Prüfung belegen (synchron, ohne
    ``await`` dazwischen – parallele Versuche können das Limit nicht unterlaufen) und bei Erfolg
    ``release()``. Bei Verweigerung: 429 ``rate_limited``. Die Reihenfolge zählt: Ein enger Zähler
    (E-Mail+IP) vor einem weiten (E-Mail) verhindert, dass ein einzelner Angreifer den weiten
    Zähler füllt – er wird davor abgewiesen und zählt dort nicht.
    """
    reservation = Reservation()
    for slot in slots:
        if slot.ip is None:
            key, group = _digest(slot.name, slot.identity), None
        else:
            key = _digest(slot.name, f"{slot.identity}|{normalize_ip(slot.ip)}")
            group = overflow_group(slot.name, slot.ip)
        token, retry_after = limiter.reserve(slot.name, key, slot.limit, slot.window_s, group=group)
        if token is None:
            reservation.release()
            raise rate_limited_error(retry_after)
        reservation._taken.append((slot.name, token))
    return reservation


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
