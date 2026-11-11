# Mitfahrer-Matching 40C3 – Übergabe für Coding-Agenten

Selbst gehostete Mitfahrer-Matching-App (FastAPI + PostGIS + OSRM, React/Vite/Leaflet-Frontend in `web/`, Caddy).
Zielplattform: **Proxmox** (Docker Compose). Der Gesamtstand läuft seit 2026-11-11 in LXC 220 auf `hel` (https://congress.sgr.ski, Commit `edd0c2d`), Betrieb/Rückbau/Offenes: `deploy/hel/README.md`. Entscheidungen/Annahmen: `PLAN.md` (maßgeblich).

## Arbeitsstand

Siehe Phasen-Checkliste in `PLAN.md`. Jede Phase = ein Conventional-Commit, grüne Tests, sofort pushen.

**Stand 2026-11-11:** Phasen 1–8 sind fertig und in einem frischen Klon abgenommen (`PLAN.md`, „Phase 8, Teil Abnahme“; dort auch, was **nicht** geprüft wurde).
Nicht behobene niedrige Befunde und alle Betreiberentscheidungen stehen vollständig in `README.md` („Bekannte Grenzen und offene Punkte“) und `PLAN.md`
(„Offene Fragen für den Betreiber“) – dort weiterarbeiten, nicht hier duplizieren. Wichtigste offene Punkte: Platzhalter (Ort/Datum, Impressum, Datenschutztexte in
`web/src/i18n/de.ts`), Tile-Server, SMTP/E-Mail-Verifikation, PBS/Backup auf hel, Oberfläche mit `docs/DATENSCHUTZ.md` angleichen.
**Adresssuche (2026-11-11, auf hel seit Commit `eb777c1` eingeschaltet):** `GET /api/geocode` (serverseitiger Nominatim-Proxy, `GEOCODER=nominatim`,
Standard `none`), Suchfeld im `PointPicker`; Doku in `README.md` („Adresssuche“), `docs/API-CONTRACT.md`, `docs/DATENSCHUTZ.md` (V3a). Der Browser fragt nie einen Geocoder direkt;
der Cache ist je Nutzer getrennt (sonst Zeitkanal). Abschalten: `GEOCODER=none sh deploy/hel/ausrollen.sh` (siehe `deploy/hel/README.md`).
Auf hel ist seit 2026-11-11 Commit `edd0c2d` ausgerollt (inkl. Oberfläche und aller Audit-Fixes); danach gepushte Commits sind es nur, wenn `deploy/hel/README.md` es nennt.

## Befehle

- `make env DEV=1` · `make osrm-prepare` · `make up` · `make test` · `make lint` · `make web-check` (Frontend: tsc, eslint, vitest, Build, check:external; alles in `Makefile`)
- Tests brauchen laufende DB (`make test` startet sie) und lesen `.env`. `REQUIRE_OPS_TESTS=1` macht Docker-/Betriebstests zur Pflicht (CI-Job `ops`).
- Dokumentation: `README.md`, `docs/DATENSCHUTZ.md` (Entwurf, kein Rechtsrat), `docs/API-CONTRACT.md` (eingefroren, nur über Änderungsanfrage). Einige Tests lesen Doku-Dateien
  (`test_privacy_claims.py` verlangt in `PLAN.md`, `docs/API-CONTRACT.md`, `matching.py` „Median … 200 m“ und „900 m“ und verbietet „bis wenige km“) – nach Doku-Änderungen `make test` laufen lassen.
- `make e2e` (Playwright, `e2e/`): startet ein eigenes Compose-Projekt `mitfahrer-e2e` (Port 18080, angehobene Rate-Limits nur dort,
  Kachel-Host wird im Test abgefangen), braucht `make osrm-prepare` (Hamburg) und Docker; `make e2e-down` räumt auf. Details: `e2e/README.md`.

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
- **Docker unter macOS sieht nicht jeden Pfad** (Colima spiegelt nur `$HOME` in die VM; Docker Desktop nur die freigegebenen Pfade): Ein Bind-Mount aus dem Scratchpad (`/private/tmp/claude-501/…`) oder `$TMPDIR` ist im Container **leer**
  (`osrm-extract: Input file /data/map.osm.pbf not found`, obwohl die Datei auf dem Host liegt). Compose-Läufe mit Bind-Mounts (z. B. `make osrm-prepare`) im Repo oder in einer
  Kopie unter `~/.cache/…` ausführen, nicht im Scratchpad.
- `env_file: .env` in `docker-compose.yml` ist Pflicht: Ohne `.env` bricht `docker compose` ab (auch `config`).
- Zweiter Stack neben dem Dev-Stack: eigenes `COMPOSE_PROJECT_NAME` und andere Hostports (`DB_PORT`, `OSRM_PORT`, `API_PORT`, `WEB_PORT`; bei Caddy `HTTP_PORT`/`HTTPS_PORT`).
  `make seed-demo` dauert ≈ 1 min (Login-Limit 10/min) und braucht die API auf dem Host (nicht unter `make up-proxy`).
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
- Der Rand-Caddy (CT 180) reicht `/api/*` an die API und den Rest an den Web-Container; `/api/health/ready` ist extern zu. Noch offen auf hel: Kartenserver (`TILE_URL`/`TILE_ATTRIBUTION`) und Impressum/Datenschutz-Platzhalter, siehe `deploy/hel/README.md`.
- **Vor dem Live-Gang:** PBS-Sicherung von CT 220 entscheiden (siehe `deploy/hel/README.md`, „Offene Entscheidungen“); auf hel gibt es kein App-Backup (kein `age`, kein Cron).

## Datenschutz-Leitplanken

Keine Klartext-Adressen, Kennzeichen, Telefonnummern speichern. E-Mail erst nach beidseitiger
Bestätigung freigeben. Keine externen Fonts/CDNs. Nichts Geheimes ins Repo (`.env` ist ignoriert).

## Linear

Nicht verknüpft: Das Linear-MCP war in der Entwicklungsumgebung nicht authentifiziert und es gibt
kein Projekt für dieses Repo. TODO für den Betreiber: Projekt-URL hier eintragen.
