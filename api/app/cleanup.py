"""Retention-Cleanup: löscht ALLE Daten (Anfragen, Fahrten, Konten) nach Ablauf der Frist.

Aufruf::

    python -m app.cleanup [--dry-run] [--now 2027-01-15T00:00:00+00:00]

**Frist (präzise).** Stichtag ist der Kalendertag ``EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT``
(in UTC). Die Frist läuft am **Ende dieses Tages** ab, also am Instant
``Stichtag + 1 Tag, 00:00:00.000000 UTC`` (``retention_expires_at``). Gelöscht wird, sobald
``jetzt >= retention_expires_at`` – bzw. gleichbedeutend ``jetzt > 23:59:59.999999`` des Stichtags.
Genau auf der Grenze (00:00:00 UTC des Folgetags) wird also bereits gelöscht; eine Mikrosekunde
davor noch nicht. Vorher ist der Lauf ein No-op (Exit-Code 0).

**Eigenschaften.** Idempotent (ein zweiter Lauf löscht 0 Zeilen), transaktional (alles oder nichts;
Schreibzugriffe auf die drei Tabellen werden während des Löschens kurz gesperrt, ``lock_timeout``
10 s), Ausgabe nur Zählwerte – keine Namen, keine E-Mail-Adressen. ``--dry-run`` zählt nur.
``--now`` (ISO 8601 **mit** Zeitzone) ersetzt die aktuelle Zeit (Tests, Nachholläufe).

**Schutz vor falschem Datum (OPS-4).** Ist die Frist abgelaufen, aber es gibt Datensätze, die erst
mehr als ``LATE_GRACE`` (1 Tag) NACH Fristablauf angelegt wurden, passt das Datum nicht zur
Wirklichkeit (Tippfehler im ``EVENT_END_DATE``, Frist nachträglich vorverlegt, Stack nach der Frist
weiterbetrieben).
Dann wird **nichts gelöscht**: Ausgabe nur der Zählwerte, Exit-Code 3. Ausdrücklich bestätigen mit
``CLEANUP_CONFIRM_EXPIRED=1`` bzw. ``--confirm-expired``. Konten, die zwischen Fristablauf und dem
nächsten Lauf (alle 6 h) entstehen, fallen in die Karenz und werden normal gelöscht. Nach der Frist
gehört der Stack gestoppt (``make down``); die API bleibt bis dahin offen.

**Exit-Codes.** 0 = erfolgreich (auch „Frist noch nicht abgelaufen“ und ``--dry-run``);
1 = Laufzeitfehler (z. B. Datenbank nicht erreichbar, ungültige Konfiguration; ausgegeben wird nur
der Exception-Typ bzw. die Konfigurationsmeldung ohne Werte); 2 = falsche Aufrufparameter;
3 = verweigert (Daten nach Fristablauf, siehe oben).

Nach einem Restore aus einem Backup den Cleanup erneut laufen lassen (``make cleanup``): Das Backup
kann Daten enthalten, deren Frist inzwischen abgelaufen ist.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings, get_settings
from app.db import dispose_engine, get_engine

#: Löschreihenfolge (Kinder zuerst); zugleich die Tabellen, die gezählt werden.
TABLES = ("ride_requests", "rides", "users")

#: Karenz: Datensätze, die höchstens so lange nach Fristablauf angelegt wurden, gelten als normal
#: (der Dienst läuft nur alle 6 h; bei Fehlern erneut nach 10 min).
LATE_GRACE = timedelta(days=1)

EXIT_BLOCKED = 3


def retention_deadline_day(settings: Settings) -> date:
    """Letzter Tag, an dem die Daten noch gespeichert bleiben dürfen."""
    return settings.event_end_date + timedelta(days=settings.retention_days_after_event)


def retention_expires_at(settings: Settings) -> datetime:
    """Instant, ab dem (einschließlich) gelöscht wird: 00:00:00 UTC des Tages nach dem Stichtag."""
    return datetime.combine(retention_deadline_day(settings) + timedelta(days=1), time(), UTC)


def is_due(settings: Settings, now: datetime) -> bool:
    if now.tzinfo is None:
        raise ValueError("now muss eine Zeitzone tragen")
    return now >= retention_expires_at(settings)


@dataclass(frozen=True)
class CleanupResult:
    due: bool
    dry_run: bool
    counts: dict[str, int]
    #: Anzahl Datensätze (alle Tabellen), die später als ``LATE_GRACE`` nach Fristablauf entstanden
    late_rows: int = 0
    #: True = Löschen verweigert, weil ``late_rows`` > 0 und nicht ausdrücklich bestätigt
    blocked: bool = False

    def summary(self, settings: Settings) -> str:
        deadline = retention_expires_at(settings).isoformat()
        if not self.due:
            return f"cleanup: Aufbewahrungsfrist läuft bis {deadline}, nichts zu löschen"
        if self.blocked:
            return (
                f"cleanup: verweigert – Frist abgelaufen seit {deadline}, aber {self.late_rows} "
                f"Datensätze wurden erst mehr als {LATE_GRACE.days} Tag danach angelegt. Das "
                "EVENT_END_DATE (oder RETENTION_DAYS_AFTER_EVENT) stimmt vermutlich nicht. "
                "Nichts gelöscht. Datum prüfen; bewusst löschen mit CLEANUP_CONFIRM_EXPIRED=1 "
                "bzw. --confirm-expired."
            )
        verb = "würde löschen" if self.dry_run else "gelöscht"
        counts = " ".join(f"{t}={self.counts[t]}" for t in TABLES)
        return f"cleanup: Frist abgelaufen seit {deadline}, {verb}: {counts}"


async def _count(conn: AsyncConnection) -> dict[str, int]:
    # Tabellennamen stammen aus der Konstante TABLES, nicht aus Eingaben.
    return {
        t: int((await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one())  # noqa: S608
        for t in TABLES
    }


async def _count_late(conn: AsyncConnection, limit: datetime) -> int:
    """Datensätze, die nach ``limit`` angelegt wurden (Summe über alle Tabellen)."""
    total = 0
    for table in TABLES:
        total += int(
            (
                await conn.execute(
                    text(f"SELECT count(*) FROM {table} WHERE created_at > :limit"),  # noqa: S608
                    {"limit": limit},
                )
            ).scalar_one()
        )
    return total


async def run_cleanup(
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    confirm_expired: bool = False,
) -> CleanupResult:
    """Führt den Cleanup aus. ``now`` muss zeitzonenbehaftet sein (Standard: aktuelle UTC-Zeit).

    ``confirm_expired`` (oder ``Settings.cleanup_confirm_expired``) hebt den Schutz vor Daten
    nach Fristablauf auf (siehe Modul-Docstring).
    """
    settings = settings or get_settings()
    confirm_expired = confirm_expired or settings.cleanup_confirm_expired
    late_limit = retention_expires_at(settings) + LATE_GRACE
    now = now or datetime.now(UTC)
    if not is_due(settings, now):
        return CleanupResult(due=False, dry_run=dry_run, counts=dict.fromkeys(TABLES, 0))

    engine = get_engine()
    async with engine.begin() as conn:
        if dry_run:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            late = await _count_late(conn, late_limit)
            if late and not confirm_expired:
                return CleanupResult(
                    due=True,
                    dry_run=True,
                    counts=dict.fromkeys(TABLES, 0),
                    late_rows=late,
                    blocked=True,
                )
            return CleanupResult(due=True, dry_run=True, counts=await _count(conn), late_rows=late)
        await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        # Kurz keine gleichzeitigen Schreibzugriffe: Zählung = tatsächlich gelöschte Zeilen.
        # Sperr-Reihenfolge wie im API-Vertrag: Fahrt (rides) VOR Anfrage (ride_requests) –
        # sonst Deadlock mit Transaktionen, die erst rides und dann ride_requests anfassen
        # (accept, cancel, DELETE /api/rides/{id}). users steht davor: DELETE /api/me sperrt erst
        # den Nutzer und löscht dann per CASCADE Fahrten und Anfragen. LOCK TABLE sperrt in der
        # genannten Reihenfolge.
        await conn.execute(
            text("LOCK TABLE users, rides, ride_requests IN SHARE ROW EXCLUSIVE MODE")
        )
        late = await _count_late(conn, late_limit)
        if late and not confirm_expired:
            return CleanupResult(
                due=True,
                dry_run=False,
                counts=dict.fromkeys(TABLES, 0),
                late_rows=late,
                blocked=True,
            )
        counts: dict[str, int] = {}
        for table in TABLES:
            result = await conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
            counts[table] = int(result.rowcount or 0)
    return CleanupResult(due=True, dry_run=False, counts=counts, late_rows=late)


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ungültiges ISO-8601-Datum") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--now braucht eine Zeitzone, z. B. 2027-01-15T00:00:00Z")
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cleanup",
        description="Löscht alle Daten nach Ablauf der Aufbewahrungsfrist (EVENT_END_DATE + "
        "RETENTION_DAYS_AFTER_EVENT, Tagesende UTC).",
    )
    parser.add_argument("--dry-run", action="store_true", help="nur zählen, nichts löschen")
    parser.add_argument(
        "--confirm-expired",
        action="store_true",
        help="auch löschen, wenn Daten NACH Fristablauf angelegt wurden (sonst Exit-Code 3); "
        "wie CLEANUP_CONFIRM_EXPIRED=1",
    )
    parser.add_argument(
        "--now", type=_aware_datetime, help="aktuelle Zeit ersetzen (ISO 8601 mit Zeitzone)"
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    try:
        settings = get_settings()
        result = await run_cleanup(
            settings=settings,
            now=args.now,
            dry_run=args.dry_run,
            confirm_expired=args.confirm_expired,
        )
    except ValidationError as exc:
        # Ungültige Konfiguration (z. B. EVENT_END_DATE vor EVENT_START_DATE): Meldungen ohne
        # Eingabewerte (die könnten Geheimnisse enthalten).
        print("cleanup: Fehler (ungültige Konfiguration)", file=sys.stderr)
        for err in exc.errors(include_input=False, include_context=False, include_url=False):
            print(f"cleanup:   {err['msg']}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Nur der Typ: Fehlertexte könnten Verbindungsdaten oder Zeileninhalte enthalten.
        print(f"cleanup: Fehler ({type(exc).__name__})", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    if result.blocked:
        print(result.summary(settings), file=sys.stderr)
        return EXIT_BLOCKED
    print(result.summary(settings))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)  # Exit-Code 2 bei falschen Parametern
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
