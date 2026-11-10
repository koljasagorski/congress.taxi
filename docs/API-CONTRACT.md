# API-Vertrag (verbindlich für alle Phasen)

Dieses Dokument legt Pfade, JSON-Formen, Fehlercodes und Regeln fest, damit Backend-Phasen und
Frontend **parallel** entstehen können. Abweichungen nur über eine dokumentierte Änderungsanfrage
(siehe „Änderungen am Vertrag“). Konventionen: Koordinaten sind überall `(lat, lon)`; im JSON
`{"lat": …, "lon": …}`; Routenlinien als `[[lat, lon], …]`. Alle Zeiten ISO 8601 **mit Zeitzone**
(UTC im Backend). Sprache der Meldungen: Deutsch. Alle Pfade beginnen mit `/api`.

## Fehlerformat

```json
{"error": {"code": "ride_full", "message": "Die Fahrt ist voll", "details": [{"field": "…", "message": "…"}]}}
```

`details` nur bei `validation_error` (422). Nie Eingabewerte (E-Mail, Koordinaten) in Fehlern oder Logs
zurückspiegeln. Codes (HTTP): `bad_request` 400 · `unauthorized` 401 · `forbidden` 403 · `not_found` 404 ·
`conflict` 409 · `validation_error` 422 · `rate_limited` 429 (mit `Retry-After`) · `routing_unavailable` 503 ·
`internal_error` 500. Fachliche Codes siehe je Endpunkt.

**Objekt-Autorisierung:** Fremde oder nicht existierende Objekte liefern beide `404 not_found` (kein
Existenz-Orakel). `401` nur ohne gültige Anmeldung.

## Authentifizierung (Phase 2)

Cookie `access_token` (HttpOnly, SameSite=Strict, `Secure` per `COOKIE_SECURE`, Pfad `/`, JWT HS256).
Kein Token im Body. Hilfen (fertig, nicht ändern): `app.auth.tokens`, `app.auth.deps.CurrentUser`.

| Methode & Pfad | Body | Erfolg | Fehler |
|---|---|---|---|
| `POST /api/auth/register` | `{email, password, display_name, consent}` | **202** `{"message": "…"}` – **immer identisch**, egal ob die E-Mail schon existiert (kein Nutzer-Orakel); legt bei neuer E-Mail das Konto an, **kein** Auto-Login | 422 (`consent` muss `true` sein; Passwort 10–128 Zeichen; `display_name` 1–80; E-Mail gültig, ≤ 320) · 429 |
| `POST /api/auth/login` | `{email, password}` | 200 `{"user": {"id", "display_name"}}` + Set-Cookie | 401 `invalid_credentials` (identisch für unbekannte E-Mail und falsches Passwort; Antwortzeit angleichen: Dummy-Hash) · 429 |
| `POST /api/auth/logout` | – | 204, Cookie gelöscht (idempotent, ohne Anmeldung erlaubt) | – |
| `GET /api/me` | – | 200 `{id, display_name, email, created_at}` | 401 |
| `DELETE /api/me` | – | **204**, Konto + alle Fahrten/Anfragen (CASCADE) gelöscht, Cookie gelöscht | 401 |

E-Mail wird klein geschrieben und getrimmt gespeichert (zusätzlich nach `email-validator`-Normalisierung; Login normalisiert
gleich, ungültige Eingaben und NUL-Bytes ergeben dort 401, nie 422/500). Reservierte Domains (`@demo.invalid` …) sind nur
außerhalb von `APP_ENV=production` zulässig (Seed-Daten); in Produktion 422. `DELETE /api/me` gibt Plätze angenommener
Mitfahrten zurück (Sperr-Reihenfolge: Fahrt vor Anfrage, Gutschrift erst nach der Sperre). Passwort-Hash: argon2id (`argon2-cffi`).
Login/Register sind per `rate_limit(...)` (`app/ratelimit.py`) begrenzt: `rate_limit_login_per_min`,
`rate_limit_register_per_hour`. Bekannte Grenze: E-Mail-Adressen sind nicht verifiziert.

## Konfiguration (fertig)

