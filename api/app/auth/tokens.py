"""JWT im HttpOnly-Cookie (HS256). Bewusst klein und zustandslos.

Bekannte Grenze: Ein ausgestelltes Token lässt sich serverseitig nicht widerrufen (Logout löscht
nur das Cookie); es läuft nach ``JWT_TTL_MINUTES`` ab. Wird ein Konto gelöscht, schlägt die
Benutzersuche in :mod:`app.auth.deps` fehl – das Token ist dann wertlos.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Response

from app.config import Settings, get_settings

COOKIE_NAME = "access_token"
_ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    """Token fehlt, ist kaputt, abgelaufen oder gefälscht."""


def create_access_token(user_id: uuid.UUID, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_ttl_minutes)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)


def decode_access_token(token: str, settings: Settings | None = None) -> uuid.UUID:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[_ALGORITHM],  # feste Liste: kein "alg=none"
            options={"require": ["sub", "exp"]},
        )
        return uuid.UUID(str(claims["sub"]))
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError from exc


def set_auth_cookie(response: Response, token: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_auth_cookie(response: Response, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
