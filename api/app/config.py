"""Konfiguration ausschließlich aus Umgebungsvariablen (Compose reicht .env durch)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
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
    #: Datenschutz: In Trefferlisten wird der Anfang der Fahrerroute abgeschnitten, damit die
    #: genaue Startposition (oft die Wohnung) nicht an Fremde geht. 0 = nicht kürzen.
    match_route_trim_start_m: int = Field(default=1000, ge=0)

    # Karte
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    geocoder: Literal["none", "photon", "nominatim"] = "none"

    # Auth
    jwt_secret: SecretStr = SecretStr("CHANGE_ME")
    jwt_ttl_minutes: int = Field(default=720, gt=0)
    cookie_secure: bool = True
    #: Komma-getrennte IPs/CIDRs von Reverse Proxies, deren X-Forwarded-For geglaubt wird.
    trusted_proxies: str = ""
    #: Öffentlicher Hostname; daraus wird (ohne ALLOWED_ORIGINS) die erlaubte Origin abgeleitet.
    domain: str = "localhost"
    #: Komma-getrennte erlaubte Origins für Schreibzugriffe (CSRF-Schicht, zusätzlich zu SameSite).
    allowed_origins: str = ""

    # Rate-Limits (Phase 2 nutzt login/register, Phase 7 den Rest; in-memory, 1 Worker)
    rate_limit_enabled: bool = True
    rate_limit_login_per_min: int = Field(default=10, gt=0)
    rate_limit_register_per_hour: int = Field(default=10, gt=0)
    rate_limit_api_per_min: int = Field(default=120, gt=0)
    rate_limit_match_per_min: int = Field(default=20, gt=0)
    #: zusätzlich zum IP-Limit (Phase 7): je normalisierter E-Mail beim Login, je Nutzer sonst
    rate_limit_login_per_email_per_min: int = Field(default=5, gt=0)
    rate_limit_match_user_per_min: int = Field(default=20, gt=0)
    rate_limit_rides_create_per_hour: int = Field(default=20, gt=0)
    rate_limit_requests_create_per_hour: int = Field(default=30, gt=0)

    # Backup (Phase 7)
    backup_age_recipient: str = ""
    backup_keep_days: int = Field(default=7, ge=1)

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

    @property
    def trusted_proxy_networks(self) -> list[IPv4Network | IPv6Network]:
        return [
            ip_network(item.strip(), strict=False)
            for item in self.trusted_proxies.split(",")
            if item.strip()
        ]

    @property
    def allowed_origin_list(self) -> list[str]:
        """Erlaubte Origins für Schreibzugriffe. Ohne ALLOWED_ORIGINS aus DOMAIN abgeleitet."""
        explicit = [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]
        if explicit:
            return explicit
        scheme = "https" if self.cookie_secure else "http"
        return [f"{scheme}://{self.domain}"]

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
