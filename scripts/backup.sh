#!/usr/bin/env bash
# Verschlüsseltes Datenbank-Backup: pg_dump (Custom-Format) -> age -> $BACKUP_DIR.
#
#   make backup            (oder:  bash scripts/backup.sh)
#   make purge-backups     (oder:  bash scripts/backup.sh --purge-expired)
#
# Ablauf: Der Dump wird direkt in `age` gepipet – es liegt NIE ein unverschlüsselter Dump auf
# der Platte. Geschrieben wird erst in eine .tmp-Datei, nach Erfolg per rename umbenannt.
# Danach werden eigene Backups (mitfahrer-*.dump.age) gelöscht, die älter als BACKUP_KEEP_DAYS
# Tage sind. Nur nach einem erfolgreichen Backup, nie vorher.
#
# SCHLÜSSEL (wichtig):
#   * BACKUP_AGE_RECIPIENT ist der ÖFFENTLICHE age-Schlüssel ("age1..."). Er darf auf dem Server
#     und in .env stehen.
#   * Der PRIVATE Schlüssel ("AGE-SECRET-KEY-1...") gehört NIE auf den Server und NIE ins
#     Repository. Erzeugen auf einem anderen Rechner:  age-keygen -o backup-key.txt
#     (die Zeile "# public key: age1..." ist der Empfänger). Das Skript lehnt private Schlüssel
#     als Empfänger ab.
#   * Ohne den privaten Schlüssel sind die Backups nicht lesbar – sicher getrennt aufbewahren.
#
# AUFBEWAHRUNG / DATENSCHUTZ:
#   Backups enthalten personenbezogene Daten (E-Mail-Adressen, Passwort-Hashes).
#   * BACKUP_KEEP_DAYS muss KLEINER sein als RETENTION_DAYS_AFTER_EVENT, sonst brechen wir ab.
#   * Das allein reicht nicht: Ein Backup vom Tag vor Fristablauf würde noch bis zu KEEP_DAYS Tage
#     nach dem Cleanup liegen bleiben. Deshalb gilt dieselbe Frist wie in app.cleanup
#     (EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT, Ende dieses Tages, 00:00 UTC des Folgetags):
#     Ab diesem Zeitpunkt werden ALLE eigenen Backups gelöscht, und es wird kein neues mehr
#     angelegt (der normale Lauf beendet sich dann nach dem Löschen mit Exit-Code 0).
#   * `--purge-expired` macht nur dieses Löschen (kein Dump, braucht weder age noch Datenbank).
#     Es als eigenen, häufigen Cron-Eintrag laufen lassen (z. B. stündlich) und den Cron NACH
#     dem Event NICHT abschalten, bevor die Frist vorbei und das Verzeichnis leer ist. Fällt der
#     Host-Cron komplett aus, bleibt die Löschung aus – dann `make purge-backups` von Hand.
#   * Ein Restore stellt den Stand des Backups wieder her – auch Konten, die Nutzer seitdem per
#     DELETE /api/me gelöscht haben. Nach einem Restore den Cleanup erneut laufen lassen
#     (make cleanup) und gelöschte Konten ggf. erneut entfernen.
#
# KONFIGURATION (Umgebung hat Vorrang vor .env; es wird nur gelesen, nicht ausgeführt):
#   BACKUP_AGE_RECIPIENT   Pflicht, "age1..."
#   BACKUP_DIR             Standard ./backups (Verzeichnis 0700, Dateien 0600)
#   BACKUP_KEEP_DAYS       Standard 7
#   RETENTION_DAYS_AFTER_EVENT  Standard 14
#   BACKUP_DB_EXEC         Befehl, der in den Datenbank-Container führt (stdin durchreichen!).
#                          Standard: "docker compose exec -T db". Tests: "docker exec -i <name>".
#   EVENT_END_DATE         YYYY-MM-DD, Ende des Events (für die Löschfrist; wie bei app.cleanup)
#   EVENT_START_DATE       optional, YYYY-MM-DD; liegt EVENT_END_DATE davor, bricht das Skript ab (OPS-4)
#   ENV_FILE               Standard ./.env
#   BACKUP_NOW_EPOCH       nur für Tests: aktuelle Zeit (Sekunden seit 1970, UTC) ersetzen
#
# Die Datenbank-Zugangsdaten stehen im Container (POSTGRES_USER/POSTGRES_DB); dort läuft pg_dump
# über den lokalen Socket. Es werden keine Passwörter als Argument übergeben oder ausgegeben.
#
# CI: "age" kommt per `sudo apt-get install -y age`; lokal z. B. `brew install age`.
#
# Beispiel-Cron (als der Nutzer, dem das Compose-Projekt gehört):
#   15 3 * * *  cd /pfad/zum/projekt && make backup >> backups/backup.log 2>&1
#   5 * * * *   cd /pfad/zum/projekt && make purge-backups >> backups/backup.log 2>&1
# SC2016: Variablen sollen bewusst erst im Datenbank-Container expandiert werden.
# shellcheck disable=SC2016
set -euo pipefail

