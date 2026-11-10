#!/usr/bin/env python3
"""Demo-Daten für die Entwicklung anlegen (Aufruf: ``make seed-demo``).

Das Skript spricht ausschließlich die laufende API über HTTP an (kein Datenbank-Direktzugriff):

* legt Demo-Nutzer mit ``@demo.invalid``-Adressen an (Registrierung antwortet 202 ohne Auto-Login,
  deshalb danach Login) und
* legt Fahrer-Fahrten rund um das Hamburg-Dev-Extrakt an (morgen/übermorgen, verschiedene Startorte
  im Stadtgebiet und am Rand),
* gibt am Ende die Demo-Zugangsdaten und Beispiel-Koordinaten für die Mitfahrt-Suche aus.

Idempotent: Wer sich schon anmelden kann, wird nicht erneut registriert (schont das
Registrierungs-Limit); wer bereits eine kommende, nicht stornierte Fahrt hat, bekommt keine weitere.

Sicherheit: verweigert den Lauf, wenn ``APP_ENV=production`` (Prozessumgebung oder ``.env`` im
Repo-Wurzelverzeichnis) und – ohne ``--allow-remote`` – gegen Nicht-Loopback-Ziele. Die
Demo-Passwörter sind absichtlich öffentlich; die Konten existieren nur in Entwicklungsumgebungen.

Nur Standardbibliothek und ``httpx`` (Aufruf: ``cd api && uv run python ../scripts/seed_demo.py``).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time as time_module
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
#: RFC 2606/6761 (nie zustellbar). Die Registrierung erlaubt ``.invalid`` nur außerhalb von
#: ``APP_ENV=production`` (``app/schemas/auth.py``); in Produktion läuft dieses Skript ohnehin nie.
DEMO_DOMAIN = "demo.invalid"
#: Absichtlich öffentlich (nur Entwicklung); erfüllt die Vorgaben 10–128 Zeichen.
DEMO_PASSWORD = "demo-passwort-40c3"  # noqa: S105
COOKIE_NAME = "access_token"
REQUEST_TIMEOUT_S = 15.0
#: Login ist auf 10/min begrenzt (und zählt auch Fehlversuche): bis zu dieser Wartezeit warten wir.
MAX_LOGIN_WAIT_S = 90.0
LOGIN_ATTEMPTS = 3


class SeedError(Exception):
    """Fehler mit verständlicher Meldung (enthält nie Passwörter oder Tokens)."""


class RefusedError(SeedError):
    """Der Lauf wurde bewusst verweigert (Produktion, fremdes Ziel)."""


@dataclass(frozen=True, slots=True)
class DemoUser:
    email: str
    display_name: str
    role: str  # "driver" | "rider"
    password: str = DEMO_PASSWORD


@dataclass(frozen=True, slots=True)
class DemoRide:
    driver_email: str
    place: str
    start: tuple[float, float]  # (lat, lon)
    day_offset: int  # 1 = morgen, 2 = übermorgen
    departure_utc: time
    seats: int


#: Startorte liegen im Hamburg-Extrakt (Straße < 500 m); Ziel ist das konfigurierte Event.
_DRIVER_PLACES: tuple[tuple[str, str, tuple[float, float], int, time, int], ...] = (
    ("bergedorf", "Bergedorf", (53.4890, 10.2150), 1, time(6, 30), 3),
    ("poppenbuettel", "Poppenbüttel", (53.6560, 10.0840), 1, time(7, 15), 2),
    ("harburg", "Harburg", (53.4609, 9.9831), 1, time(8, 0), 4),
    ("neugraben", "Neugraben", (53.4750, 9.8500), 2, time(6, 45), 3),
    ("volksdorf", "Volksdorf", (53.6500, 10.1700), 2, time(7, 30), 2),
    ("rahlstedt", "Rahlstedt", (53.6000, 10.1500), 2, time(8, 15), 1),
)

DEMO_DRIVERS: tuple[DemoUser, ...] = tuple(
    DemoUser(f"demo-fahrer-{slug}@{DEMO_DOMAIN}", f"Demo Fahrt {place}", "driver")
    for slug, place, *_ in _DRIVER_PLACES
)
DEMO_RIDER = DemoUser(f"demo-mitfahrer@{DEMO_DOMAIN}", "Demo Mitfahrer", "rider")
DEMO_USERS: tuple[DemoUser, ...] = (*DEMO_DRIVERS, DEMO_RIDER)

DEMO_RIDES: tuple[DemoRide, ...] = tuple(
    DemoRide(f"demo-fahrer-{slug}@{DEMO_DOMAIN}", place, start, day, at, seats)
    for slug, place, start, day, at, seats in _DRIVER_PLACES
)

#: Beispiel-Abholpunkte für die Mitfahrt-Suche (ebenfalls im Extrakt).
SAMPLE_PICKUPS: tuple[tuple[str, tuple[float, float]], ...] = (
    ("Altona", (53.5511, 9.9265)),
    ("Wilhelmsburg", (53.5000, 10.0100)),
    ("Wandsbek", (53.5711, 10.0820)),
)


# --- Reine Hilfen (ohne Netzwerk) -------------------------------------------------------------


def read_env_file_value(path: Path, key: str) -> str | None:
    """Liest ``KEY=wert`` aus einer ``.env``-Datei (Kommentare und Anführungszeichen ok)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    value: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, rest = line.partition("=")
        if name.strip() == key:
            value = rest.strip().strip("'\"")
    return value


