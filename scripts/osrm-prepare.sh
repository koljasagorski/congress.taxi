#!/bin/sh
# OSRM-Datenvorbereitung: Download (Geofabrik) -> osrm-extract -> osrm-partition -> osrm-customize
# Aufruf:  make osrm-prepare            (FORCE=1 erzwingt Neuaufbau, KEEP_PBF=1 behält die PBF-Datei)
#
# ACHTUNG Ressourcen: Für germany-latest (PBF ≈ 4,9 GB) braucht osrm-extract sehr viel RAM
# (Schätzung ≈ 20 GB, ungeprüft). Für Entwicklung ein kleines Extrakt wählen (hamburg-latest).
set -eu
cd "$(dirname "$0")/.."

envval() { # envval KEY DEFAULT – liest KEY aus .env (ohne die Datei zu sourcen)
  if [ -f .env ]; then
    v="$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true)"
    if [ -n "${v:-}" ]; then printf '%s' "$v"; return; fi
  fi
  printf '%s' "$2"
}

url="$(envval OSRM_PBF_URL https://download.geofabrik.de/europe/germany-latest.osm.pbf)"
data_dir="$(envval OSRM_DATA_DIR ./data/osrm)"
mkdir -p "$data_dir"

if [ -f "$data_dir/map.osrm.mldgr" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "OSRM-Daten sind bereits vorbereitet ($data_dir). Mit FORCE=1 neu aufbauen."
  exit 0
fi

md5_of() {
  if command -v md5sum >/dev/null 2>&1; then md5sum "$1" | cut -d' ' -f1; else md5 -q "$1"; fi
}

pbf="$data_dir/map.osm.pbf"
echo ">> Lade $url"
curl -fL --retry 3 --retry-delay 5 -C - -o "$pbf.part" "$url"
mv "$pbf.part" "$pbf"

# Prüfsumme (Geofabrik veröffentlicht <datei>.md5); fehlt sie, wird nur gewarnt.
if expected="$(curl -fsL "$url.md5" 2>/dev/null | awk '{print $1}')" && [ -n "$expected" ]; then
  actual="$(md5_of "$pbf")"
  if [ "$expected" != "$actual" ]; then
    echo "FEHLER: MD5 stimmt nicht (erwartet $expected, erhalten $actual). Abbruch." >&2
    rm -f "$pbf"
    exit 1
  fi
  echo ">> MD5 geprüft: $actual"
else
  echo "WARNUNG: keine MD5-Prüfsumme verfügbar – Download ungeprüft." >&2
fi

run() { docker compose --profile tools run --rm -T osrm-prepare -c "$*"; }

echo ">> osrm-extract (kann lange dauern und viel RAM brauchen)"
run osrm-extract -p /opt/car.lua /data/map.osm.pbf
echo ">> osrm-partition"
run osrm-partition /data/map.osrm
echo ">> osrm-customize"
run osrm-customize /data/map.osrm

if [ "${KEEP_PBF:-0}" != "1" ]; then
  rm -f "$pbf"
fi
echo ">> Fertig. Jetzt 'make up' (bzw. 'docker compose restart osrm')."