cd "$(dirname "$0")/.."

MODE=backup
while [ $# -gt 0 ]; do
  case "$1" in
    --purge-expired) MODE=purge ;;
    -h | --help)
      echo "Aufruf: backup.sh [--purge-expired]" >&2
      exit 0
      ;;
    *)
      echo "backup: unbekannte Option: $1" >&2
      echo "Aufruf: backup.sh [--purge-expired]" >&2
      exit 2
      ;;
  esac
  shift
done

ENV_FILE="${ENV_FILE:-.env}"

# Wert aus der Umgebung, sonst aus der .env (letzte Zeile gewinnt), sonst Standard.
cfg() { # $1 = Name, $2 = Standard
  local name="$1" default="${2-}" value=""
  value="${!name-}"
  if [ -z "$value" ] && [ -f "$ENV_FILE" ]; then
    value="$(sed -n "s/^${name}=//p" "$ENV_FILE" | tail -n 1)"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
  fi
  printf '%s' "${value:-$default}"
}

die() {
  echo "backup: FEHLER: $*" >&2
  exit 1
}

EVENT_END_DATE="$(cfg EVENT_END_DATE)"
RECIPIENT="$(cfg BACKUP_AGE_RECIPIENT)"
BACKUP_DIR="$(cfg BACKUP_DIR ./backups)"
KEEP_DAYS="$(cfg BACKUP_KEEP_DAYS 7)"
EVENT_START_DATE="$(cfg EVENT_START_DATE)"
RETENTION_DAYS="$(cfg RETENTION_DAYS_AFTER_EVENT 14)"
DB_EXEC="$(cfg BACKUP_DB_EXEC 'docker compose exec -T db')"

case "$RETENTION_DAYS" in '' | *[!0-9]*) die "RETENTION_DAYS_AFTER_EVENT muss eine ganze Zahl sein." ;; esac

# --- Löschfrist (identisch zu app.cleanup.retention_expires_at) -------------------------------
# Instant, ab dem (einschließlich) gelöscht wird: 00:00:00 UTC des Tages nach
# EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT. Reine awk-Rechnung (Kalendertag -> Tage seit 1970),
# damit kein GNU-/BSD-spezifisches `date` nötig ist. Leer, wenn EVENT_END_DATE fehlt.
expiry_epoch() {
  [ -n "$EVENT_END_DATE" ] || return 0
  case "$EVENT_END_DATE" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) die "EVENT_END_DATE muss das Format YYYY-MM-DD haben." ;;
  esac
  # Plausibilität (wie Settings in der API): Ein Tippfehler im Enddatum würde sonst alle Backups
  # unwiederbringlich löschen. Gleiches Format => der String-Vergleich entspricht dem Datumsvergleich.
  case "$EVENT_START_DATE" in
    '' | [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
    *) die "EVENT_START_DATE muss das Format YYYY-MM-DD haben." ;;
  esac
  if [ -n "$EVENT_START_DATE" ] && [[ "$EVENT_END_DATE" < "$EVENT_START_DATE" ]]; then
    die "EVENT_END_DATE ($EVENT_END_DATE) liegt vor EVENT_START_DATE ($EVENT_START_DATE) – nichts gelöscht, bitte Daten in .env prüfen."
  fi
  awk -v d="$EVENT_END_DATE" -v n="$RETENTION_DAYS" 'BEGIN {
    split(d, a, "-"); y = a[1] + 0; m = a[2] + 0; dd = a[3] + 0
    if (m < 1 || m > 12 || dd < 1 || dd > 31) exit 1
    if (m <= 2) y--
    era = int(y / 400); yoe = y - era * 400
    doy = int((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + dd - 1
    doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
    days = era * 146097 + doe - 719468
    printf "%.0f", (days + n + 1) * 86400
  }' || die "EVENT_END_DATE ist kein gültiges Datum."
}

