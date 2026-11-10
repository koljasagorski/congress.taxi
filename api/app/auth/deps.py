"""FastAPI-Dependencies für angemeldete Nutzer; geschützte Endpunkte nutzen ``CurrentUser``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import COOKIE_NAME, InvalidTokenError, decode_access_token
from app.config import Settings, get_settings
from app.db import get_session
from app.errors import ApiError
from app.models import User

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthorized() -> ApiError:
    return ApiError(401, "unauthorized", "Anmeldung erforderlich")


async def get_current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise _unauthorized()
    try:
        user_id = decode_access_token(token, settings)
    except InvalidTokenError:
        raise _unauthorized() from None
    user = await session.get(User, user_id)
    if user is None:  # Konto gelöscht → Token wertlos
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
