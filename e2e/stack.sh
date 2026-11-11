#!/bin/sh
# E2E-Stack: eigenes Compose-Projekt "mitfahrer-e2e" (db, osrm, api, web, caddy) neben dem Dev-Stack.
#
#   sh e2e/stack.sh up      Stack starten und auf Bereitschaft warten (legt e2e/.stack/e2e.env an)
#   sh e2e/stack.sh down    Stack stoppen, Volumes und e2e.env löschen (frische DB beim nächsten Start)
#   sh e2e/stack.sh logs    Logs ausgeben
#   sh e2e/stack.sh url     Basis-URL für die Tests ausgeben
#   sh e2e/stack.sh ps      Containerstatus
#
# Ports (nur localhost): E2E_HTTP_PORT (Standard 18080), E2E_DB_PORT (55433), E2E_OSRM_PORT (55001).
# Voraussetzung: vorbereitete OSRM-Daten (make osrm-prepare, Hamburg-Extrakt) in data/osrm bzw. E2E_OSRM_DATA_DIR.
# Dev-Stack (Projekt "mitfahrer") und alles andere wird nicht angefasst; down löscht nur Volumes dieses Projekts.
set -eu
cd "$(dirname "$0")/.."

PROJECT="mitfahrer-e2e"
STACK_DIR="e2e/.stack"
ENV_FILE="$STACK_DIR/e2e.env"
P_HTTP="${E2E_HTTP_PORT:-18080}"
P_DB="${E2E_DB_PORT:-55433}"
P_OSRM="${E2E_OSRM_PORT:-55001}"
OSRM_DIR="${E2E_OSRM_DATA_DIR:-./data/osrm}"
BASE_URL_DEFAULT="http://127.0.0.1:$P_HTTP"
TILE_URL_E2E="https://tiles.e2e.invalid/{z}/{x}/{y}.png"

# Sicherheitsgurt: nie versehentlich das Projekt des Dev-Stacks verwalten.
if [ "$PROJECT" = "mitfahrer" ]; then
  echo "FEHLER: Projektname darf nicht 'mitfahrer' sein." >&2
  exit 1
fi

# Umgebungsvariablen der Shell schlagen --env-file und -f: ausschalten.
unset COMPOSE_FILE COMPOSE_PROFILES COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES
unset POSTGRES_PASSWORD POSTGRES_DB POSTGRES_USER JWT_SECRET DB_PORT OSRM_PORT HTTP_PORT HTTPS_PORT \
  CADDY_SITE_ADDRESS CADDY_SUBNET CADDY_IPV4 CADDY_IP_RANGE TILE_URL DOMAIN OSRM_DATA_DIR POSTGIS_IMAGE OSRM_VERSION \
  API_BIND API_PORT WEB_BIND WEB_PORT TRUSTED_PROXIES CADDY_TRUSTED_PROXIES CADDY_HSTS

compose() {
  docker compose -p "$PROJECT" --env-file "$ENV_FILE" \
    -f docker-compose.yml -f docker-compose.proxy.yml -f e2e/docker-compose.e2e.yml \
    --profile proxy "$@"
}

