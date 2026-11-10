#!/bin/sh
# Leitet aus TILE_URL den Ursprung (Schema + Host[:Port]) für die Content-Security-Policy ab.
#
#   sh tile-origin.sh 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'  ->  https://tile.openstreetmap.org
#   sh tile-origin.sh 'https://{s}.tile.example.org/{z}/{x}/{y}.png'    ->  https://*.tile.example.org
#   sh tile-origin.sh '/tiles/{z}/{x}/{y}.png'                          ->  (leer: 'self' genügt)
#
# Streng validiert: Die Ausgabe landet in einem HTTP-Header und im Caddyfile, darf also weder
# Leerzeichen, Semikolons, Anführungszeichen noch Platzhalter-Syntax enthalten. Ungültige Werte
# führen zu Exit-Code 1 (Caddy startet dann bewusst nicht).
set -eu

url="${1-}"

fail() {
  echo "tile-origin: TILE_URL ist ungültig ($1)" >&2
  exit 1
}

# grep prüft zeilenweise: Zeilenumbrüche würden die Prüfung umgehen -> vorab ablehnen
nl='
'
cr="$(printf '\r')"
case "$url" in
  *"$nl"* | *"$cr"*) fail "Steuerzeichen" ;;
esac

case "$url" in
  "") exit 0 ;;
  /* | ./*) exit 0 ;; # relative URL: gleiche Origin
  http://* | https://*) ;;
  *) fail "erwartet https://host/... oder einen relativen Pfad" ;;
esac

scheme="${url%%://*}"
rest="${url#*://}"
hostport="${rest%%/*}"
hostport="${hostport%%\?*}"
hostport="${hostport%%#*}"

case "$hostport" in
  *@*) fail "Zugangsdaten in der URL sind nicht erlaubt" ;;
esac

case "$hostport" in
  *"*"*) fail "Wildcards gehören nicht in die URL" ;;
esac

# Leaflet-Subdomain-Platzhalter {s} als erstes Label -> CSP-Wildcard
case "$hostport" in
  "{s}."*) hostport="*.${hostport#\{s\}.}" ;;
esac

if ! printf '%s' "$hostport" |
  grep -Eq '^(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'; then
  fail "Host nicht erlaubt"
fi

printf '%s://%s\n' "$scheme" "$hostport"
