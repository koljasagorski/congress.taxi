#!/bin/sh
# Rollt den Stack auf dem Gast „congress" (CT 220 auf hel) aus. Läuft AUF dem Gast als root, im
# Projektverzeichnis (/opt/congress):   sh deploy/hel/ausrollen.sh
#
# Idempotent: erzeugt .env nur, wenn sie fehlt (Zufalls-Secrets, Rechte 600), setzt die
# Zielmaschinen-Werte, bereitet OSRM nur vor, wenn die Daten fehlen, und startet den Stack.
# Gibt NIE den Inhalt von .env aus.
#
# Überschreibbar per Umgebung (Standardwerte = Teilstand mit Hamburg-Extrakt):
#   OSRM_PBF_URL  Geofabrik-Extrakt (Deutschland erst nach Ressourcenmessung, siehe PLAN.md)
#   API_BIND      interne Adresse des Gasts (Port 8000 nur dort, Zugriff beschränkt nftables.conf)
set -eu
cd "$(dirname "$0")/../.."

OSRM_PBF_URL="${OSRM_PBF_URL:-https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf}"
API_BIND="${API_BIND:-10.10.10.80}"
PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-congress.sgr.ski}"
EDGE_PROXY="${EDGE_PROXY:-10.10.10.70}"

[ -e .env ] || sh scripts/init-env.sh prod

# set_kv KEY WERT – ersetzt die Zeile KEY=… oder hängt sie an (awk statt sed -i)
set_kv() {
  awk -v k="$1" -v v="$2" '
    BEGIN { FS = "="; found = 0 }
    $1 == k { print k "=" v; found = 1; next }
    { print }
    END { if (!found) print k "=" v }
  ' .env > .env.tmp
  chmod 600 .env.tmp
  mv .env.tmp .env
}

set_kv APP_ENV production
set_kv COOKIE_SECURE true
set_kv OSRM_PBF_URL "$OSRM_PBF_URL"
set_kv API_BIND "$API_BIND"
set_kv DOMAIN "$PUBLIC_DOMAIN"
set_kv TRUSTED_PROXIES "$EDGE_PROXY"

if [ ! -f data/osrm/map.osrm.mldgr ]; then
  make osrm-prepare
else
  echo "OSRM-Daten vorhanden – Vorbereitung übersprungen (neu bauen: make osrm-prepare FORCE=1)."
fi

make up
make ps
