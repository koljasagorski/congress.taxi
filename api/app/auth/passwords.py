"""Passwort-Hashing hinter einem kleinen Interface.

Die Endpunkte kennen nur :class:`PasswordHasher`; ein späterer Wechsel des Verfahrens (oder ein
Umstieg auf externe Anmeldung wie OIDC) bleibt damit auf dieses Modul und ``app/api/auth.py``
beschränkt. Standard ist argon2id (``argon2-cffi``, aktuelle Standardparameter der Bibliothek).
"""

from __future__ import annotations

import secrets
from typing import Protocol

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool:
        """``True`` nur bei passendem Passwort; unlesbare Hashes gelten als „passt nicht“."""
        ...

    def needs_rehash(self, password_hash: str) -> bool:
        """``True``, wenn der Hash nicht (mehr) den aktuellen Parametern entspricht."""
        ...


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._argon2 = _Argon2()

    def hash(self, password: str) -> str:
        return self._argon2.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._argon2.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._argon2.check_needs_rehash(password_hash)
        except (VerificationError, InvalidHashError):
            return True


_DUMMY_HASHES: dict[type, str] = {}


def dummy_hash(hasher: PasswordHasher) -> str:
    """Fester Hash eines zufälligen, nie ausgegebenen Passworts – mit den aktuellen Parametern.

    Dient der Zeitangleichung: Auch bei unbekannter E-Mail wird ein vollwertiges ``verify``
    ausgeführt, damit die Antwortzeit kein Konto-Orakel ist. Je Hasher-Typ einmal berechnet.
    """
    key = type(hasher)
    cached = _DUMMY_HASHES.get(key)
    if cached is None:
        cached = _DUMMY_HASHES[key] = hasher.hash(secrets.token_urlsafe(32))
    return cached
