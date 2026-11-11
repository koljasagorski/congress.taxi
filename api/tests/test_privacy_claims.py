"""Datenschutzaussagen in Doku und Oberfläche müssen zur gemessenen Genauigkeit passen (DS-3).

Prüfer-Messung (OSRM Hamburg-Extrakt): Mit 8 bis 15 ``/api/match``-Abfragen eines nicht
verifizierten Kontos (unter dem Rate-Limit) lässt sich der Fahrer-Start aus den gerundeten
``detour_km``-Werten eingrenzen: Median ≈ 200 m, Bestfall ≈ 20 bis 50 m, Einzelfälle bis ≈ 900 m;
„wenige km“ kam nicht vor. Die frühere Aussage „einige hundert Meter bis wenige km“ überschätzte
den Schutz, und die Oberfläche versprach, die Wohnadresse bleibe geschützt.
"""

from __future__ import annotations

import re

from tests.conftest import API_DIR

REPO = API_DIR.parent
DOCS = {
    "PLAN.md": REPO / "PLAN.md",
    "API-CONTRACT.md": REPO / "docs" / "API-CONTRACT.md",
    "matching.py": API_DIR / "app" / "services" / "matching.py",
}


def test_docs_do_not_overstate_the_probing_protection() -> None:
    for name, path in DOCS.items():
        text = path.read_text()
        assert "bis wenige km" not in text, f"{name}: überholte Spanne „bis wenige km“"
        assert re.search(r"Median\D{0,20}200 m", text), f"{name}: gemessene Genauigkeit fehlt"
        assert "900 m" in text, f"{name}: Maximalfehler fehlt"


def test_ui_does_not_promise_that_the_home_address_stays_protected() -> None:
    de = (REPO / "web" / "src" / "i18n" / "de.ts").read_text()
    line = next(ln for ln in de.splitlines() if '"offer.startPrivacy"' in ln)
    assert "geschützt bleibt" not in line  # Zusage ist gegen gezieltes Abtasten falsch
    assert "Abtasten" in line  # nennt die Grenze
    assert "Haustür" in line  # und gibt den praktischen Rat
