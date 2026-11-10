"""Phase 2 – GET/DELETE /api/me.

Vertrag: docs/API-CONTRACT.md. Dieses Modul gehört genau einer Phase; die Registrierung in
app/main.py ist bereits erfolgt – hier nur Endpunkte ergänzen.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/me", tags=["me"])
