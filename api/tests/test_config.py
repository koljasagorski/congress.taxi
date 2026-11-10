from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_database_url_handles_special_characters_in_password() -> None:
    s = Settings(postgres_password="p@ss:w/rd#1", postgres_host="db")  # type: ignore[arg-type]
    url = s.database_url
    assert url.password == "p@ss:w/rd#1"
    assert url.host == "db"
    assert url.drivername == "postgresql+asyncpg"
    # Passwort darf nicht im Klartext in der String-Darstellung auftauchen
    assert "p@ss" not in repr(s)


def test_production_rejects_placeholder_secrets() -> None:
    placeholder = "CHANGE_ME"
    with pytest.raises(ValidationError, match="POSTGRES_PASSWORD"):
        Settings(app_env="production", postgres_password=placeholder, jwt_secret="x" * 64)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(app_env="production", postgres_password="x" * 48, jwt_secret=placeholder)  # type: ignore[arg-type]


def test_production_accepts_real_secrets() -> None:
    s = Settings(
        app_env="production",
        postgres_password="a" * 48,  # type: ignore[arg-type]
        jwt_secret="b" * 64,  # type: ignore[arg-type]
    )
    assert s.app_env == "production"


def test_coordinates_are_range_checked() -> None:
    with pytest.raises(ValidationError):
        Settings(event_lat=91.0)
    with pytest.raises(ValidationError):
        Settings(event_lon=-181.0)
