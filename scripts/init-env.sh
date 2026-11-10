#!/bin/sh
# Erzeugt .env aus .env.example mit Zufalls-Secrets.
#   sh scripts/init-env.sh        -> Produktion (Standardwerte)
#   sh scripts/init-env.sh dev    -> Entwicklung (Hamburg-Extrakt, APP_ENV=development, COOKIE_SECURE=false)
# Überschreibt eine vorhandene .env NIE.
set -eu
cd "$(dirname "$0")/.."

mode="${1:-prod}"

if [ -e .env ]; then
  echo ".env existiert bereits – nichts geändert." >&2
  exit 1
fi

rand_hex() { openssl rand -hex "$1"; }

pg_pw="$(rand_hex 24)"
jwt="$(rand_hex 32)"

# awk statt sed -i (portabel zwischen macOS und Linux)
arch="$(uname -m)"
case "$arch" in
  arm64|aarch64) arm=1 ;;
  *) arm=0 ;;
esac

awk -v pg="$pg_pw" -v jwt="$jwt" -v mode="$mode" -v arm="$arm" '
  /^POSTGRES_PASSWORD=/ { print "POSTGRES_PASSWORD=" pg; next }
  /^JWT_SECRET=/        { print "JWT_SECRET=" jwt; next }
  arm == 1 && /^POSTGIS_IMAGE=/ { print "POSTGIS_IMAGE=imresamu/postgis:16-3.5-bookworm"; next }
  mode == "dev" && /^APP_ENV=/        { print "APP_ENV=development"; next }
  mode == "dev" && /^COOKIE_SECURE=/  { print "COOKIE_SECURE=false"; next }
  mode == "dev" && /^OSRM_PBF_URL=/   { print "OSRM_PBF_URL=https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf"; next }
  { print }
' .env.example > .env

chmod 600 .env

if [ "$arm" = "1" ]; then
  echo "Hinweis: arm64 erkannt – postgis/postgis hat kein arm64-Image;" >&2
  echo "         POSTGIS_IMAGE wurde auf imresamu/postgis:16-3.5-bookworm gesetzt." >&2
fi

echo ".env erzeugt (Modus: $mode, Secrets zufällig, Rechte 600)."
