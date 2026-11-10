"""Phase 2 – GET/DELETE /api/me.

Vertrag: docs/API-CONTRACT.md. Kontolöschung entfernt Nutzer, Fahrten und Anfragen über die
ON-DELETE-CASCADE-Fremdschlüssel; ein ausgestelltes Token ist danach wertlos
(``get_current_user`` findet den Nutzer nicht mehr).
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUser, SessionDep, SettingsDep
from app.auth.tokens import clear_auth_cookie
from app.models import Ride, RideRequest, User
from app.schemas.auth import MeOut

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("", response_model=MeOut)
async def get_me(user: CurrentUser) -> MeOut:
    return MeOut(
        id=user.id, display_name=user.display_name, email=user.email, created_at=user.created_at
    )


async def _lock_rides(session: AsyncSession, ride_ids: Any, rider_id: uuid.UUID) -> list[uuid.UUID]:
    """Sperrt fremde Fahrten (``FOR UPDATE``), deterministisch nach ``id`` – kein Deadlock."""
    rows = await session.scalars(
        select(Ride.id)
        .where(Ride.id.in_(ride_ids), Ride.driver_id != rider_id)
        .order_by(Ride.id)
        .with_for_update()
    )
    return list(rows)


async def release_seats_of_rider(session: AsyncSession, rider_id: uuid.UUID) -> None:
    """Löscht alle Anfragen des Mitfahrers und gibt Plätze angenommener Mitfahrten zurück.

    Phase-5-Schnittstelle: Sobald Phase 5 (Anfragen/Accept/Cancel) denselben Platz-Mechanismus
    bereitstellt, ersetzt deren Funktion diese hier (gleiche Signatur) oder wird von hier aus
    aufgerufen; ``delete_me`` bleibt dann unverändert.

    Reihenfolge (Vertrag: Fahrt vor Anfrage) und Race-Sicherheit gegen accept/cancel:

    1. Fahrten sperren, auf denen der Nutzer *irgendeine* Anfrage hat (auch ``pending`` – ein
       gleichzeitiges ``accept`` kann sie noch annehmen). Wer die Fahrtsperre hält, ändert
       Anfragen dieser Fahrt; erst nach dem Warten sehen wir den endgültigen Zustand.
    2. Erst *nach* den Sperren die Anfragen mit ``DELETE … RETURNING`` löschen und nur die
       tatsächlich gelöschten ``accepted``-Zeilen gutschreiben. Vorher gezählte Werte wären durch
       ein gleichzeitiges ``cancel``/``accept`` veraltet (Doppel- bzw. Fehlgutschrift).
    """
    candidates = select(RideRequest.ride_id).where(RideRequest.rider_id == rider_id).distinct()
    locked = set(await _lock_rides(session, candidates.scalar_subquery(), rider_id))
    deleted = (
        await session.execute(
            delete(RideRequest)
            .where(RideRequest.rider_id == rider_id)
            .returning(RideRequest.ride_id, RideRequest.status)
        )
    ).all()
    held = Counter(ride_id for ride_id, status in deleted if status == "accepted")
    # Nachzügler: Anfrage wurde nach Schritt 1 angelegt und angenommen (praktisch ausgeschlossen,
    # aber ohne Sperre würde der Platz stillschweigend verloren gehen).
    late = [ride_id for ride_id in held if ride_id not in locked]
    if late:
        locked |= set(await _lock_rides(session, late, rider_id))
    for ride_id in sorted(locked):
        n = held.get(ride_id, 0)
        if n:
            await session.execute(
                update(Ride)
                .where(Ride.id == ride_id, Ride.status != "cancelled")
                .values(
                    seats_available=func.least(Ride.seats_total, Ride.seats_available + n),
                    status=case((Ride.status == "full", "open"), else_=Ride.status),
                )
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