`GET /api/config` → 200, ohne Anmeldung, keine Geheimnisse: `event{name,lat,lon,start_date,end_date}`,
`matching{prefilter_radius_m,max_detour_ratio,max_detour_minutes,detour_mode,max_candidates}`,
`map{tile_url,geocoder}`, `limits{min_seats:1,max_seats:8}`, `retention_days_after_event`.

## Fahrten (Phase 3) – alle Endpunkte erfordern Anmeldung

**RideOut** (Sicht der Fahrerin/des Fahrers auf die eigene Fahrt):
`{id, start{lat,lon}, end{lat,lon}, departure_time, seats_total, seats_available, status, distance_km, duration_min, route:[[lat,lon],…], created_at, pending_requests, accepted_requests}`
(`status` ∈ `open|full|cancelled`; `route` vereinfacht per `ST_Simplify` auf dem Geometry-Cast, Helfer in `app/services/geo.py`).

| Methode & Pfad | Body / Query | Erfolg | Fehler |
|---|---|---|---|
| `POST /api/rides` | `{start{lat,lon}, departure_time, seats}` | **201** RideOut | 422 `validation_error` (`departure_time` in der Zukunft, `seats` 1–8) · 422 `outside_coverage` (Start oder Event nicht nahe einer Straße im Extrakt, `snap_or_error`) · 422 `no_route` · 503 `routing_unavailable` |
| `GET /api/rides/mine` | `limit` (1–100, Standard 50), `offset` | 200 `Page[RideOut]` (`{items,total,limit,offset}`), neueste Abfahrt zuerst | 422 |
| `PATCH /api/rides/{id}` | optional `{departure_time, seats_total}` | 200 RideOut | 404 · 409 `has_accepted_requests` (Abfahrtszeit ändern, solange angenommene Anfragen existieren) · 409 `seats_below_accepted` (`seats_total` < angenommene) · 409 `ride_cancelled` · 422 |
| `DELETE /api/rides/{id}` | – | **204**; Fahrt → `cancelled`; alle `pending`/`accepted`-Anfragen → `cancelled` (`decided_at` gesetzt) | 404 |

`end_geom` = Eventziel zum Zeitpunkt der Anlage; `start_geom` = der **gelieferte** Punkt (nicht gesnappt);
die Route kommt serverseitig von OSRM (`RoutingClient`). Bei `PATCH seats_total`:
`seats_available = seats_total − angenommene`; Status `full`/`open` nachziehen. Die Abfahrtszeit-Änderung
ändert die Route nicht.

Umsetzung Phase 3 (Klarstellungen, keine Abweichung von den Pfaden/Formen):

* `PATCH`/`DELETE` auf fremde oder unbekannte Fahrten → 404 (kein Orakel). `DELETE` ist idempotent
  (bereits stornierte eigene Fahrt → wieder 204). `PATCH` mit unveränderter `departure_time` zählt nicht als
  Änderung (kein 409 `has_accepted_requests`). `null` in `PATCH`-Feldern und unbekannte Felder → 422.
* `POST /api/rides`: OSRM wird **vor** dem ersten Schreibzugriff aufgerufen (Fehler speichern nichts) und die
  DB-Verbindung währenddessen freigegeben. Wird das Konto in dieser Zeit gelöscht → 401 `unauthorized`.
  `departure_time` braucht eine Zeitzone und wird als UTC gespeichert; `seats` muss eine Ganzzahl sein.
* `distance_km` auf 0,1 km, `duration_min` auf 0,1 min gerundet. Ist die Linie zu kurz für `ST_Simplify`,
  wird die unvereinfachte Linie geliefert.
* Sperr-Reihenfolge wie vorgeschrieben: erst Fahrtzeile (`FOR UPDATE`), dann Anfragen.
* Noch offen (Phase 7): Rate-Limit **pro Nutzer** für `POST /api/rides`; bis dahin schützt nur das globale
  IP-Limit.

## Matching (Phase 4) – Anmeldung erforderlich

`GET /api/match?lat=&lon=&time_from=&time_to=&min_seats=1&limit=20` (`limit` 1–50, begrenzt nur die Ausgabe)

