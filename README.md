# Mitfahrer-Matching für den 40C3

Selbst gehostete, rein Open-Source-basierte Webanwendung: Fahrer legen eine Fahrt zum Kongress
an, Mitfahrer finden passende Fahrer anhand der **realen Umwegzeit** (OSRM), nicht nur der Luftlinie.

> **Status: in Entwicklung (Phase 1 von 8).** Die vollständige Dokumentation (Architektur,
> Deployment, Datenschutzkonzept, Matching-Algorithmus, bekannte Grenzen) entsteht in Phase 8.
> Entscheidungen, Annahmen und offene Punkte stehen jetzt schon in [`PLAN.md`](PLAN.md).

## Zielplattform

Eine **Proxmox-VM** (empfohlen; Debian 12/13 oder Ubuntu 24.04, amd64) mit Docker Engine und
Compose v2. Ein LXC-Container ist möglich, braucht aber `nesting=1`. Details und Ressourcen
(`germany-latest` braucht viel RAM!) in [`PLAN.md`](PLAN.md).

## Schnellstart (Entwicklung, Hamburg-Extrakt)

Voraussetzungen: Docker + Compose v2, `make`, `curl`, [`uv`](https://docs.astral.sh/uv/).

```sh
make env DEV=1        # .env mit Zufalls-Secrets erzeugen (Hamburg-Extrakt, Dev-Modus)
make osrm-prepare     # lädt hamburg-latest (54 MB) und bereitet OSRM vor (~30 s)
make up               # db + osrm + api + web + cleanup starten, wartet auf Healthchecks
curl localhost:8000/api/health/ready
make test             # Tests inkl. OSRM-Live-Tests
make lint             # ruff + mypy
```

### Oberfläche im Browser: `make up-proxy` statt `make up`

`make up` (und damit auch der Pfad `cp .env.example .env && make osrm-prepare && make up`) startet **keinen
Reverse Proxy**. Das ist Absicht (Compose-Profil `proxy`, `PLAN.md` Annahme 9): Auf dem Zielserver sitzt ein
Rand-Proxy davor, ein eigener Caddy würde um die Ports 80/443 streiten. Das Ergebnis dieses Standardpfads:

| Was | Erreichbar | Hinweis |
| --- | --- | --- |
| API | `http://127.0.0.1:8000` (`/api/health`, `/api/health/ready`) | direkt, für Tests und `curl` |
| SPA | `http://127.0.0.1:8080` | **ohne API**: nginx beantwortet `/api/*` mit 404, die Seite zeigt Fehler |
| Caddy | läuft nicht | |

Die **vollständige Anwendung unter einer Origin** (SPA, `/api` und TLS über Caddy) startet:

```sh
make up-proxy         # = COMPOSE_PROFILES=proxy COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml docker compose up …
# Browser: https://localhost  (DOMAIN in .env; lokal internes Zertifikat, Browser-Warnung bestätigen)
make down-proxy       # stoppt den Stack inklusive Caddy
```

Dabei veröffentlicht nur Caddy Ports (80/443, per `HTTP_PORT`/`HTTPS_PORT` änderbar); die API ist nicht mehr auf
`:8000` erreichbar, und `/api/health/ready` ist von außen gesperrt (404; nur `/api/health` kommt durch). Ohne `make` entspricht das den beiden Zeilen `COMPOSE_PROFILES=proxy` und
`COMPOSE_FILE=…` in `.env.example`. Hinter einem vorhandenen Rand-Proxy (z. B. `deploy/hel/`) beides **nicht** setzen.

Produktion: `make env` (ohne `DEV=1`), `OSRM_PBF_URL` bleibt `germany-latest` – vorher die
Ressourcenhinweise in `PLAN.md` lesen.

## Eventdaten, Löschfrist und Betrieb nach der Frist

`EVENT_START_DATE`, `EVENT_END_DATE` und `RETENTION_DAYS_AFTER_EVENT` (in `.env`) bestimmen, wann der
Cleanup-Dienst **alle** Konten, Fahrten und Anfragen löscht: am Ende des Tages
`EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT` (UTC). Der Dienst läuft immer mit (beim Start, dann alle 6 h).

- **Plausibilisierung.** `EVENT_END_DATE` vor `EVENT_START_DATE` lehnt die Konfiguration ab (API und Cleanup starten
  nicht, `scripts/backup.sh` bricht ab, ohne Backups zu löschen).
- **Schutz vor falschem Datum.** Findet der Cleanup bei abgelaufener Frist Datensätze, die mehr als 1 Tag *nach*
  Fristablauf angelegt wurden (typisch: Tippfehler wie 2025 statt 2026, Frist nachträglich vorverlegt), löscht er
  **nichts**, meldet es auf stderr und beendet sich mit Exit-Code 3 (der Container wird `unhealthy`).
  Datum in `.env` korrigieren; wirklich löschen mit `CLEANUP_CONFIRM_EXPIRED=1` (oder einmalig
  `make cleanup CLEANUP_ARGS=--confirm-expired`). Vorher `make cleanup DRY=1`.
- **Nach Fristablauf den Stack stoppen** (`make down` bzw. `make down-proxy`; auf hel `pct stop 220`). Die API bleibt
  bis dahin vollständig offen (Registrierung, Fahrten): Konten, die erst nach der Frist entstehen, löscht der
  nächste Cleanup-Lauf (bis zu 6 h später) ohne Rückfrage – oder verweigert ihn, siehe oben.

## Stand Phase 1

- PostgreSQL 16 + PostGIS, Alembic-Migration mit dem vollständigen Schema
- OSRM (Profil `car`, MLD) mit reproduzierbarer Datenvorbereitung (`make osrm-prepare`)
- API-Skelett (FastAPI): `/api/health`, `/api/health/ready`, einheitliches Fehlerformat,
  Request-Log ohne personenbezogene Daten
- `RoutingClient`-Interface mit OSRM-Implementierung
- CI (GitHub Actions): Lint, mypy, Tests; Stack-Lauf auf frischem Ubuntu
