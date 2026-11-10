"""GET /api/config: Form und Kartenangaben (Vertrag docs/API-CONTRACT.md, „Konfiguration“)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings, get_settings
from app.main import app


@pytest.mark.asyncio(loop_scope="session")
async def test_config_map_contains_attribution(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/config")
    assert r.status_code == 200
    m = r.json()["map"]
    assert set(m) == {"tile_url", "attribution", "geocoder"}
    assert isinstance(m["attribution"], str) and m["attribution"]
    # Standard (OSM): Namensnennung mit Link auf die Urheberrechtsseite
    assert "OpenStreetMap" in m["attribution"]
    assert 'href="https://www.openstreetmap.org/copyright"' in m["attribution"]


@pytest.mark.asyncio(loop_scope="session")
async def test_config_attribution_follows_settings(client: httpx.AsyncClient) -> None:
    custom = Settings(
        tile_url="https://tiles.example.test/{z}/{x}/{y}.png",
        tile_attribution="Karte: Beispiel GmbH",
    )
    app.dependency_overrides[get_settings] = lambda: custom
    try:
        r = await client.get("/api/config")
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert r.status_code == 200
    assert r.json()["map"]["tile_url"] == "https://tiles.example.test/{z}/{x}/{y}.png"
    assert r.json()["map"]["attribution"] == "Karte: Beispiel GmbH"


def test_attribution_default_is_set_in_settings() -> None:
    assert "OpenStreetMap" in Settings().tile_attribution
