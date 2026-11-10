"""Backups überleben die Löschfrist nicht: ``backup.sh --purge-expired`` und Sperre danach.

Braucht weder Docker noch ``age``: Es werden nur Dateien im Backup-Verzeichnis geprüft.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import API_DIR

REPO = API_DIR.parent
BACKUP_SH = REPO / "scripts" / "backup.sh"
RECIPIENT = "age1" + "q" * 58


def run(backup_dir: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    full_env = {
        "PATH": os.environ["PATH"],
        "HOME": str(backup_dir),
        "ENV_FILE": str(backup_dir / "no.env"),
        "BACKUP_DIR": str(backup_dir / "b"),
        "BACKUP_AGE_RECIPIENT": RECIPIENT,
        # würde der Dump-Befehl je laufen, schlüge der Test laut fehl
        "BACKUP_DB_EXEC": "false",
        **env,
    }
    return subprocess.run(
        ["bash", str(BACKUP_SH), *args],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=REPO,
    )


def seed(tmp_path: Path) -> list[Path]:
    d = tmp_path / "b"
    d.mkdir(mode=0o700)
    files = [
        d / "mitfahrer-20270113T031500Z.dump.age",
        d / "mitfahrer-20270114T031500Z.dump.age.tmp",
        d / "mitfahrer-20270101T031500Z.dump.age",
    ]
    for f in files:
        f.write_bytes(b"geheim")
    return files


def test_purge_removes_all_backups_after_the_deadline(tmp_path: Path) -> None:
    files = seed(tmp_path)
    foreign = tmp_path / "b" / "notizen.txt"
    foreign.write_text("nicht anfassen")
    r = run(
        tmp_path, "--purge-expired",
        EVENT_END_DATE="2020-01-01", RETENTION_DAYS_AFTER_EVENT="14",
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert not any(f.exists() for f in files)
    assert foreign.exists()
    assert "3" in r.stdout


def test_purge_keeps_backups_until_the_deadline(tmp_path: Path) -> None:
    files = seed(tmp_path)
    r = run(
        tmp_path, "--purge-expired",
        EVENT_END_DATE="2099-01-01", RETENTION_DAYS_AFTER_EVENT="14",
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert all(f.exists() for f in files)


@pytest.mark.parametrize(
    ("now_epoch", "gone"),
    [
        (1800057599, False),  # 2027-01-15T23:59:59Z: Stichtag (2027-01-01 + 14) noch nicht vorbei
        (1800057600, True),  # 2027-01-16T00:00:00Z: genau auf der Grenze (wie app.cleanup)
        (1800057601, True),
    ],
)
def test_deadline_boundary_matches_app_cleanup(tmp_path: Path, now_epoch: int, gone: bool) -> None:
    files = seed(tmp_path)
    r = run(
        tmp_path, "--purge-expired",
        EVENT_END_DATE="2027-01-01", RETENTION_DAYS_AFTER_EVENT="14",
        BACKUP_NOW_EPOCH=str(now_epoch),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (not any(f.exists() for f in files)) is gone


def test_purge_needs_no_recipient_and_ignores_keep_days_check(tmp_path: Path) -> None:
    files = seed(tmp_path)
    r = run(
        tmp_path, "--purge-expired",
        EVENT_END_DATE="2020-01-01", RETENTION_DAYS_AFTER_EVENT="14",
        BACKUP_AGE_RECIPIENT="", BACKUP_KEEP_DAYS="99",
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert not any(f.exists() for f in files)


def test_purge_without_event_date_is_a_noop_with_a_clear_message(tmp_path: Path) -> None:
    files = seed(tmp_path)
    r = run(tmp_path, "--purge-expired", RETENTION_DAYS_AFTER_EVENT="14")
    assert r.returncode == 0, r.stderr
    assert all(f.exists() for f in files)
    assert "EVENT_END_DATE" in r.stdout + r.stderr


def test_purge_rejects_garbage_dates(tmp_path: Path) -> None:
    seed(tmp_path)
    r = run(
        tmp_path, "--purge-expired",
        EVENT_END_DATE="morgen", RETENTION_DAYS_AFTER_EVENT="14",
    )  # fmt: skip
    assert r.returncode == 1
    assert "EVENT_END_DATE" in r.stderr


def test_backup_after_the_deadline_purges_and_creates_nothing(tmp_path: Path) -> None:
    files = seed(tmp_path)
    r = run(
        tmp_path,
        EVENT_END_DATE="2020-01-01", RETENTION_DAYS_AFTER_EVENT="14",
    )  # fmt: skip
    assert r.returncode == 0, r.stderr  # kein Dump versucht (BACKUP_DB_EXEC=false würde scheitern)
    assert not any(f.exists() for f in files)
    assert list((tmp_path / "b").iterdir()) == []
    assert "frist" in r.stdout


def test_unknown_option_is_rejected(tmp_path: Path) -> None:
    r = run(tmp_path, "--gibt-es-nicht")
    assert r.returncode == 2
