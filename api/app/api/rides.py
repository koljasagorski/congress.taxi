"""Phase 3 – Fahrten anlegen/ändern/löschen.

Vertrag: docs/API-CONTRACT.md. Dieses Modul gehört genau einer Phase; die Registrierung in
app/main.py ist bereits erfolgt – hier nur Endpunkte ergänzen.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/rides", tags=["rides"])
