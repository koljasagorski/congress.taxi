"""Backup/Restore: kompletter Roundtrip gegen einen Wegwerf-PostGIS-Container.

Es wird NIE die Compose-Datenbank angefasst: Ein eigener Container mit zufälligem Namen und
zufälligem Host-Port wird gestartet und am Ende entfernt (``BACKUP_DB_EXEC`` zeigt darauf).
Voraussetzungen: ``docker`` und ``age``/``age-keygen``. Fehlen sie, werden die Tests übersprungen –
außer ``REQUIRE_OPS_TESTS=1`` (CI-Job ``ops``) ist gesetzt, dann schlagen sie fehl.
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import API_DIR

REPO = API_DIR.parent
BACKUP_SH = REPO / "scripts" / "backup.sh"
RESTORE_SH = REPO / "scripts" / "restore.sh"
IMAGE = os.environ.get("POSTGIS_IMAGE", "postgis/postgis:16-3.5")
DB_USER = "mitfahrer"
PROD_DB = "mitfahrer"


def _require(ok: bool, reason: str) -> None:
    if ok:
        return
    if os.environ.get("REQUIRE_OPS_TESTS"):
        pytest.fail(f"Voraussetzung fehlt: {reason}")
    pytest.skip(reason)


def sh(
    *cmd: str, env: dict[str, str] | None = None, stdin: str | None = None, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=REPO,
    )


class Pg:
    def __init__(self, name: str, password: str, port: int) -> None:
        self.name = name
        self.password = password
        self.port = port

    @property
    def exec_prefix(self) -> str:
        return f"docker exec -i {self.name}"

    def psql(self, sql: str, db: str = PROD_DB) -> str:
        r = sh(
            "docker", "exec", "-i", self.name, "psql", "-U", DB_USER, "-d", db,
            "-v", "ON_ERROR_STOP=1", "-tA", stdin=sql,
        )  # fmt: skip
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def fingerprint(self, db: str) -> str:
        return self.psql(
            "SELECT 'u:' || id || ':' || display_name || ':' || email FROM users ORDER BY id;"
            "SELECT 'r:' || id || ':' || seats_total || ':' || ST_AsText(route_geom::geometry) "
            "FROM rides ORDER BY id;"
            "SELECT 'q:' || id || ':' || status || ':' || ST_AsText(pickup_geom::geometry) "
            "FROM ride_requests ORDER BY id;",
            db,
        )

    def databases(self) -> list[str]:
        out = self.psql("SELECT datname FROM pg_database WHERE NOT datistemplate;", "postgres")
        return out.splitlines()


SEED_SQL = """
INSERT INTO users (id, display_name, email, password_hash, consent_at) VALUES
 ('00000000-0000-0000-0000-000000000001', 'Fahrerin Äöü', 'fahrerin@example.org', 'hash1', now()),
 ('00000000-0000-0000-0000-000000000002', 'Mitfahrer', 'mitfahrer@example.org', 'hash2', now()),
 ('00000000-0000-0000-0000-000000000003', 'Dritte', 'dritte@example.org', 'hash3', now());
INSERT INTO rides (id, driver_id, start_geom, end_geom, route_geom, route_distance_m,
   route_duration_s, seats_total, seats_available, departure_time, status) VALUES
 ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-000000000001',
  ST_GeogFromText('POINT(9.95 53.75)'), ST_GeogFromText('POINT(9.9885 53.5615)'),
  ST_GeogFromText('LINESTRING(9.95 53.75, 9.97 53.65, 9.9885 53.5615)'), 21000, 1500,
  3, 3, now() + interval '1 day', 'open');
INSERT INTO ride_requests (id, ride_id, rider_id, pickup_geom, detour_distance_m,
   detour_duration_s, status) VALUES
 ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1',
  '00000000-0000-0000-0000-000000000002', ST_GeogFromText('POINT(9.96 53.7)'), 1200, 150,
  'pending');
