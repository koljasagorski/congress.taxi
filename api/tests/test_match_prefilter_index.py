"""Der Vorfilter muss den GiST-Index auf ``rides.route_geom`` nutzen können (EXPLAIN)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import ClauseElement, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.base import Executable

from app.db import get_sessionmaker
from app.services.matching import candidate_statement
from tests.helpers import EVENT, NORTH, create_user, future, insert_ride
from tests.test_match_support import MID_NORTH_EVENT

pytestmark = pytest.mark.asyncio(loop_scope="session")


class Explain(Executable, ClauseElement):
    """``EXPLAIN <statement>`` für beliebige Core-Statements (offizielles SQLAlchemy-Rezept)."""

    inherit_cache = False

    def __init__(self, statement: Any) -> None:
        self.statement = statement


@compiles(Explain)
def _compile_explain(element: Explain, compiler: Any, **kw: Any) -> str:
    return "EXPLAIN " + compiler.process(element.statement, **kw)


async def test_prefilter_can_use_the_gist_index(db: Any) -> None:
    driver = await create_user(db)
    # Viele weit entfernte Fahrten (Süddeutschland), wenige nahe – damit der Planer den Index lohnt.
    for i in range(400):
        lat = 48.0 + (i % 20) * 0.05
        lon = 11.0 + (i // 20) * 0.05
        await insert_ride(
            db, driver["id"], start=(lat, lon), end=(lat + 0.1, lon + 0.1), departure_time=future(5)
        )
    near_ids: set[uuid.UUID] = set()
    for _ in range(3):
        near_ids.add(await insert_ride(db, driver["id"], start=NORTH, end=EVENT))
    await db.execute("ANALYZE rides")

    rider = await create_user(db)
    stmt = candidate_statement(
        user_id=rider["id"],
        pickup=MID_NORTH_EVENT,
        radius_m=20_000,
        time_from=future(0),
        time_to=future(100),
        min_seats=1,
        max_candidates=30,
        trim_m=1000,
    )
    async with get_sessionmaker()() as session:
        # Mit seqscan=off wählt der Planer einen Index, sofern einer verwendbar ist. Die Prüfung ist
        # also: „der GiST-Index auf route_geom ist für ST_DWithin verwendbar“.
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(row[0] for row in await session.execute(Explain(stmt)))
        assert "ix_rides_route_geom" in plan, plan
        assert "Seq Scan on rides" not in plan, plan

        # nicht vacuous: dasselbe Statement liefert genau die nahen Fahrten
        rows = (await session.execute(stmt)).all()
        assert {row[0] for row in rows} == near_ids
