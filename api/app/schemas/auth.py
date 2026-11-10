"""Schemas für Registrierung, Anmeldung und Konto (Vertrag: Abschnitt Authentifizierung)."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, Field, StrictBool

from app.config import get_settings

EMAIL_MAX = 320
PASSWORD_MIN = 10
PASSWORD_MAX = 128
DISPLAY_NAME_MAX = 80

# Bidi-Steuerzeichen (Umkehr der Leserichtung) wären im Anzeigenamen eine Spoofing-Gefahr.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")
# Cc = Steuerzeichen, Cs/Co/Cn = Surrogate, privat, unzugewiesen; Zl/Zp = Zeilen-/Absatztrenner.
# Cf (z. B. Zero-Width-Joiner) bleibt erlaubt, weil Emoji-Sequenzen es brauchen.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Cn", "Zl", "Zp"})


# Reservierte Namen (RFC 6761 u. a.), die email-validator in Version 2.3 immer ablehnt und nicht
# freigeben lässt. Außerhalb von Produktion brauchen wir sie trotzdem (Demo-Daten: @demo.invalid).
_SPECIAL_USE_SUFFIXES = ("invalid", "local", "localhost", "test", "arpa", "onion")
_ASCII_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def allow_special_use_domains() -> bool:
    """Strikt nur in Produktion; sonst sind reservierte Domains (``.invalid`` …) erlaubt."""
    return get_settings().app_env != "production"


def _validate_address(candidate: str, *, special_use_ok: bool) -> str:
    """Normalisierte Adresse oder ``EmailNotValidError``. Keine DNS-Abfrage."""
    try:
        return validate_email(candidate, check_deliverability=False).normalized
    except EmailNotValidError:
        local, sep, domain = candidate.rpartition("@")
        domain = domain.lower()
        if not (
            special_use_ok
            and sep
            and domain.endswith(tuple(f".{s}" for s in _SPECIAL_USE_SUFFIXES))
        ):
            raise
        if not _ASCII_DOMAIN.fullmatch(domain):
            raise
        # Local-Part wie üblich prüfen/normalisieren, die Domain bleibt wie angegeben.
        probe = validate_email(f"{local}@example.org", check_deliverability=False)
        return f"{probe.local_part}@{domain}"


def normalize_email(value: str) -> str:
    """Getrimmt und klein geschrieben (so wird die E-Mail gespeichert und gesucht)."""
    return value.strip().lower()


def canonical_email(value: str) -> str:
    """Kanonische Form für die Suche (Login): dieselbe Normalisierung wie bei der Registrierung.

    ``email-validator`` schreibt Adressen um (NFC im Local-Part, IDNA/NFKC in der Domain); das
    Konto liegt in dieser Form in der DB. Ungültiges wirft hier **nicht**, sondern fällt auf
    getrimmt + klein zurück, damit der Login für Unsinn bei 401 bleibt statt 422 zu liefern.
    """
    candidate = normalize_email(value)
    try:
        return normalize_email(
            _validate_address(candidate, special_use_ok=allow_special_use_domains())
        )
    except EmailNotValidError:
        return candidate


def _check_email(value: str) -> str:
    candidate = normalize_email(value)
    if len(candidate) > EMAIL_MAX:
        raise ValueError("E-Mail-Adresse ist zu lang")
    try:
        # Keine Zustellbarkeitsprüfung (kein DNS-Zugriff); nur die Syntax. Reservierte Domains
        # (@demo.invalid für Seed-Daten) nur außerhalb von APP_ENV=production.
        result = _validate_address(candidate, special_use_ok=allow_special_use_domains())
    except EmailNotValidError:
        # Absichtlich eigener Text: Meldungen der Bibliothek können Zeichen der Eingabe enthalten.
        raise ValueError("Ungültige E-Mail-Adresse") from None
    normalized = normalize_email(result)
    if len(normalized) > EMAIL_MAX:
        raise ValueError("E-Mail-Adresse ist zu lang")
    return normalized


def _check_password(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Passwort enthält ungültige Zeichen")
    return value


def _check_display_name(value: str) -> str:
    name = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(name) <= DISPLAY_NAME_MAX:
        raise ValueError(f"Anzeigename muss 1 bis {DISPLAY_NAME_MAX} Zeichen lang sein")
    if any(unicodedata.category(c) in _FORBIDDEN_CATEGORIES or c in _BIDI_CONTROLS for c in name):
        raise ValueError("Anzeigename enthält unzulässige Zeichen")
    return name


def _check_consent(value: bool) -> bool:
    if value is not True:
        raise ValueError("Die Einwilligung ist erforderlich")
    return value


class RegisterIn(BaseModel):
    # E-Mail: Länge wird im Validator geprüft (max_length wäre vor dem Trimmen wirksam).
    email: Annotated[
        str, Field(min_length=1, max_length=EMAIL_MAX + 64), AfterValidator(_check_email)
    ]
    password: Annotated[
        str,
        Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX),
        AfterValidator(_check_password),
    ]
    display_name: Annotated[
        str, Field(max_length=DISPLAY_NAME_MAX * 4), AfterValidator(_check_display_name)
    ]
    consent: Annotated[StrictBool, AfterValidator(_check_consent)]


class LoginIn(BaseModel):
    # Bewusst keine Format-/Stärkeprüfung: Fehlschläge sollen immer gleich als 401 enden.
    email: Annotated[str, Field(min_length=1, max_length=EMAIL_MAX + 64)]
    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX)]


class UserBrief(BaseModel):
    id: uuid.UUID
    display_name: str


class LoginOut(BaseModel):
    user: UserBrief


class MeOut(BaseModel):
    id: uuid.UUID
    display_name: str
    email: str
    created_at: datetime
