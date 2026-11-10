"""Swagger UI/OpenAPI nur außerhalb von production (die UI lädt Skripte von cdn.jsdelivr.net)."""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DOC_PATHS = ("/api/docs", "/api/openapi.json", "/api/redoc", "/docs", "/openapi.json", "/redoc")


def settings(env: str) -> Settings:
    return Settings(
        app_env=env,  # type: ignore[arg-type]
        jwt_secret="x" * 40,  # type: ignore[arg-type]
        postgres_password="y" * 20,  # type: ignore[arg-type]
    )


async def get(env: str, path: str) -> httpx.Response:
    app = create_app(settings(env))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("path", DOC_PATHS)
async def test_production_has_no_docs_or_openapi(path: str) -> None:
    r = await get("production", path)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"  # Standard-Fehlerformat, nicht FastAPI-Default
    assert "swagger" not in r.text.lower() and "jsdelivr" not in r.text.lower()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("env", ["development", "test"])
async def test_non_production_serves_docs_and_openapi(env: str) -> None:
    docs = await get(env, "/api/docs")
    assert docs.status_code == 200 and "swagger" in docs.text.lower()
    spec = await get(env, "/api/openapi.json")
    assert spec.status_code == 200 and spec.json()["info"]["title"].startswith("Mitfahrer")
    assert (await get(env, "/api/redoc")).status_code == 404  # ReDoc ist nie aktiv


@pytest.mark.asyncio(loop_scope="session")
async def test_production_still_serves_the_api() -> None:
    r = await get("production", "/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_invalid_production_config_disables_docs_instead_of_crashing_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main
    from app.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "CHANGE_ME")  # Platzhalter: Settings() schlägt fehl
    get_settings.cache_clear()
    try:
        assert main._docs_enabled(None) is False
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
