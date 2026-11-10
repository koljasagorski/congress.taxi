"""Security-Schicht der API (Phase 7): Header, Origin-Prüfung, globales Rate-Limit.

Der Hook ``install_security`` ist in app/main.py bereits eingehängt (nach den Fehler-Handlern, vor
den Routern). Phase 7 füllt ihn; bis dahin ist er ein No-op.
"""

from __future__ import annotations

from fastapi import FastAPI


def install_security(app: FastAPI) -> None:  # pragma: no cover - Phase 7 ersetzt das
    return None