now_epoch() {
  local now="${BACKUP_NOW_EPOCH:-}"
  [ -n "$now" ] || now="$(date -u +%s)"
  case "$now" in '' | *[!0-9]*) die "BACKUP_NOW_EPOCH muss eine ganze Zahl sein." ;; esac
  printf '%s' "$now"
}

# Löscht ALLE eigenen Backups (auch halbfertige .tmp); fremde Dateien bleiben unberührt.
purge_all() {
  local n=0 f
  if [ -d "$BACKUP_DIR" ]; then
    for f in "$BACKUP_DIR"/mitfahrer-*.dump.age "$BACKUP_DIR"/mitfahrer-*.dump.age.tmp; do
      if [ -f "$f" ]; then
        rm -f -- "$f"
        n=$((n + 1))
      fi
    done
  fi
  printf '%s' "$n"
}

# Zuweisungen (nicht `[ "$(...)" ]`): ein `die` in der Befehlsersetzung bricht sonst nur die Subshell ab.
EXPIRY="$(expiry_epoch)"
NOW="$(now_epoch)"
if [ -n "$EXPIRY" ] && [ "$NOW" -ge "$EXPIRY" ]; then
  removed="$(purge_all)"
  echo "backup: Aufbewahrungsfrist abgelaufen – ${removed} Backup(s) gelöscht, kein neues Backup angelegt."
  exit 0
fi
if [ "$MODE" = purge ]; then
  if [ -z "$EVENT_END_DATE" ]; then
    echo "backup: EVENT_END_DATE ist nicht gesetzt – Frist unbekannt, nichts gelöscht."
  else
    echo "backup: Aufbewahrungsfrist noch nicht abgelaufen, nichts zu löschen."
  fi
  exit 0
fi

[ -n "$RECIPIENT" ] || die "BACKUP_AGE_RECIPIENT ist nicht gesetzt (öffentlicher Schlüssel, age1...)."
case "$RECIPIENT" in
  AGE-SECRET-KEY-*)
    die "BACKUP_AGE_RECIPIENT enthält einen PRIVATEN Schlüssel. Nur den öffentlichen Schlüssel (age1...) eintragen und den privaten entfernen."
    ;;
  age1*) ;;
  *) die "BACKUP_AGE_RECIPIENT muss ein öffentlicher age-Schlüssel sein (beginnt mit age1)." ;;
esac
case "$RECIPIENT" in
  *[!a-z0-9]*) die "BACKUP_AGE_RECIPIENT enthält ungültige Zeichen." ;;
esac

case "$KEEP_DAYS" in '' | *[!0-9]*) die "BACKUP_KEEP_DAYS muss eine ganze Zahl sein." ;; esac
[ "$KEEP_DAYS" -ge 1 ] || die "BACKUP_KEEP_DAYS muss mindestens 1 sein."
if [ "$KEEP_DAYS" -ge "$RETENTION_DAYS" ]; then
  die "BACKUP_KEEP_DAYS ($KEEP_DAYS) muss kleiner sein als RETENTION_DAYS_AFTER_EVENT ($RETENTION_DAYS): Backups dürfen die Löschfrist nicht überleben."
fi

command -v age >/dev/null 2>&1 || die "age ist nicht installiert (brew install age bzw. apt-get install age)."

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$BACKUP_DIR/mitfahrer-$stamp.dump.age"
tmp="$final.tmp"
[ ! -e "$final" ] || die "Zieldatei existiert bereits (zwei Backups in derselben Sekunde?)."

cleanup_tmp() { rm -f "$tmp"; }
trap cleanup_tmp EXIT INT TERM

# $DB_EXEC absichtlich ungequotet (Wortteilung); Globbing aus.
set -f
# shellcheck disable=SC2086
$DB_EXEC sh -c 'exec pg_dump --format=custom --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' |
  age -r "$RECIPIENT" -o "$tmp"
set +f

[ -s "$tmp" ] || die "Backup-Datei ist leer."
chmod 600 "$tmp"
mv "$tmp" "$final"
trap - EXIT INT TERM

# Rotation: nur eigene Dateien, nur nach erfolgreichem Backup.
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'mitfahrer-*.dump.age' -o -name 'mitfahrer-*.dump.age.tmp' \) \
  -mmin "+$((KEEP_DAYS * 1440))" -exec rm -f {} +

size="$(wc -c <"$final" | tr -d ' ')"
echo "backup: ok -> $final (${size} Bytes, aufbewahrt: ${KEEP_DAYS} Tage)"
