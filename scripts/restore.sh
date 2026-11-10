#!/usr/bin/env bash
# Backup wiederherstellen: age-Entschlüsselung -> pg_restore in eine ANGEGEBENE Ziel-Datenbank.
#
#   bash scripts/restore.sh --identity KEY.txt --file backups/mitfahrer-<ZEIT>.dump.age \
#                           --target-db mitfahrer_restore
#   make restore IDENTITY=KEY.txt FILE=backups/... TARGET_DB=mitfahrer_restore
#
# Der PRIVATE age-Schlüssel (--identity) liegt NICHT auf dem Server und nicht im Repository:
# für den Restore vorübergehend von einem sicheren Ort bereitstellen (Datei mit Rechten 600) und
# danach wieder entfernen. Ein Schlüssel innerhalb des Projektverzeichnisses wird abgelehnt.
#
# Sicherheitsregeln:
#   * Existiert die Ziel-Datenbank nicht, wird sie angelegt und befüllt.
#   * Existiert sie schon, wird nur mit --replace überschrieben (pg_restore --clean, eine
#     Transaktion: bei einem Fehler bleibt der alte Stand).
#   * Die PRODUKTIV-Datenbank (POSTGRES_DB des Containers) wird nur mit der ausdrücklichen
#     Bestätigung --yes-overwrite-production überschrieben. Besser: erst in eine andere
#     Datenbank einspielen und prüfen.
#   * Nach jedem Restore den Cleanup erneut laufen lassen (make cleanup): Das Backup kann Daten
#     enthalten, deren Aufbewahrungsfrist inzwischen abgelaufen ist.
#   * Ein Restore bringt auch Konten zurück, die Nutzer nach dem Backup per DELETE /api/me
#     gelöscht haben (und deren Fahrten/Anfragen). Das Skript kann sie nicht erkennen: Gelöschte
#     Konten nach dem Restore erneut entfernen (Löschwünsche gesondert festhalten) – Backups also
#     nur im Notfall einspielen und die Produktiv-Datenbank nicht ohne Not überschreiben.
#
# BACKUP_DB_EXEC: Befehl in den Datenbank-Container (Standard "docker compose exec -T db";
# Tests: "docker exec -i <name>"). Zugangsdaten stehen im Container; es werden keine Passwörter
# als Argumente übergeben.
# SC2016: Variablen sollen bewusst erst im Datenbank-Container expandiert werden.
# shellcheck disable=SC2016
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd -P)"

die() {
  echo "restore: FEHLER: $*" >&2
  exit 1
}

usage() {
  echo "Aufruf: restore.sh --identity SCHLÜSSELDATEI --file BACKUP.dump.age --target-db NAME [--replace] [--yes-overwrite-production]" >&2
  exit 2
}

IDENTITY=""
FILE=""
TARGET=""
REPLACE=0
PROD_OK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --identity) [ $# -ge 2 ] || usage; IDENTITY="$2"; shift 2 ;;
    --file) [ $# -ge 2 ] || usage; FILE="$2"; shift 2 ;;
    --target-db) [ $# -ge 2 ] || usage; TARGET="$2"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --yes-overwrite-production) PROD_OK=1; shift ;;
    -h | --help) usage ;;
    *) echo "restore: unbekannte Option: $1" >&2; usage ;;
  esac
done
if [ -z "$IDENTITY" ] || [ -z "$FILE" ] || [ -z "$TARGET" ]; then usage; fi

DB_EXEC="${BACKUP_DB_EXEC:-docker compose exec -T db}"

command -v age >/dev/null 2>&1 || die "age ist nicht installiert."
[ -f "$IDENTITY" ] || die "Identity-Datei nicht gefunden."
[ -f "$FILE" ] || die "Backup-Datei nicht gefunden."

# Schlüssel im Projektverzeichnis ablehnen (Gefahr, ihn zu committen / auf dem Server zu lassen)
identity_dir="$(cd "$(dirname "$IDENTITY")" && pwd -P)"
case "$identity_dir/" in
  "$PROJECT_ROOT"/*) die "Der private Schlüssel liegt im Projektverzeichnis. Bitte außerhalb ablegen." ;;
esac

# Datenbankname: Bezeichner ohne Sonderzeichen (wird in SQL-Befehle eingesetzt)
case "$TARGET" in
  [A-Za-z_]*) ;;
  *) die "Ungültiger Datenbankname." ;;
esac
case "$TARGET" in
  *[!A-Za-z0-9_]*) die "Ungültiger Datenbankname (erlaubt: Buchstaben, Ziffern, Unterstrich)." ;;
esac
[ "${#TARGET}" -le 63 ] || die "Datenbankname zu lang."

# Vorab prüfen, dass sich die Datei mit diesem Schlüssel entschlüsseln lässt und ein
# pg_dump-Archiv ("PGDMP") enthält – bevor irgendetwas in der Datenbank angelegt wird.
magic="$(age -d -i "$IDENTITY" "$FILE" 2>/dev/null | head -c 5 || true)"
[ "$magic" = "PGDMP" ] || die "Entschlüsselung fehlgeschlagen oder keine gültige Backup-Datei (falscher Schlüssel?)."

set -f
# shellcheck disable=SC2086
dbx() { $DB_EXEC "$@"; }

PROD_DB="$(dbx sh -c 'printf %s "$POSTGRES_DB"')"
[ -n "$PROD_DB" ] || die "Konnte die Produktiv-Datenbank nicht ermitteln (läuft der Datenbank-Container?)."

if [ "$TARGET" = "$PROD_DB" ]; then
  [ "$PROD_OK" -eq 1 ] || die "Ziel ist die PRODUKTIV-Datenbank '$PROD_DB'. Zum Überschreiben ausdrücklich --yes-overwrite-production angeben (besser: erst in eine andere Datenbank einspielen)."
  REPLACE=1
fi

exists="$(dbx sh -c 'psql -U "$POSTGRES_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '"'$TARGET'"'"')"
if [ "$exists" = "1" ]; then
  [ "$REPLACE" -eq 1 ] || die "Die Datenbank '$TARGET' existiert bereits. Mit --replace überschreiben oder einen neuen Namen wählen."
  clean_flags="--clean --if-exists --single-transaction"
else
  dbx sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$TARGET" >/dev/null
  clean_flags="--single-transaction"
fi

# shellcheck disable=SC2086
age -d -i "$IDENTITY" "$FILE" |
  dbx sh -c 'exec pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --exit-on-error '"$clean_flags" sh "$TARGET"
set +f

echo "restore: ok -> Datenbank '$TARGET'"
echo "restore: HINWEIS: jetzt den Cleanup erneut laufen lassen (make cleanup), damit abgelaufene Daten nicht wieder auftauchen."
echo "restore: HINWEIS: Konten, die seit dem Backup per DELETE /api/me gelöscht wurden, sind wieder vorhanden und müssen erneut entfernt werden."
