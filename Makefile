# Mitfahrer-Matching 40C3 – Make-Targets
# Kompatibel mit GNU Make 3.81 (macOS) und 4.x (Linux).

SHELL := /bin/sh
COMPOSE := docker compose

# Oberfläche unter EINER Origin: Caddy (Compose-Profil "proxy") leitet /api/* an die API, alles andere
# an die SPA. Als Umgebungsvariablen gesetzt, damit .env nicht angepasst werden muss und `make down-proxy`
# dieselben Dienste (inkl. Caddy) wieder abbaut.
PROXY_ENV := COMPOSE_PROFILES=proxy COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml

.PHONY: help env fill-secrets up up-proxy down down-proxy restart logs ps build migrate db-up test lint fmt osrm-prepare seed-demo clean-data

help: ## Diese Hilfe
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env: ## .env mit Zufalls-Secrets erzeugen (make env DEV=1: Hamburg-Extrakt, Development-Modus)
	@sh scripts/init-env.sh $(if $(DEV),dev,prod)

up: ## Gesamten Stack starten (füllt Platzhalter-Secrets, baut Images, wartet auf Healthchecks)
	@sh scripts/fill-secrets.sh
	$(COMPOSE) up -d --build --wait

up-proxy: ## Stack MIT Caddy starten: Oberfläche + API unter https://$DOMAIN (Standard https://localhost; Ports 80/443 müssen frei sein)
	@sh scripts/fill-secrets.sh
	$(PROXY_ENV) $(COMPOSE) up -d --build --wait

down: ## Stack stoppen (Daten bleiben)
	$(COMPOSE) down

down-proxy: ## Mit `make up-proxy` gestarteten Stack (inkl. Caddy) stoppen (Daten bleiben)
	$(PROXY_ENV) $(COMPOSE) down

restart: ## Stack neu starten
	$(COMPOSE) restart

logs: ## Logs verfolgen
	$(COMPOSE) logs -f --tail=100

ps: ## Containerstatus
	$(COMPOSE) ps

build: ## Images bauen
	$(COMPOSE) build

fill-secrets: ## Platzhalter-Secrets (CHANGE_ME) in .env durch Zufallswerte ersetzen
	@sh scripts/fill-secrets.sh

db-up: fill-secrets ## Nur die Datenbank starten und auf Healthcheck warten
	$(COMPOSE) up -d --wait db

migrate: ## Alembic-Migrationen im api-Container ausführen
	$(COMPOSE) run --rm --no-deps api alembic upgrade head

test: fill-secrets db-up ## Tests (startet die DB; OSRM-Live-Tests laufen, wenn OSRM erreichbar ist; liest .env)
	cd api && set -a && . ../.env && set +a && POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=$$DB_PORT \
		OSRM_URL=http://127.0.0.1:$$OSRM_PORT uv run pytest $(PYTEST_ARGS)

lint: ## ruff + mypy
	cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app

fmt: ## ruff format + Autofixes
	cd api && uv run ruff check --fix . && uv run ruff format .

osrm-prepare: ## OSRM-Daten herunterladen und vorbereiten (FORCE=1 erzwingt Neuaufbau)
	@sh scripts/osrm-prepare.sh

seed-demo: ## Demo-Fahrer/-Mitfahrer über die laufende API anlegen (nie in Produktion; SEED_ARGS=--base-url …)
	cd api && uv run python ../scripts/seed_demo.py $(SEED_ARGS)

clean-data: ## ACHTUNG: löscht Datenbank-Volume (nicht die OSRM-Daten)
	$(COMPOSE) down -v

# --- Betrieb (Phase 7) ---
# ACHTUNG make cleanup: arbeitet auf der Datenbank aus .env. Mit --now in der Zukunft löscht es
# LIVE-Daten. Vorher DRY=1 verwenden.
.PHONY: backup purge-backups restore cleanup

backup: ## Verschlüsseltes DB-Backup (age) nach BACKUP_DIR, rotiert nach BACKUP_KEEP_DAYS
	@bash scripts/backup.sh

purge-backups: ## Alle Backups löschen, sobald die Aufbewahrungsfrist abgelaufen ist (stündlich per Cron)
	@bash scripts/backup.sh --purge-expired

restore: ## Backup einspielen: make restore IDENTITY=key.txt FILE=backups/x.dump.age TARGET_DB=name [RESTORE_ARGS=--replace]
	@bash scripts/restore.sh --identity "$(IDENTITY)" --file "$(FILE)" --target-db "$(TARGET_DB)" $(RESTORE_ARGS)

cleanup: ## Retention-Cleanup einmal ausführen (DRY=1: nur zählen; CLEANUP_ARGS="--now 2027-02-01T00:00:00Z" oder "--confirm-expired")
	$(COMPOSE) run --rm --no-deps --entrypoint python cleanup -m app.cleanup $(if $(DRY),--dry-run,) $(CLEANUP_ARGS)

# --- Frontend (Phase 6) ---
# Dieselben Prüfungen wie der CI-Job "web". Benötigt Node >= 22.
.PHONY: web-install web-check

web-install: ## Frontend-Abhängigkeiten installieren (npm ci in web/)
	cd web && npm ci --no-audit --no-fund

web-check: ## Frontend prüfen: tsc, eslint, vitest, Build, check:external (wie CI; installiert fehlende Abhängigkeiten)
	cd web && if [ ! -d node_modules ]; then npm ci --no-audit --no-fund; fi
	cd web && npx tsc --noEmit && npm run lint && npm test && npm run build && npm run check:external

# --- E2E (Phase 8) ---
# Playwright gegen den echten Stack (Caddy -> SPA + API -> PostGIS + OSRM Hamburg). Eigenes Compose-Projekt
# "mitfahrer-e2e" mit eigenen Ports (18080/55433/55001) und angehobenen Rate-Limits NUR dafür; der Dev-Stack und
# die Produktions-Standardwerte bleiben unberührt. Voraussetzung: make osrm-prepare (Hamburg), Docker, Node >= 22.
.PHONY: e2e e2e-up e2e-down e2e-logs

e2e-up: ## E2E-Stack starten (Projekt mitfahrer-e2e, HTTP auf 127.0.0.1:18080)
	@sh e2e/stack.sh up

e2e-down: ## E2E-Stack stoppen und seine Volumes entfernen (nur Projekt mitfahrer-e2e)
	@sh e2e/stack.sh down

e2e-logs: ## Logs des E2E-Stacks
	@sh e2e/stack.sh logs

e2e: e2e-up ## Playwright-E2E (startet/prüft den Stack, installiert Chromium; E2E_ARGS="-g Suchwort")
	cd e2e && if [ ! -d node_modules ]; then npm ci --no-audit --no-fund; fi
	cd e2e && npx playwright install chromium
	cd e2e && BASE_URL="$$(sh stack.sh url)" npx playwright test $(E2E_ARGS)
