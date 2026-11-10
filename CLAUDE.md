# Mitfahrer-Matching 40C3 – Übergabe für Coding-Agenten

Selbst gehostete Mitfahrer-Matching-App (FastAPI + PostGIS + OSRM, React/Vite/Leaflet-Frontend in `web/`, Caddy).
Zielplattform: **Proxmox** (Docker Compose). Teilstand läuft seit 2026-11-10 in LXC 220 auf `hel` (https://congress.sgr.ski), Betrieb/Rückbau/Offenes: `deploy/hel/README.md`. Entscheidungen/Annahmen: `PLAN.md` (maßgeblich).

## Arbeitsstand

Siehe Phasen-Checkliste in `PLAN.md`. Jede Phase = ein Conventional-Commit, grüne Tests, sofort pushen.

## Befehle

- `make env DEV=1` · `make osrm-prepare` · `make up` · `make test` · `make lint` · `make web-check` (Frontend: tsc, eslint, vitest, Build, check:external; alles in `Makefile`)
- Tests brauchen laufende DB (`make test` startet sie) und lesen `.env`.

## Fallstricke (bereits erlebt)

- **Auf dem Host läuft evtl. ein fremder Postgres auf 5432.** Deshalb DB-Port 55432 (`DB_PORT`),
  OSRM-Port 55000 (`OSRM_PORT`; 5000 belegt macOS AirPlay). Die Test-Fixtures verweigern Anlegen/
  Löschen von Datenbanken auf Servern ohne die Basis-Datenbank `POSTGRES_DB`.
- `postgis/postgis` hat **kein arm64-Image**; `make env` setzt auf arm64 automatisch
  `imresamu/postgis:16-3.5-bookworm`. Produktion (amd64) nutzt das offizielle Image.
- OSRM-Image `v26.4.0` ist das neueste veröffentlichte (Release v26.11.0 hat noch keins).
- **OSRM snappt Punkte außerhalb des Extrakts still auf ferne Straßen** (München → 592 km Snap im
  Hamburg-Extrakt). Jede Koordinate muss gegen `MATCH_MAX_SNAP_DISTANCE_M` geprüft werden.
- GNU Make 3.81 (macOS): keine Make-4-Features. Kein `sed -i` in Skripten (macOS/Linux verschieden).
- **Nie `git add -A` blind:** Am 2026-11-10 landete eine macOS-Kopie `.env 2` (Dev-Secrets) in `f804b7f` und damit im
  öffentlichen Repo. Entfernt, `.gitignore` (`.env *`) und Test `test_repo_hygiene.py` ergänzt, Dev-Secrets rotiert;
  die Historie bleibt (kein Force-Push) – alte Werte gelten als verbrannt. Produktiv-Secrets entstehen auf dem Gast
  und waren nie betroffen. Vor jedem Commit `git status` lesen.
- **Frontend-Dev-Server** (`npm run dev`, Port 5173): schreibende Aufrufe tragen die Origin `http://localhost:5173`; ohne `ALLOWED_ORIGINS=http://localhost:5173` in `.env` antwortet die API 403.
- Der öffentliche OSM-Tile-Server ist nur für Entwicklung; `TILE_URL` **und** `TILE_ATTRIBUTION` für den Betrieb setzen (CSP folgt `TILE_URL`).
- Caddy: Referrer-Policy je `handle`-Zweig (SPA `strict-origin-when-cross-origin`, `/api` `no-referrer`); ein Site-weites `header` mit `-Server` läuft zurückgestellt und würde `/api` überstimmen.
- Logs dürfen keine Koordinaten/IPs enthalten (Uvicorn `--no-access-log`, eigenes Request-Log ohne Query).

## Server (hel) – Kurzfassung

- Nur CI-geprüfte Commits ausrollen (`deploy/hel/README.md`, „Anlegen“); SSH in **wenige gebündelte Sitzungen**;
  keine Schlüssel, `~/.ssh/config`- oder `.env`-Inhalte ins Repo/in Protokolle. Infos zum Wirt: Repo `patchletter.com`.
- Der Rand-Caddy (CT 180) reicht im Teilstand nur `/api/health` durch – das Frontend (Phase 6) ist **nicht** ausgerollt; Block öffnen und `web` freischalten siehe `deploy/hel/README.md`.
- **Vor Phase 2 live:** PBS-Sicherung von CT 220 entscheiden (siehe README, „Offene Entscheidungen“).

## Datenschutz-Leitplanken

Keine Klartext-Adressen, Kennzeichen, Telefonnummern speichern. E-Mail erst nach beidseitiger
Bestätigung freigeben. Keine externen Fonts/CDNs. Nichts Geheimes ins Repo (`.env` ist ignoriert).

## Linear

Nicht verknüpft: Das Linear-MCP war in der Entwicklungsumgebung nicht authentifiziert und es gibt
kein Projekt für dieses Repo. TODO für den Betreiber: Projekt-URL hier eintragen.