Ablauf wörtlich nach Auftrag: (1) Pickup per `snap_or_error` prüfen · (2) SQL-Vorfilter
`ST_DWithin(route_geom, pickup::geography, MATCH_PREFILTER_RADIUS_M)` + `status='open'`,
`seats_available >= min_seats`, `departure_time` im Fenster (Standard: ab jetzt; ohne `time_to` offen),
**eigene Fahrten ausgeschlossen**; Kandidaten nach Luftlinie sortiert, auf `MATCH_MAX_CANDIDATES` begrenzt ·
(3) pro Kandidat `compute_detour` (parallel, begrenzt, z. B. `asyncio.Semaphore(8)`) · (4) Filter `acceptable` ·
(5) Sortierung: kleinster Umweg, dann früheste Abfahrt.

200 `{items:[MatchOut], total}`; **MatchOut**:
`{ride_id, driver_display_name, departure_time, seats_available, detour_km, detour_minutes, detour_ratio, route:[[lat,lon],…]}`.
**Keine Kontaktdaten, keine Fahrer-ID, keine E-Mail.** Die Route wird für die Anzeige vereinfacht **und am Anfang um
`MATCH_ROUTE_TRIM_START_M` Meter gekürzt** (Datenschutz: Wohnadresse des Fahrers). Fehler: 422 `validation_error`,
422 `outside_coverage`, 503 `routing_unavailable`. Keine Treffer → `{"items": [], "total": 0}`.

**Gröbere Umweg-Werte (Standort-Orakel, Sicherheitsreview):** Die Antwort gibt den Umweg bewusst ungenau aus:
`detour_km` in **0,5-km-Schritten** (kaufmännisch gerundet, z. B. `1.5`), `detour_minutes` **ganzzahlig** (JSON-Integer),
`detour_ratio` auf **2 Nachkommastellen** (bei Basisdistanz 0 auf 999 gedeckelt). Filter (`acceptable`) und Sortierung
im Server rechnen weiter mit den exakten Werten; Einträge mit gleich angezeigtem Umweg können daher in anderer Reihenfolge
stehen, als die Rundung vermuten lässt. Hilfsfunktionen: `coarse_detour_km/minutes/ratio` in `app/services/matching.py`.
**Phase 5 (umgesetzt):** `RequestOut.detour_km`/`detour_minutes` sieht die anfragende Person ebenfalls zu einem selbst
gewählten Abholpunkt; dort gelten dieselben Hilfsfunktionen und damit dieselbe Granularität (`detour_km` in
0,5-km-Schritten, `detour_minutes` ganzzahlig, auch in Listen sowie accept/decline/cancel), gespeichert werden weiter
exakte Werte. `422 detour_too_large` ist dieselbe Ja/Nein-Information wie der Filter `acceptable` von `/api/match`.
**Frontend (Phase 6):** `detour_minutes` ist jetzt ganzzahlig, `detour_km` ein Vielfaches von 0,5 – Anzeige entsprechend
(„≈ 1,5 km“, „≈ 4 min“), keine Nachkommastellen für Minuten.

**Bekannte Grenze (Abtasten des Routenanfangs):** Die Anzeige-Route ist am Anfang gekürzt, Vorfilter und Umweg nutzen aber
die ungekürzte Route. Wer viele Abholpunkte abfragt, kann aus „Treffer ja/nein“ und den Umweg-Werten den Start der Route
grob (größenordnungsmäßig einige hundert Meter bis wenige km, je nach Rundung) eingrenzen. Gegenmaßnahmen: Anmeldepflicht,
gröbere Ausgabe (s. o.), Rate-Limit `RATE_LIMIT_MATCH_PER_MIN` (Phase 7) – Abtasten wird gebremst, nicht verhindert.
Details: Docstring `app/services/matching.py`, `PLAN.md` Annahme 18.

**Demo-Daten:** `make seed-demo` (`scripts/seed_demo.py`) legt über die laufende API Demo-Konten (`@demo.invalid`, nur
außerhalb von `APP_ENV=production` registrierbar) und Fahrten an; verweigert Produktion und Nicht-Loopback-Ziele.

## Anfragen (Phase 5) – Anmeldung erforderlich

