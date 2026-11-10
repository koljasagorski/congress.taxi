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
make up               # db + osrm + api starten, wartet auf Healthchecks
curl localhost:8000/api/health/ready
make test             # Tests inkl. OSRM-Live-Tests
make lint             # ruff + mypy
```

Produktion: `make env` (ohne `DEV=1`), `OSRM_PBF_URL` bleibt `germany-latest` – vorher die
Ressourcenhinweise in `PLAN.md` lesen.

## Stand Phase 1

- PostgreSQL 16 + PostGIS, Alembic-Migration mit dem vollständigen Schema
- OSRM (Profil `car`, MLD) mit reproduzierbarer Datenvorbereitung (`make osrm-prepare`)
- API-Skelett (FastAPI): `/api/health`, `/api/health/ready`, einheitliches Fehlerformat,
  Request-Log ohne personenbezogene Daten
- `RoutingClient`-Interface mit OSRM-Implementierung
- CI (GitHub Actions): Lint, mypy, Tests; Stack-Lauf auf frischem Ubuntu
