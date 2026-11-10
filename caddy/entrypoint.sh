#!/bin/sh
# Startskript des Caddy-Containers: leitet TILE_ORIGIN aus TILE_URL ab und startet Caddy.
set -eu

TILE_ORIGIN="$(sh /etc/caddy/tile-origin.sh "${TILE_URL-}")" || {
  echo "caddy: Start abgebrochen (TILE_URL prüfen)" >&2
  exit 1
}
export TILE_ORIGIN

exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