"""


@pytest.fixture(scope="module")
def pg() -> Iterator[Pg]:
    _require(shutil.which("docker") is not None, "docker fehlt")
    _require(
        sh("docker", "info", "--format", "{{.ServerVersion}}").returncode == 0,
        "Docker-Daemon nicht erreichbar",
    )
    name = f"p7-backup-test-{secrets.token_hex(4)}"
    password = secrets.token_hex(16)
    run = sh(
        "docker", "run", "-d", "--rm", "--name", name,
        "-e", f"POSTGRES_PASSWORD={password}", "-e", f"POSTGRES_USER={DB_USER}",
        "-e", f"POSTGRES_DB={PROD_DB}", "-p", "127.0.0.1::5432", IMAGE,
        timeout=600,
    )  # fmt: skip
    if run.returncode != 0:
        _require(False, f"Wegwerf-Container nicht startbar ({IMAGE})")
    try:
        deadline = time.monotonic() + 120
        while True:
            ready = sh("docker", "exec", name, "psql", "-h", "127.0.0.1", "-U", DB_USER,
                       "-d", PROD_DB, "-tAc", "select 1")  # fmt: skip
            if ready.returncode == 0:
                break
            assert time.monotonic() < deadline, "Postgres wurde nicht bereit"
            time.sleep(1)
        mapped = sh("docker", "port", name, "5432/tcp").stdout.split(":")[-1].strip()
        env = {
            **os.environ,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": mapped,
            "POSTGRES_DB": PROD_DB,
            "POSTGRES_USER": DB_USER,
            "POSTGRES_PASSWORD": password,
        }
        migrate = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_DIR, env=env, capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert migrate.returncode == 0, migrate.stderr
        pg = Pg(name, password, int(mapped))
        pg.psql(SEED_SQL)
        yield pg
    finally:
        # -v: Das Postgres-Image legt ein anonymes Datenvolume an (~190 MB); ohne -v bleibt es nach
        # jedem Testlauf liegen und füllt auf Dauer die Docker-Platte.
        sh("docker", "rm", "-f", "-v", name)


@pytest.fixture(scope="module")
def age_keys(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    _require(
        shutil.which("age") is not None and shutil.which("age-keygen") is not None, "age fehlt"
    )
    key = tmp_path_factory.mktemp("agekey") / "key.txt"
    r = sh("age-keygen", "-o", str(key))
    assert r.returncode == 0, r.stderr
    recipient = next(
        line.split(":", 1)[1].strip()
        for line in key.read_text().splitlines()
        if line.startswith("# public key:")
    )
    return recipient, key


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"


def env_for(pg: Pg, backup_dir: Path, recipient: str | None, **extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BACKUP_", "RETENTION_", "POSTGRES_", "EVENT_END_DATE"))
    }
    env.update(
        # Fern in der Zukunft: Nach Fristablauf legt backup.sh keine Backups mehr an (siehe
        # test_security_backup_purge.py); die Tests hier dürfen nicht von der echten .env abhängen.
        EVENT_END_DATE="2099-12-31",
        BACKUP_DB_EXEC=pg.exec_prefix,
        BACKUP_DIR=str(backup_dir),
        ENV_FILE=str(backup_dir.parent / "gibt-es-nicht.env"),  # echte .env nie lesen
        BACKUP_KEEP_DAYS="7",
        RETENTION_DAYS_AFTER_EVENT="14",
    )
    if recipient is not None:
        env["BACKUP_AGE_RECIPIENT"] = recipient
    env.update(extra)
    return env


def run_backup(pg: Pg, backup_dir: Path, recipient: str | None, **extra: str):  # type: ignore[no-untyped-def]
    return sh("bash", str(BACKUP_SH), env=env_for(pg, backup_dir, recipient, **extra))


def run_restore(pg: Pg, key: Path, file: Path, target: str, *flags: str):  # type: ignore[no-untyped-def]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BACKUP_", "POSTGRES_"))}
    env["BACKUP_DB_EXEC"] = pg.exec_prefix
    return sh(
        "bash", str(RESTORE_SH), "--identity", str(key), "--file", str(file),
        "--target-db", target, *flags, env=env,
    )  # fmt: skip


def backups_in(d: Path) -> list[Path]:
    return sorted(d.glob("mitfahrer-*.dump.age"))


def test_scripts_have_valid_syntax() -> None:
    for script in (BACKUP_SH, RESTORE_SH):
        assert sh("bash", "-n", str(script)).returncode == 0, script


# --- Roundtrip --------------------------------------------------------------------------------


def test_roundtrip_backup_decrypt_restore_compare(
    pg: Pg, age_keys: tuple[str, Path], backup_dir: Path
) -> None:
    recipient, key = age_keys
    before = pg.fingerprint(PROD_DB)
    assert "fahrerin@example.org" in before and "LINESTRING" in before

    result = run_backup(pg, backup_dir, recipient)
    assert result.returncode == 0, result.stderr
    files = backups_in(backup_dir)
    assert len(files) == 1
    f = files[0]
    assert f.name.endswith("Z.dump.age") and f.name.startswith("mitfahrer-20")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert not list(backup_dir.glob("*.tmp"))
    # nichts im Klartext auf der Platte
    raw = f.read_bytes()
    assert raw.startswith(b"age-encryption.org/")
    assert b"PGDMP" not in raw[:64] and b"fahrerin@example.org" not in raw
    # keine Geheimnisse in der Ausgabe
    assert pg.password not in result.stdout + result.stderr

    # Entschlüsseln mit dem Wegwerf-Schlüssel -> pg_dump-Archiv
    dec = subprocess.run(["age", "-d", "-i", str(key), str(f)], capture_output=True, check=False)
    assert dec.returncode == 0 and dec.stdout.startswith(b"PGDMP")

    target = f"restore_{secrets.token_hex(3)}"
    restored = run_restore(pg, key, f, target)
    assert restored.returncode == 0, restored.stderr
    assert "make cleanup" in restored.stdout  # Hinweis auf erneuten Cleanup
    assert target in pg.databases()
    assert pg.fingerprint(target) == before
    assert pg.psql("SELECT postgis_version() IS NOT NULL;", target) == "t"
    # Die Produktiv-Datenbank blieb unberührt
    assert pg.fingerprint(PROD_DB) == before


# --- Restore-Schutz -----------------------------------------------------------------------------


@pytest.fixture
def one_backup(pg: Pg, age_keys: tuple[str, Path], backup_dir: Path) -> Path:
    recipient, _ = age_keys
    assert run_backup(pg, backup_dir, recipient).returncode == 0
    return backups_in(backup_dir)[0]


def test_restore_refuses_production_database_without_confirmation(
    pg: Pg, age_keys: tuple[str, Path], one_backup: Path
) -> None:
    _, key = age_keys
    pg.psql("DELETE FROM users WHERE email = 'dritte@example.org';")
    changed = pg.fingerprint(PROD_DB)
    try:
        r = run_restore(pg, key, one_backup, PROD_DB)
        assert r.returncode != 0
        assert "--yes-overwrite-production" in r.stderr
        assert pg.fingerprint(PROD_DB) == changed  # nichts passiert

        r = run_restore(pg, key, one_backup, PROD_DB, "--replace")  # --replace genügt nicht
        assert r.returncode != 0
        assert pg.fingerprint(PROD_DB) == changed

        r = run_restore(pg, key, one_backup, PROD_DB, "--yes-overwrite-production")
        assert r.returncode == 0, r.stderr
        assert "dritte@example.org" in pg.fingerprint(PROD_DB)  # wieder da
    finally:
        # Zustand für die übrigen Tests sicherstellen
        if "dritte@example.org" not in pg.fingerprint(PROD_DB):
            pg.psql(
                "INSERT INTO users (id, display_name, email, password_hash, consent_at) VALUES "
                "('00000000-0000-0000-0000-000000000003', 'Dritte', 'dritte@example.org', "
                "'hash3', now());"
            )


def test_restore_into_existing_database_needs_replace(
    pg: Pg, age_keys: tuple[str, Path], one_backup: Path
) -> None:
    _, key = age_keys
    target = f"restore_{secrets.token_hex(3)}"
    assert run_restore(pg, key, one_backup, target).returncode == 0
    pg.psql(
        "INSERT INTO users (id, display_name, email, password_hash, consent_at) VALUES "
        "(gen_random_uuid(), 'Zusatz', 'zusatz@example.org', 'h', now());",
        target,
    )
    refused = run_restore(pg, key, one_backup, target)
    assert refused.returncode != 0 and "--replace" in refused.stderr
    assert "zusatz@example.org" in pg.fingerprint(target)
    replaced = run_restore(pg, key, one_backup, target, "--replace")
    assert replaced.returncode == 0, replaced.stderr
    assert "zusatz@example.org" not in pg.fingerprint(target)
    assert pg.fingerprint(target) == pg.fingerprint(PROD_DB)


def test_restore_with_wrong_key_creates_nothing(pg: Pg, one_backup: Path, tmp_path: Path) -> None:
    _require(shutil.which("age-keygen") is not None, "age fehlt")
    other = tmp_path / "other-key.txt"
    assert sh("age-keygen", "-o", str(other)).returncode == 0
    target = f"restore_{secrets.token_hex(3)}"
    r = run_restore(pg, other, one_backup, target)
    assert r.returncode != 0 and "Entschlüsselung" in r.stderr
    assert target not in pg.databases()


@pytest.mark.parametrize(
    "name", ["x; DROP DATABASE mitfahrer", "1abc", "a-b", "ä", "a" * 64, "a b"]
)
def test_restore_rejects_bad_database_names_before_touching_the_server(
    name: str, age_keys: tuple[str, Path], tmp_path: Path
) -> None:
    _, key = age_keys
    fake_backup = tmp_path / "x.dump.age"
    fake_backup.write_bytes(b"x")
    env = {**os.environ, "BACKUP_DB_EXEC": "echo SERVER-WURDE-ANGESPROCHEN"}
    r = sh("bash", str(RESTORE_SH), "--identity", str(key), "--file", str(fake_backup),
           "--target-db", name, env=env)  # fmt: skip
    assert r.returncode != 0
    assert "SERVER-WURDE-ANGESPROCHEN" not in r.stdout + r.stderr


def test_restore_rejects_a_key_inside_the_project(
    pg: Pg, age_keys: tuple[str, Path], one_backup: Path
) -> None:
    _, key = age_keys
    inside = REPO / f".p7-test-key-{secrets.token_hex(4)}.txt"
    shutil.copy(key, inside)
    try:
        r = run_restore(pg, inside, one_backup, f"restore_{secrets.token_hex(3)}")
        assert r.returncode != 0 and "Projektverzeichnis" in r.stderr
    finally:
        inside.unlink(missing_ok=True)


def test_restore_requires_all_arguments() -> None:
    r = sh("bash", str(RESTORE_SH), "--file", "x")
    assert r.returncode == 2 and "Aufruf" in r.stderr


# --- Backup: Prüfungen und Rotation ----------------------------------------------------------


def test_backup_rejects_missing_private_or_malformed_recipient(
    pg: Pg, age_keys: tuple[str, Path], backup_dir: Path
) -> None:
    _, key = age_keys
    private = next(
        line for line in key.read_text().splitlines() if line.startswith("AGE-SECRET-KEY-")
    )
    for bad, fragment in (
        (None, "nicht gesetzt"),
        (private, "PRIVATEN"),
        ("kein-schluessel", "öffentlicher age-Schlüssel"),
        ("age1abc; rm -rf /", "ungültige Zeichen"),
    ):
        r = run_backup(pg, backup_dir, bad)
        assert r.returncode != 0, bad
        assert fragment in r.stderr
        assert private not in r.stdout + r.stderr  # privater Schlüssel wird nicht ausgegeben
    assert not backups_in(backup_dir)


@pytest.mark.parametrize(("keep", "retention"), [("14", "14"), ("30", "14"), ("0", "14")])
def test_backup_aborts_unless_keep_days_is_below_retention(
    pg: Pg, age_keys: tuple[str, Path], backup_dir: Path, keep: str, retention: str
) -> None:
    recipient, _ = age_keys
    r = run_backup(
        pg, backup_dir, recipient, BACKUP_KEEP_DAYS=keep, RETENTION_DAYS_AFTER_EVENT=retention
    )
    assert r.returncode != 0
    assert not backups_in(backup_dir)


def test_backup_reads_settings_from_env_file_but_environment_wins(
    pg: Pg, age_keys: tuple[str, Path], tmp_path: Path
) -> None:
    recipient, _ = age_keys
    env_file = tmp_path / "x.env"
    out = tmp_path / "ausgabe"
    sentinel = tmp_path / "darf-nicht-entstehen"
    env_file.write_text(
        f'BACKUP_AGE_RECIPIENT="{recipient}"\nBACKUP_DIR={out}\nBACKUP_KEEP_DAYS=3\n'
        f"RETENTION_DAYS_AFTER_EVENT=14\nUNRELATED=$(touch {sentinel})\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("BACKUP_", "RETENTION_", "EVENT_END_DATE"))
    }
    env.update(BACKUP_DB_EXEC=pg.exec_prefix, ENV_FILE=str(env_file), EVENT_END_DATE="2099-12-31")
    r = sh("bash", str(BACKUP_SH), env=env)
    assert r.returncode == 0, r.stderr
    assert len(backups_in(out)) == 1 and "3 Tage" in r.stdout
    assert not sentinel.exists()  # .env wird gelesen, nicht ausgeführt
    env["BACKUP_KEEP_DAYS"] = "5"  # Umgebung hat Vorrang
    time.sleep(1.1)  # neuer Zeitstempel (Sekundenauflösung)
    r = sh("bash", str(BACKUP_SH), env=env)
    assert r.returncode == 0 and "5 Tage" in r.stdout


def _touch(path: Path, days_old: float) -> None:
    path.write_bytes(b"x")
    ts = time.time() - days_old * 86400
    os.utime(path, (ts, ts))


def test_rotation_deletes_only_own_old_backups(
    pg: Pg, age_keys: tuple[str, Path], backup_dir: Path
) -> None:
    recipient, _ = age_keys
    backup_dir.mkdir(mode=0o700)
    old = backup_dir / "mitfahrer-20200101T000000Z.dump.age"
    old_tmp = backup_dir / "mitfahrer-20200101T000001Z.dump.age.tmp"
    edge_old = backup_dir / "mitfahrer-20200102T000000Z.dump.age"
    edge_new = backup_dir / "mitfahrer-20200103T000000Z.dump.age"
    other_age = backup_dir / "fremd.age"
    notes = backup_dir / "notizen.txt"
    _touch(old, 30)
    _touch(old_tmp, 30)
    _touch(edge_old, 7.2)  # älter als 7 Tage -> weg
    _touch(edge_new, 6.8)  # jünger -> bleibt
    _touch(other_age, 90)
    _touch(notes, 90)
    r = run_backup(pg, backup_dir, recipient, BACKUP_KEEP_DAYS="7")
    assert r.returncode == 0, r.stderr
    assert not old.exists() and not old_tmp.exists() and not edge_old.exists()
    assert edge_new.exists() and other_age.exists() and notes.exists()
    assert len(backups_in(backup_dir)) == 2  # das neue und edge_new


def test_failed_dump_keeps_old_backups_and_leaves_no_files(
    pg: Pg, age_keys: tuple[str, Path], backup_dir: Path
) -> None:
    recipient, _ = age_keys
    backup_dir.mkdir(mode=0o700)
    old = backup_dir / "mitfahrer-20200101T000000Z.dump.age"
    _touch(old, 30)
    # Exec-Befehl, der scheitert (Container existiert nicht) -> pipefail muss anschlagen
    r = run_backup(pg, backup_dir, recipient, BACKUP_DB_EXEC="docker exec -i p7-gibt-es-nicht")
    assert r.returncode != 0
    assert old.exists()  # keine Rotation nach Fehlschlag
    assert not list(backup_dir.glob("*.tmp"))
    assert backups_in(backup_dir) == [old]
