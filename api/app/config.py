"""Konfiguration ausschließlich aus Umgebungsvariablen (Compose reicht .env durch)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

PLACEHOLDER_PREFIX = "CHANGE_ME"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: Literal["development", "production", "test"] = "development"

    # Datenbank – aus Einzelteilen zusammengesetzt, damit Sonderzeichen im Passwort
    # nicht URL-kodiert werden müssen.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "mitfahrer"
    postgres_user: str = "mitfahrer"
    postgres_password: SecretStr = SecretStr("CHANGE_ME")

    # Routing
    osrm_url: str = "http://localhost:5000"
    osrm_timeout_s: float = Field(default=5.0, gt=0)

    # Event (TODO: Platzhalter, siehe .env.example)
    event_name: str = "40C3"
    event_lat: float = Field(default=53.5615, ge=-90, le=90)
    event_lon: float = Field(default=9.9885, ge=-180, le=180)
    event_start_date: date = date(2026, 12, 27)
    event_end_date: date = date(2026, 12, 30)
    retention_days_after_event: int = Field(default=14, ge=0)

    # Matching
    match_prefilter_radius_m: int = Field(default=20_000, gt=0)
    match_max_detour_ratio: float = Field(default=0.20, ge=0)
    match_max_detour_minutes: float = Field(default=25.0, ge=0)
    match_detour_mode: Literal["any", "all"] = "any"
    match_max_candidates: int = Field(default=30, gt=0)
    match_max_snap_distance_m: int = Field(default=500, gt=0)

    # Karte
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

    # Auth (ab Phase 2 genutzt)
    jwt_secret: SecretStr = SecretStr("CHANGE_ME")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @model_validator(mode="after")
    def _reject_placeholder_secrets_in_production(self) -> Self:
        if self.app_env == "production":
            for name in ("postgres_password", "jwt_secret"):
                value: SecretStr = getattr(self, name)
                if value.get_secret_value().startswith(PLACEHOLDER_PREFIX):
                    raise ValueError(
                        f"{name.upper()} enthält noch den Platzhalter {PLACEHOLDER_PREFIX} – "
                        "bitte in .env setzen (make env erzeugt Zufallswerte)."
                    )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