make_env() {
  mkdir -p "$STACK_DIR"
  if [ -f "$ENV_FILE" ]; then
    return
  fi
  arm=0
  case "$(uname -m)" in arm64 | aarch64) arm=1 ;; esac
  pg_pw="$(openssl rand -hex 24)"
  jwt="$(openssl rand -hex 32)"
  ovr="$STACK_DIR/overrides.tmp"
  # Erste Spalte = Schlüssel, Rest = Wert. Ersetzt Zeilen aus .env.example, fehlende werden angehängt.
  {
    echo "COMPOSE_PROJECT_NAME=$PROJECT"
    echo "POSTGRES_PASSWORD=$pg_pw"
    echo "JWT_SECRET=$jwt"
    [ "$arm" = "1" ] && echo "POSTGIS_IMAGE=imresamu/postgis:16-3.5-bookworm"
    echo "OSRM_PBF_URL=https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf"
    echo "OSRM_DATA_DIR=$OSRM_DIR"
    echo "DB_PORT=$P_DB"
    echo "OSRM_PORT=$P_OSRM"
    # HTTP ohne TLS auf localhost/127.0.0.1; Cookies ohne Secure-Attribut
    echo "HTTP_PORT=$P_HTTP"
    echo "HTTPS_PORT=$((P_HTTP + 1))"
    echo "CADDY_SITE_ADDRESS=http://:$P_HTTP"
    echo "COOKIE_SECURE=false"
    echo "ALLOWED_ORIGINS=http://127.0.0.1:$P_HTTP,http://localhost:$P_HTTP"
    echo "CADDY_HSTS=max-age=0"
    # Eigenes Subnetz, damit der Stack nicht mit dem Standard (172.29.71.0/24) kollidiert
    echo "CADDY_SUBNET=172.29.72.0/24"
    echo "CADDY_IPV4=172.29.72.2"
    echo "CADDY_IP_RANGE=172.29.72.128/25"
    # Kachelserver: Platzhalter-Host; die Tests beantworten die Anfragen selbst (kein Internet nötig)
    echo "TILE_URL=$TILE_URL_E2E"
    # Angehobene Rate-Limits NUR für den Testlauf (alle Browser teilen sich eine Client-IP)
    echo "RATE_LIMIT_API_PER_MIN=100000"
    echo "RATE_LIMIT_MATCH_PER_MIN=100000"
    echo "RATE_LIMIT_LOGIN_PER_MIN=100000"
    echo "RATE_LIMIT_REGISTER_PER_HOUR=100000"
    echo "RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_IP_PER_MIN=100000"
    echo "RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_PER_MIN=100000"
    echo "RATE_LIMIT_MATCH_USER_PER_MIN=100000"
    echo "RATE_LIMIT_RIDES_CREATE_PER_HOUR=100000"
    echo "RATE_LIMIT_REQUESTS_CREATE_PER_HOUR=100000"
  } > "$ovr"
  umask 077
  awk -v ovr="$ovr" '
    BEGIN {
      while ((getline line < ovr) > 0) {
        i = index(line, "=")
        k = substr(line, 1, i - 1)
        v[k] = substr(line, i + 1)
        order[++n] = k
      }
    }
    {
      i = index($0, "=")
      if (i > 0 && $0 !~ /^#/) {
        k = substr($0, 1, i - 1)
        if (k in v) { print k "=" v[k]; used[k] = 1; next }
      }
      print
    }
    END { for (j = 1; j <= n; j++) if (!(order[j] in used)) print order[j] "=" v[order[j]] }
  ' .env.example > "$ENV_FILE"
  rm -f "$ovr"
  chmod 600 "$ENV_FILE"
  echo "e2e: $ENV_FILE erzeugt (Zufalls-Secrets, nicht im Git)." >&2
}

wait_http() { # wait_http URL – wartet bis zu 120 s auf HTTP 200
  i=0
  while [ "$i" -lt 60 ]; do
    if curl -fsS -o /dev/null "$1" 2>/dev/null; then return 0; fi
    i=$((i + 1))
    sleep 2
  done
  echo "FEHLER: $1 nicht erreichbar." >&2
  return 1
}

cmd="${1:-up}"
case "$cmd" in
  up)
    if [ ! -f "$OSRM_DIR/map.osrm.mldgr" ]; then
      echo "FEHLER: OSRM-Daten fehlen in $OSRM_DIR (make osrm-prepare, Hamburg-Extrakt)." >&2
      exit 1
    fi
    make_env
    compose up -d --build --wait db osrm api web caddy
    wait_http "$BASE_URL_DEFAULT/api/health"
    wait_http "$BASE_URL_DEFAULT/api/config"
    echo "e2e: Stack bereit unter $BASE_URL_DEFAULT" >&2
    ;;
  down)
    if [ -f "$ENV_FILE" ]; then
      compose down -v --remove-orphans
      rm -f "$ENV_FILE"
    else
      echo "e2e: kein Stack angelegt." >&2
    fi
    ;;
  logs)
    compose logs --no-color --tail="${LOG_TAIL:-200}"
    ;;
  ps)
    compose ps
    ;;
  url)
    echo "$BASE_URL_DEFAULT"
    ;;
  *)
    echo "Aufruf: sh e2e/stack.sh up|down|logs|ps|url" >&2
    exit 2
    ;;
esac
