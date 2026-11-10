"""Initiales Schema: users, rides, ride_requests (PostGIS)

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _geo(kind: str) -> geoalchemy2.Geography:
    return geoalchemy2.Geography(geometry_type=kind, srid=4326, spatial_index=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "rides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("driver_id", sa.Uuid(), nullable=False),
        sa.Column("start_geom", _geo("POINT"), nullable=False),
        sa.Column("end_geom", _geo("POINT"), nullable=False),
        sa.Column("route_geom", _geo("LINESTRING"), nullable=False),
        sa.Column("route_distance_m", sa.Float(), nullable=False),
        sa.Column("route_duration_s", sa.Float(), nullable=False),
        sa.Column("seats_total", sa.Integer(), nullable=False),
        sa.Column("seats_available", sa.Integer(), nullable=False),
        sa.Column("departure_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rides"),
        sa.ForeignKeyConstraint(
            ["driver_id"], ["users.id"], name="fk_rides_driver_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint("seats_total BETWEEN 1 AND 8", name="ck_rides_seats_total_range"),
        sa.CheckConstraint(
            "seats_available BETWEEN 0 AND seats_total", name="ck_rides_seats_available_range"
        ),
        sa.CheckConstraint("status IN ('open', 'full', 'cancelled')", name="ck_rides_status_valid"),
    )
    op.create_index("ix_rides_route_geom", "rides", ["route_geom"], postgresql_using="gist")
    op.create_index("ix_rides_start_geom", "rides", ["start_geom"], postgresql_using="gist")
    op.create_index("ix_rides_departure_time", "rides", ["departure_time"])
    op.create_index("ix_rides_driver_id", "rides", ["driver_id"])

    op.create_table(
        "ride_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ride_id", sa.Uuid(), nullable=False),
        sa.Column("rider_id", sa.Uuid(), nullable=False),
        sa.Column("pickup_geom", _geo("POINT"), nullable=False),
        sa.Column("detour_distance_m", sa.Float(), nullable=False),
        sa.Column("detour_duration_s", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_ride_requests"),
        sa.ForeignKeyConstraint(
            ["ride_id"], ["rides.id"], name="fk_ride_requests_ride_id_rides", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["rider_id"], ["users.id"], name="fk_ride_requests_rider_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled')",
            name="ck_ride_requests_status_valid",
        ),
    )
    op.create_index("ix_ride_requests_ride_id", "ride_requests", ["ride_id"])
    op.create_index("ix_ride_requests_rider_id", "ride_requests", ["rider_id"])
    op.create_index(
        "uq_ride_requests_active",
        "ride_requests",
        ["ride_id", "rider_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'accepted')"),
    )


def downgrade() -> None:
    op.drop_table("ride_requests")
    op.drop_table("rides")
    op.drop_table("users")
    # Die PostGIS-Extension bleibt bewusst bestehen (gehört der Datenbank, nicht diesem Schema).