def refuse_in_production(environ: Mapping[str, str], env_file: Path | None) -> None:
    """Wirft :class:`RefusedError`, wenn Prozessumgebung ODER ``.env`` auf Produktion steht."""
    values = [environ.get("APP_ENV")]
    if env_file is not None:
        values.append(read_env_file_value(env_file, "APP_ENV"))
    if any(v is not None and v.strip().lower() == "production" for v in values):
        raise RefusedError(
            "APP_ENV=production: Demo-Daten werden in Produktion nie angelegt. Abbruch."
        )


def check_target(base_url: str, *, allow_remote: bool) -> None:
    host = urlparse(base_url).hostname or ""
    if not host:
        raise SeedError(f"Ungültige API-Adresse: {base_url!r}")
    if allow_remote:
        return
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback:
        raise RefusedError(
            "Das Ziel ist kein Loopback-Host. Demo-Daten gehören nur auf Entwicklungsumgebungen; "
            "mit --allow-remote bewusst freigeben."
        )


def default_base_url(environ: Mapping[str, str], env_file: Path | None) -> str:
    explicit = environ.get("SEED_API_URL")
    if explicit:
        return explicit.rstrip("/")
    port = environ.get("API_PORT") or (
        read_env_file_value(env_file, "API_PORT") if env_file is not None else None
    )
    return f"http://127.0.0.1:{port or '8000'}"


def departure_time(ride: DemoRide, now: datetime) -> datetime:
    """Abfahrt (UTC) am Tag ``now + day_offset`` zur gewünschten Uhrzeit – immer in der Zukunft."""
    day = (now.astimezone(UTC) + timedelta(days=ride.day_offset)).date()
    return datetime.combine(day, ride.departure_utc, tzinfo=UTC)