**RequestOut**: `{id, ride_id, status, pickup{lat,lon}, detour_km (Vielfaches von 0,5), detour_minutes (Integer, ganze Minuten), created_at, decided_at, ride{departure_time, seats_available, driver_display_name}, rider{display_name}, counterpart_email}`
(`status` ∈ `pending|accepted|declined|cancelled`). **`counterpart_email` ist nur gesetzt, wenn `status == accepted` und die
anfragende Person Teil der Anfrage ist** (Mitfahrer sieht die E-Mail des Fahrers, Fahrer die des Mitfahrers); sonst `null`.
Nie in Match-Antworten, nie bei `pending`/`declined`/`cancelled`.

| Methode & Pfad | Body / Query | Erfolg | Fehler |
|---|---|---|---|
| `POST /api/rides/{id}/requests` | `{pickup{lat,lon}}` | **201** RequestOut (`pending`); Umweg wird **serverseitig neu berechnet** (`compute_detour`), Clientwerte gibt es nicht | 404 (Fahrt unbekannt/`cancelled`) · 403 `own_ride` · 409 `ride_full` · 409 `ride_departed` · 409 `already_requested` (aktive Anfrage existiert) · 422 `detour_too_large` · 422 `outside_coverage` · 503 |
| `GET /api/requests/incoming` | `status`, `limit`, `offset` | 200 `Page[RequestOut]` – Anfragen an meine Fahrten | 422 |
| `GET /api/requests/outgoing` | `status`, `limit`, `offset` | 200 `Page[RequestOut]` – meine Anfragen | 422 |
| `POST /api/requests/{id}/accept` | – | 200 RequestOut (`accepted`); **atomar**: Zeile der Fahrt `SELECT … FOR UPDATE`, dann Anfrage; `seats_available − 1`; bei 0 → Fahrt `full` | 404 (nicht meine Fahrt) · 409 `invalid_state` · 409 `ride_full` · 409 `ride_cancelled` |
| `POST /api/requests/{id}/decline` | – | 200 RequestOut (`declined`) – nur Fahrer, nur `pending` | 404 · 409 `invalid_state` |
| `POST /api/requests/{id}/cancel` | – | 200 RequestOut (`cancelled`) – Mitfahrer: `pending` oder `accepted`; Fahrer: nur `accepted`; bei `accepted` Platz zurück (`+1`, `full` → `open`) | 404 · 409 `invalid_state` |

Sperr-Reihenfolge immer: **Fahrt (`rides`) vor Anfrage (`ride_requests`)**, um Deadlocks zu vermeiden.
**Konto löschen (`DELETE /api/me`):** Es gibt genau **eine** Implementierung der Platzrückgabe,
`release_seats_of_rider` in `app/services/requests.py` (von `app/api/me.py` in derselben Transaktion vor dem Löschen
aufgerufen). Sie sperrt zuerst alle Fahrten des Nutzers (als Fahrer oder mit irgendeiner Anfrage, id-sortiert in einer
Anweisung), setzt *danach* seine `accepted`-Anfragen auf `cancelled` (`RETURNING`) und schreibt je Fahrt einmal gut
(`LEAST(seats_total, …)`, `full` → `open`, abgesagte Fahrten unverändert). Dadurch weder Doppel- noch Fehlgutschrift
gegen parallele accept/cancel; das anschließende CASCADE-Löschen entfernt alle Anfragen/Fahrten des Kontos.
`decided_at` wird bei accept/decline/cancel gesetzt. Dieselbe Transition erneut → 409 `invalid_state`.

## Gesundheit (fertig)

`GET /api/health` (Liveness), `GET /api/health/ready` (DB + OSRM).

## Sicherheit und Betrieb (Phase 7)

* Security-Header an der API (`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store` für `/api`) und am Caddy (CSP, HSTS, `X-Content-Type-Options`, Frame-/Permissions-Policy).
  CSP: Tile-Host aus `TILE_URL`, sonst nur `'self'`.
* Origin-Prüfung für `POST/PUT/PATCH/DELETE`: trägt die Anfrage einen `Origin`-Header, muss er in `ALLOWED_ORIGINS`
  (bzw. aus `DOMAIN` abgeleitet, `Settings.allowed_origin_list`) liegen, sonst 403 `forbidden`. Ohne `Origin` kein Fehler
  (Skripte, Tests) – die Cookies sind SameSite=Strict.
