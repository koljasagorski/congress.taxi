"""Phase 2 – GET/DELETE /api/me.

Vertrag: docs/API-CONTRACT.md. Kontolöschung entfernt Nutzer, Fahrten und Anfragen über die
ON-DELETE-CASCADE-Fremdschlüssel. Die Plätze angenommener Mitfahrten gibt vorher – in derselben
Transaktion und unter der Fahrtsperre – ``app.services.requests.release_seats_of_rider`` zurück
(einzige Implementierung). Ein ausgestelltes Token ist danach wertlos (``get_current_user``
findet den Nutzer nicht mehr).
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import delete

from app.auth.deps import CurrentUser, SessionDep, SettingsDep
from app.auth.tokens import clear_auth_cookie
from app.models import User
from app.schemas.auth import MeOut
from app.services.requests import release_seats_of_rider

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("", response_model=MeOut)
async def get_me(user: CurrentUser) -> MeOut:
    return MeOut(
        id=user.id, display_name=user.display_name, email=user.email, created_at=user.created_at
    )


@router.delete("", status_code=204)
async def delete_me(user: CurrentUser, session: SessionDep, settings: SettingsDep) -> Response:
    await release_seats_of_rider(session, user.id)
    # Eigene Fahrten samt fremden Anfragen darauf entfernt CASCADE.
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()
    response = Response(status_code=204)
    clear_auth_cookie(response, settings)
    return response
