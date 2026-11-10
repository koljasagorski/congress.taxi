#!/bin/sh
# Ersetzt Platzhalter-Secrets (CHANGE_ME) in einer vorhandenen .env durch Zufallswerte.
# Macht `cp .env.example .env && make up` ohne manuelles Secret-Erzeugen lauffähig.
# Fasst nur Zeilen an, die noch exakt CHANGE_ME enthalten – echte Werte bleiben unberührt.
set -eu
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "FEHLER: .env fehlt. Erzeugen mit 'make env' oder 'cp .env.example .env'." >&2
  exit 1
fi

if ! grep -qE '^(POSTGRES_PASSWORD|JWT_SECRET)=CHANGE_ME[[:space:]]*$' .env; then
  exit 0
fi

pg_pw="$(openssl rand -hex 24)"
jwt="$(openssl rand -hex 32)"

awk -v pg="$pg_pw" -v jwt="$jwt" '
  /^POSTGRES_PASSWORD=CHANGE_ME[[:space:]]*$/ { print "POSTGRES_PASSWORD=" pg; next }
  /^JWT_SECRET=CHANGE_ME[[:space:]]*$/        { print "JWT_SECRET=" jwt; next }
  { print }
' .env > .env.tmp
chmod 600 .env.tmp
mv .env.tmp .env
echo "Platzhalter-Secrets in .env durch Zufallswerte ersetzt."
