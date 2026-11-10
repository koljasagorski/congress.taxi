"""Datenmodell. Bewusst ohne Klartext-Adressen, Kennzeichen oder Telefonnummern."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministische Constraint-Namen (wichtig für Alembic-Migrationen)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _geo(kind: str) -> Any:
    # spatial_index=False: GiST-Indizes legen wir explizit an (Migration + __table_args__)
    return Geography(geometry_type=kind, srid=4326, spatial_index=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(320), unique=True)  # normalisiert (klein)
    password_hash: Mapped[str] = mapped_column(String(255))
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ride(Base):
    __tablename__ = "rides"
    __table_args__ = (
        CheckConstraint("seats_total BETWEEN 1 AND 8", name="seats_total_range"),
        CheckConstraint("seats_available BETWEEN 0 AND seats_total", name="seats_available_range"),
        CheckConstraint("status IN ('open', 'full', 'cancelled')", name="status_valid"),
        Index("ix_rides_route_geom", "route_geom", postgresql_using="gist"),
        Index("ix_rides_start_geom", "start_geom", postgresql_using="gist"),
        Index("ix_rides_departure_time", "departure_time"),
        Index("ix_rides_driver_id", "driver_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    driver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    start_geom: Mapped[Any] = mapped_column(_geo("POINT"))
    end_geom: Mapped[Any] = mapped_column(_geo("POINT"))  # Eventziel zum Zeitpunkt der Anlage
    route_geom: Mapped[Any] = mapped_column(_geo("LINESTRING"))
    route_distance_m: Mapped[float] = mapped_column(Float)
    route_duration_s: Mapped[float] = mapped_column(Float)
    seats_total: Mapped[int] = mapped_column(Integer)
    seats_available: Mapped[int] = mapped_column(Integer)
    departure_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RideRequest(Base):
    __tablename__ = "ride_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled')", name="status_valid"
        ),
        # Pro (Fahrt, Mitfahrer) höchstens eine aktive Anfrage
        Index(
            "uq_ride_requests_active",
            "ride_id",
            "rider_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'accepted')"),
        ),
        Index("ix_ride_requests_rider_id", "rider_id"),
        Index("ix_ride_requests_ride_id", "ride_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    ride_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rides.id", ondelete="CASCADE"))
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    pickup_geom: Mapped[Any] = mapped_column(_geo("POINT"))
    detour_distance_m: Mapped[float] = mapped_column(Float)
    detour_duration_s: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