* Globales Rate-Limit je Client-IP (`RATE_LIMIT_API_PER_MIN`), strenger für `/api/match` (`RATE_LIMIT_MATCH_PER_MIN`);
  Login/Register wie oben. Zähler in-memory, ein Worker (bekannte Grenze).
* Retention-Cleanup: `python -m app.cleanup [--dry-run] [--now ISO]`; löscht **alles** (Anfragen, Fahrten, Konten), sobald
  `jetzt > EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT` (Tagesende UTC); idempotent; Ausgabe nur Zählwerte.
* Backups: `pg_dump` → `age -r $BACKUP_AGE_RECIPIENT`; Aufbewahrung `BACKUP_KEEP_DAYS` **kleiner** als die Retention.
* Logs: kein Query-String, keine IP, keine E-Mail, keine Koordinaten.

## Frontend (Phase 6)

Reine SPA (React + Vite + TypeScript, Leaflet), spricht nur mit `/api` (gleiche Origin) und dem Tile-Server aus
`/api/config`. Keine externen Fonts/CDNs/Tracker; Leaflet-Icons lokal. Seiten: Start (Erklärung + Datenschutzhinweis),
Registrierung/Login, Fahrt anbieten, Mitfahrt suchen, Meine Fahrten & Anfragen, Datenschutz, Impressum (Platzhalter),
Konto löschen. Deutsch Standard, i18n-Struktur für Englisch. Dev-Proxy: Vite leitet `/api` an `http://127.0.0.1:8000`.
Build-Ausgabe `web/dist`; Container `web` (nginx, Port 8080, SPA-Fallback); Caddy leitet `/api/*` an `api:8000`, sonst an `web:8080`.

## Dateibesitz (Merge-Konflikte vermeiden)

Jede Phase bearbeitet **nur** ihre Dateien. **Eingefroren** (nur durch Änderungsanfrage): `app/main.py`, `app/models.py`,
`app/config.py`, `app/errors.py`, `app/db.py`, `alembic/versions/*`, `pyproject.toml`/`uv.lock`, `tests/conftest.py`,
`tests/fakes.py`, `tests/helpers.py`, `tests/test_contract_shared.py`, `app/auth/tokens.py`, `app/auth/deps.py`,
`app/api/config.py`, `app/services/geo.py`, `docs/API-CONTRACT.md`.

| Phase | Eigene Dateien |
|---|---|
| 2 | `app/api/auth.py`, `app/api/me.py`, `app/auth/passwords.py`, `app/schemas/auth.py`, `tests/test_auth_*.py`, `tests/test_me_*.py` |
| 3 | `app/api/rides.py`, `app/schemas/rides.py`, `app/services/rides.py`, `tests/test_rides_*.py` |
| 4 | `app/api/match.py`, `app/schemas/match.py`, `app/services/matching.py`, `app/services/detour.py` (Interna/Härtung, **Signatur bleibt**), `scripts/seed_demo.py` + Make-Target `seed-demo`, `tests/test_match_*.py`, `tests/test_detour*.py` |
| 5 | `app/api/requests.py`, `app/schemas/requests.py`, `app/services/requests.py`, `tests/test_requests_*.py` |
| 6 | `web/**`; `.github/workflows/ci.yml` (nur den eigenen Job `web` anhängen); `docker-compose.yml` (nur Service `web` anhängen) |
| 7 | `app/cleanup.py`, `app/middleware/security.py`, `app/ratelimit.py`, `scripts/backup.sh`, `scripts/restore.sh`, `caddy/**`, `tests/test_cleanup*.py`, `tests/test_security_*.py`, `tests/test_ratelimit*.py`; `docker-compose.yml` (nur Services `caddy`, `cleanup`, ggf. `backup` anhängen); `Makefile` (nur neue Targets anhängen) |
| 8 (später) | `e2e/**`, `README.md`, `docs/DATENSCHUTZ.md`, `PLAN.md`, CI-Job `e2e` |

Das Schema ist vollständig – **keine** neuen Migrationen. Falls zwingend nötig: Änderungsanfrage.

## Änderungen am Vertrag

Wer den Vertrag nicht einhalten kann, ändert **keine** eingefrorene Datei, sondern meldet im Abschlussbericht unter
`vertrags_aenderungen` (Datei, Grund, konkreter Vorschlag). Die Integration entscheidet.
