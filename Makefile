# Mitfahrer-Matching 40C3 – Make-Targets
# Kompatibel mit GNU Make 3.81 (macOS) und 4.x (Linux).

SHELL := /bin/sh
COMPOSE := docker compose

.PHONY: help env up down restart logs ps build migrate db-up test lint fmt osrm-prepare seed-demo clean-data

help: ## Diese Hilfe
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env: ## .env mit Zufalls-Secrets erzeugen (make env DEV=1: Hamburg-Extrakt, Development-Modus)
	@sh scripts/init-env.sh $(if $(DEV),dev,prod)

up: ## Gesamten Stack starten (baut Images, wartet auf Healthchecks)
	$(COMPOSE) up -d --build --wait

down: ## Stack stoppen (Daten bleiben)
	$(COMPOSE) down

restart: ## Stack neu starten
	$(COMPOSE) restart

logs: ## Logs verfolgen
	$(COMPOSE) logs -f --tail=100

ps: ## Containerstatus
	$(COMPOSE) ps

build: ## Images bauen
	$(COMPOSE) build

db-up: ## Nur die Datenbank starten und auf Healthcheck warten
	$(COMPOSE) up -d --wait db

migrate: ## Alembic-Migrationen im api-Container ausführen
	$(COMPOSE) run --rm --no-deps api alembic upgrade head

test: db-up ## Tests (startet die DB; OSRM-Live-Tests laufen, wenn OSRM erreichbar ist; liest .env)
	cd api && set -a && . ../.env && set +a && POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=$$DB_PORT \
		OSRM_URL=http://127.0.0.1:$$OSRM_PORT uv run pytest

lint: ## ruff + mypy
	cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app

fmt: ## ruff format + Autofixes
	cd api && uv run ruff check --fix . && uv run ruff format .

osrm-prepare: ## OSRM-Daten herunterladen und vorbereiten (FORCE=1 erzwingt Neuaufbau)
	@sh scripts/osrm-prepare.sh

seed-demo: ## Demo-Fahrer/-Mitfahrer anlegen (ab Phase 4)
	@echo "seed-demo ist noch nicht implementiert (Phase 4)." >&2; exit 1

clean-data: ## ACHTUNG: löscht Datenbank-Volume (nicht die OSRM-Daten)
	$(COMPOSE) down -v
