"""Phase 2 – register, login, logout.

Vertrag: docs/API-CONTRACT.md. Datenschutz: keine E-Mail, kein Passwort, kein Token in Logs oder
Fehlermeldungen; Registrierung und Anmeldung verraten nicht, ob ein Konto existiert.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth.deps import SessionDep, SettingsDep
from app.auth.passwords import Argon2PasswordHasher, PasswordHasher, dummy_hash
from app.auth.tokens import clear_auth_cookie, create_access_token, set_auth_cookie
from app.errors import ApiError
from app.models import User
from app.ratelimit import check_limit, rate_limit
from app.schemas.auth import LoginIn, LoginOut, RegisterIn, UserBrief, canonical_email
from app.schemas.common import Message

router = APIRouter(prefix="/api/auth", tags=["auth"])

_hasher: PasswordHasher = Argon2PasswordHasher()

REGISTER_MESSAGE = (
    "Anfrage entgegengenommen. Du kannst dich jetzt mit E-Mail-Adresse und Passwort anmelden."
)


def get_password_hasher() -> PasswordHasher:
    """Dependency (in Tests überschreibbar); hier wechselt man das Hash-Verfahren."""
    return _hasher


HasherDep = Annotated[PasswordHasher, Depends(get_password_hasher)]


@router.post(
    "/register",
    status_code=202,
    response_model=Message,
    dependencies=[Depends(rate_limit("register", "rate_limit_register_per_hour", 3600))],
)
async def register(body: RegisterIn, session: SessionDep, hasher: HasherDep) -> Message:
    # Das Passwort wird immer gehasht – auch wenn die E-Mail schon existiert –, damit Antwortzeit
    # und Antwort kein Orakel für vorhandene Konten sind. Hashing blockiert, daher im Thread.
    password_hash = await asyncio.to_thread(hasher.hash, body.password)
    # ON CONFLICT DO NOTHING fängt auch zwei gleichzeitige Registrierungen derselben E-Mail ab.
    await session.execute(
        pg_insert(User)
        .values(
            display_name=body.display_name,
            email=body.email,
            password_hash=password_hash,
            consent_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    await session.commit()
    return Message(message=REGISTER_MESSAGE)


def _invalid_credentials() -> ApiError:
    return ApiError(401, "invalid_credentials", "E-Mail-Adresse oder Passwort ist falsch")


@router.post(
    "/login",
    response_model=LoginOut,
    dependencies=[Depends(rate_limit("login", "rate_limit_login_per_min", 60))],
)
async def login(
    body: LoginIn,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    hasher: HasherDep,
) -> LoginOut:
    email = canonical_email(body.email)
    # Zusätzlich zum IP-Limit: je normalisierter E-Mail (verteiltes Raten auf ein Konto). Gezählt
    # wird jeder Versuch, egal ob das Konto existiert (kein Orakel); gespeichert nur als Salt-Hash.
    if settings.rate_limit_enabled:
        check_limit("login_email", email, settings.rate_limit_login_per_email_per_min, 60)
    # NUL-Bytes lehnt PostgreSQL ab (sonst 500): wie eine unbekannte E-Mail behandeln.
    user = (
        None if "\x00" in email else await session.scalar(select(User).where(User.email == email))
    )
    # Bei unbekannter E-Mail gegen einen festen Dummy-Hash prüfen (gleiche Rechenzeit).
    stored_hash = user.password_hash if user is not None else dummy_hash(hasher)
    verified = await asyncio.to_thread(hasher.verify, stored_hash, body.password)
    if user is None or not verified:
        raise _invalid_credentials()

    if await asyncio.to_thread(hasher.needs_rehash, user.password_hash):
        new_hash = await asyncio.to_thread(hasher.hash, body.password)
        await session.execute(update(User).where(User.id == user.id).values(password_hash=new_hash))
        await session.commit()

    set_auth_cookie(response, create_access_token(user.id, settings), settings)
    return LoginOut(user=UserBrief(id=user.id, display_name=user.display_name))


@router.post("/logout", status_code=204)
async def logout(settings: SettingsDep) -> Response:
    response = Response(status_code=204)
    clear_auth_cookie(response, settings)
    return response