# --- HTTP-Ablauf ------------------------------------------------------------------------------


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
        return str(body["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return "unbekannt"


def _fail(action: str, response: httpx.Response) -> SeedError:
    """Verständliche Meldung; zeigt nur Status und Fehlercode, nie Body-Inhalte."""
    status = response.status_code
    if status == 429:
        wait = response.headers.get("retry-after", "?")
        return SeedError(
            f"{action}: Rate-Limit erreicht (Retry-After: {wait} s). Später erneut versuchen "
            "oder das Limit in der Entwicklungs-.env anheben."
        )
    if status == 503:
        return SeedError(f"{action}: Dienst nicht verfügbar (503) – laufen API, DB und OSRM?")
    return SeedError(f"{action}: unerwartete Antwort {status} ({_error_code(response)})")


def _token_from(response: httpx.Response) -> str | None:
    token = response.cookies.get(COOKIE_NAME)
    return token or None


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def login(
    client: httpx.Client,
    user: DemoUser,
    *,
    sleep: Callable[[float], None] = time_module.sleep,
) -> bool:
    """True bei Erfolg (Cookie am Client gesetzt), False bei 401. Sonst :class:`SeedError`.

    Bei 429 (Login-Limit) wird kurz gewartet und wiederholt; lange Sperren brechen mit Hinweis ab.
    """
    for attempt in range(LOGIN_ATTEMPTS):
        response = client.post(
            "/api/auth/login", json={"email": user.email, "password": user.password}
        )
        wait = _retry_after(response) if response.status_code == 429 else None
        if wait is not None and wait <= MAX_LOGIN_WAIT_S and attempt < LOGIN_ATTEMPTS - 1:
            print(f"  Login-Limit erreicht, warte {wait:.0f} s ...", file=sys.stderr)
            sleep(wait + 1)
            continue
        break
    if response.status_code == 401:
        return False
    if response.status_code != 200:
        raise _fail("Anmeldung", response)
    token = _token_from(response)
    if token is None:
        raise SeedError("Anmeldung: Antwort ohne Sitzungs-Cookie")
    # Das Cookie wird ohne Secure-Flag neu gesetzt: Läuft die API mit COOKIE_SECURE=true hinter
    # http://, würde httpx das Original sonst nie zurücksenden und jede Folgeanfrage wäre ein 401.
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, token)
    return True


def ensure_account(
    client: httpx.Client,
    user: DemoUser,
    *,
    sleep: Callable[[float], None] = time_module.sleep,
) -> bool:
    """Meldet an; registriert nur bei 401. Gibt zurück, ob neu registriert wurde."""
    if login(client, user, sleep=sleep):
        return False
    response = client.post(
        "/api/auth/register",
        json={
            "email": user.email,
            "password": user.password,
            "display_name": user.display_name,
            "consent": True,
        },
    )
    if response.status_code == 422:
        raise SeedError(
            "Registrierung: Eingabe abgelehnt (422). Mögliche Ursache: Die API läuft mit "
            f"APP_ENV=production und lehnt die Demo-Domain @{DEMO_DOMAIN} ab (reservierte Domains "
            "sind nur in development/test erlaubt) oder die Passwort-Vorgaben haben sich geändert."
        )
    if response.status_code != 202:
        raise _fail("Registrierung", response)
    if not login(client, user, sleep=sleep):
        raise SeedError(
            "Anmeldung nach der Registrierung abgelehnt – existiert das Konto bereits mit einem "
            "anderen Passwort? Demo-Konten nur in einer frischen Entwicklungsdatenbank anlegen."
        )
    return True


def _has_upcoming_ride(client: httpx.Client, now: datetime) -> bool:
    response = client.get("/api/rides/mine", params={"limit": 100})
    if response.status_code != 200:
        raise _fail("Eigene Fahrten lesen", response)
    for item in response.json().get("items", []):
        if item.get("status") == "cancelled":
            continue
        departure = datetime.fromisoformat(str(item["departure_time"]).replace("Z", "+00:00"))
        if departure > now:
            return True
    return False


def ensure_ride(client: httpx.Client, ride: DemoRide, now: datetime) -> bool:
    """Legt die Fahrt an, falls die Person keine kommende hat. Gibt zurück, ob angelegt wurde."""
    if _has_upcoming_ride(client, now):
        return False
    response = client.post(
        "/api/rides",
        json={
            "start": {"lat": ride.start[0], "lon": ride.start[1]},
            "departure_time": departure_time(ride, now).isoformat(),
            "seats": ride.seats,
        },
    )
    if response.status_code != 201:
        raise _fail(f"Fahrt ab {ride.place} anlegen", response)
    return True


@dataclass(slots=True)
class SeedReport:
    users_registered: int = 0
    users_existing: int = 0
    rides_created: int = 0
    rides_skipped: int = 0
    match_probe: int | None = None
    notes: list[str] = field(default_factory=list)


def seed(
    base_url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time_module.sleep,
) -> SeedReport:
    """Führt den gesamten Ablauf aus. ``transport`` erlaubt Tests mit ``httpx.MockTransport``."""
    moment = now or datetime.now(UTC)
    report = SeedReport()

    def new_client() -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=transport, timeout=REQUEST_TIMEOUT_S)

    rides_by_driver = {r.driver_email: r for r in DEMO_RIDES}
    try:
        for user in DEMO_USERS:
            with new_client() as client:
                if ensure_account(client, user, sleep=sleep):
                    report.users_registered += 1
                else:
                    report.users_existing += 1
                ride = rides_by_driver.get(user.email)
                if ride is not None:
                    if ensure_ride(client, ride, moment):
                        report.rides_created += 1
                    else:
                        report.rides_skipped += 1
                elif user.role == "rider":
                    report.match_probe = _probe_match(client, report)
    except httpx.HTTPError as exc:
        raise SeedError(
            f"API nicht erreichbar unter {base_url} ({type(exc).__name__}). "
            "Läuft der Stack (make up)? Port: API_PORT / --base-url."
        ) from None
    return report


def _probe_match(client: httpx.Client, report: SeedReport) -> int | None:
    """Kontrollsuche für die erste Beispiel-Koordinate; Fehler hier sind nur ein Hinweis."""
    name, (lat, lon) = SAMPLE_PICKUPS[0]
    response = client.get("/api/match", params={"lat": lat, "lon": lon})
    if response.status_code != 200:
        report.notes.append(
            f"Kontrollsuche ab {name} fehlgeschlagen ({response.status_code}, "
            f"{_error_code(response)})."
        )
        return None
    return int(response.json().get("total", 0))


# --- Ausgabe / Einstieg -----------------------------------------------------------------------


def format_report(report: SeedReport, base_url: str) -> str:
    lines = [
        f"Demo-Daten angelegt unter {base_url}",
        f"  Nutzer: {report.users_registered} neu, {report.users_existing} vorhanden",
        f"  Fahrten: {report.rides_created} neu, {report.rides_skipped} übersprungen (vorhanden)",
    ]
    if report.match_probe is not None:
        lines.append(f"  Kontrollsuche ab {SAMPLE_PICKUPS[0][0]}: {report.match_probe} Treffer")
    lines += [f"  Hinweis: {note}" for note in report.notes]
    lines += ["", "Demo-Zugangsdaten (nur Entwicklung, gemeinsames Passwort):"]
    lines.append(f"  Passwort: {DEMO_PASSWORD}")
    lines.append(f"  Mitfahrer:  {DEMO_RIDER.email}")
    lines += [f"  Fahrer:     {u.email}" for u in DEMO_DRIVERS]
    lines += ["", "Beispiel-Abholpunkte (Mitfahrt suchen):"]
    lines += [f"  {name:<13} lat={lat}, lon={lon}" for name, (lat, lon) in SAMPLE_PICKUPS]
    lines.append("")
    lines.append("Die Fahrten starten morgen bzw. übermorgen (UTC) und enden am Event-Ziel.")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = REPO_ROOT / ".env",
    transport: httpx.BaseTransport | None = None,
) -> int:
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        description="Demo-Nutzer und Demo-Fahrten über die API anlegen."
    )
    parser.add_argument(
        "--base-url", help="API-Adresse (Standard: SEED_API_URL oder 127.0.0.1:API_PORT)"
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Auch Nicht-Loopback-Ziele erlauben (Entwicklungsserver)",
    )
    args = parser.parse_args(argv)
    try:
        refuse_in_production(env, env_file)
        base_url = (args.base_url or default_base_url(env, env_file)).rstrip("/")
        check_target(base_url, allow_remote=args.allow_remote)
        report = seed(base_url, transport=transport)
    except RefusedError as exc:
        print(f"Verweigert: {exc}", file=sys.stderr)
        return 2
    except SeedError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(format_report(report, base_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
