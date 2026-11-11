# PLAN – Mitfahrer-Matching für den 40C3

Stand: 2026-11-10 · Status: **Phasen 1–7 abgeschlossen (Phase 8 offen)** · Teilstand läuft auf Proxmox (hel, CT 220) unter https://congress.sgr.ski (nur `/api/health`)

Dieses Dokument hält Phasen, Annahmen, Entscheidungen und offene Punkte fest.
Alles hier ist bewusst knapp; Details stehen im README (ab Phase 8).

## Zielplattform: Proxmox

Alles läuft auf **einer Proxmox-VM** (empfohlen) oder einem Proxmox-LXC-Container.

| | VM (Standard) | LXC (dokumentiert, nicht empfohlen) |
|---|---|---|
| OS | Debian 12/13 oder Ubuntu 24.04 LTS, amd64 | dito |
| Docker | Docker Engine + Compose v2 (offizielles Repo) | Docker im LXC braucht `nesting=1` (+ `keyctl=1`), Cgroup-/AppArmor-Eigenheiten |
| Isolation | eigener Kernel, kein Sonderwissen nötig | geteilter Host-Kernel |
| Backup | Proxmox-Snapshot/vzdump **zusätzlich** zum `pg_dump`-Skript | dito |

- CPU-Typ der VM auf `host` stellen (OSRM/PostGIS profitieren von AVX2).
- Reverse Proxy: **Caddy** (eigenes TLS) *oder* – bei Coolify/vorgeschaltetem Proxy – Caddy per
  Compose-Profil abgeschaltet (Phase 7), damit Port 80/443 nicht kollidieren.
- Es gibt keine Abhängigkeit vom Docker-Socket; alle Secrets liegen in `.env`.

### Ressourcen (Stand der Recherche, **nicht lokal verifiziert**)

| Größe | Wert | Quelle/Status |
|---|---|---|
| `germany-latest.osm.pbf` | 4,9 GB | gemessen (Geofabrik, 2026-11-10) |
| RAM `osrm-extract` für Deutschland | ≈ 20 GB Peak | **extrapoliert**: Europa (28 GB PBF) ≈ 118 GB Peak laut OSRM-Issue #6146, linear auf 4,9 GB skaliert. Ungeprüft. |
| RAM `osrm-routed` (Betrieb, MLD) | ≈ 7 GB | **extrapoliert** (Europa ≈ 39 GB). Ein Blog nennt 3–5 GB. Ungeprüft. |
| Disk | ≥ 40 GB frei | grobe Schätzung (PBF 5 + Graph 10–15 + DB + Images) |

Konsequenz: Die Vorbereitung (`make osrm-prepare`) für Deutschland entweder auf einer
temporär größeren VM ausführen und `data/osrm/` übernehmen, oder ein kleineres Extrakt
wählen (z. B. nur die für die Anreise relevanten Bundesländer). Die Betriebs-VM braucht
dann ≥ 8 GB RAM. **Lokal getestet wird ausschließlich das Hamburg-Extrakt (54 MB).**

## Annahmen und Entscheidungen

1. **Images.** PostGIS: `postgis/postgis:16-3.5` (nur amd64 – passt zur Proxmox-VM).
   Auf arm64-Entwicklungsrechnern per `POSTGIS_IMAGE=imresamu/postgis:16-3.5-bookworm`
   (Multi-Arch, geprüft). OSRM: `ghcr.io/project-osrm/osrm-backend:v26.4.0` – das ist das
   neueste *tatsächlich veröffentlichte* Image (Release v26.11.0 hat noch keines; geprüft).
2. **Dev-Extrakt = Hamburg, nicht Bremen.** Das Standardziel (CCH Hamburg) liegt außerhalb
   von `bremen-latest`; OSRM würde den Zielpunkt still auf eine weit entfernte Straße
   snappen. Hamburg (54 MB) deckt das Ziel ab und hat mit der Elbe eine echte
   „kleine Luftlinie, großer Umweg“-Situation für die Matching-Tests.
3. **Snap-Prüfung.** Punkte (Start, Pickup, Ziel), deren nächste Straße weiter als
   `MATCH_MAX_SNAP_DISTANCE_M` (Standard 500 m) entfernt ist, werden abgelehnt. `/api/health/ready`
   meldet die Snap-Distanz des Eventziels und warnt, wenn das Ziel nicht im Extrakt liegt.
4. **Umweg-Schwelle.** „max. 20 % Mehrdistanz *oder* max. 25 Minuten Mehrfahrzeit“ wird
   wörtlich gelesen: **eines von beiden genügt** (`MATCH_DETOUR_MODE=any`). Umschaltbar auf
   `all` (beide müssen erfüllt sein). Werte per Env.
5. **Event-Datum.** Die Spezifikation nennt keines. Neu: `EVENT_START_DATE`, `EVENT_END_DATE`.
   Retention löscht, sobald `heute > EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT`.
   Die Platzhalterwerte (27.–30.12.2026) und die Hamburg-Koordinaten sind **TODO/unbestätigt**.
6. **Nutzerenumeration.** Ohne E-Mail-Versand lässt sich „E-Mail schon vergeben“ nicht
   vollständig verbergen. Entscheidung (Phase 2): `POST /api/auth/register` antwortet
   immer identisch (202) und loggt **nicht** ein; bei bereits vergebener E-Mail wird nichts
   angelegt. Login-Fehler sind generisch, Timing wird per Dummy-Hash angeglichen.
   **Bekannte Grenze (E-Mail-Squatting/Pre-Hijacking):** E-Mail-Adressen werden nicht
   verifiziert und es gibt keinen Mailversand. Wer eine fremde Adresse zuerst registriert,
   „besetzt“ sie (die echte Person kann sich dann nicht mit dieser Adresse registrieren) und
   kennt das Passwort. Ein Gegenüber kann nach beidseitiger Bestätigung also eine Adresse
   erhalten, die nicht der Person gehört, mit der es sich verabredet hat.
   Wer sich vertippt, merkt es erst beim Login. Bewusst hingenommen (kurzlebige Event-App, kein
   Mailserver, Datensparsamkeit); Gegenmaßnahmen sind Rate-Limit (Phase 2/7) und die
   Kontaktfreigabe erst nach beidseitiger Bestätigung. README und Datenschutzdokumentation
   nennen die Grenze in Phase 8.
   **E-Mail-Syntax je Umgebung:** `email-validator` lehnt reservierte Domains (`.invalid`,
   `.local`, `.test` …) ab. In `APP_ENV=production` bleibt das so (strikt). In `development`
   und `test` sind sie erlaubt (u. a. für `seed_demo` in Phase 4 mit `@demo.invalid`); die
   Ausnahme gilt nur für ASCII-Domains mit reserviertem Suffix, der Local-Part wird weiter
   streng geprüft. Es gibt keine DNS-Zustellbarkeitsprüfung. Umsetzung: `app/schemas/auth.py`
   (Version 2.3 der Bibliothek kennt keinen Schalter dafür).
   **Konto löschen und Plätze:** `DELETE /api/me` sperrt zuerst die Fahrten (id-sortiert), löscht
   danach die Anfragen mit `DELETE … RETURNING` und schreibt nur tatsächlich gelöschte
   `accepted`-Anfragen gut. **Seit Phase 5 gibt es genau eine Implementierung:**
   `release_seats_of_rider` in `app/services/requests.py` (sperrt Fahrten des Nutzers als Fahrer
   *und* mit Anfragen id-sortiert, setzt danach `accepted` → `cancelled`, schreibt je Fahrt einmal
   gut); `app/api/me.py` ruft sie in derselben Transaktion auf. Tests: `test_requests_account.py`,
   `test_me_account.py` (Konto-Löschen vs. accept/cancel, mehrere Wiederholungen).
7. **Backups enthalten personenbezogene Daten.** Rotation (`BACKUP_KEEP_DAYS`, Standard 7)
   muss kürzer sein als die Retention-Frist; nach einem Restore muss der Cleanup-Job erneut
   laufen (dokumentiert, Phase 7).
8. **Logs ohne Personenbezug.** `/api/match?lat=&lon=` trägt Koordinaten im Query-String.
   Uvicorn-Access-Log ist aus (`--no-access-log`); eigenes Request-Log schreibt nur
   Methode, Routen-Template, Status, Dauer – keine IP, kein Query. Caddy-Access-Log wird
   in Phase 7 entsprechend abgeschaltet/gefiltert.
9. **Rate-Limit (Phase 7).** In-Memory, daher **ein** Uvicorn-Worker (dokumentierte Grenze). Echte
   Client-IP nur aus `X-Forwarded-For`, wenn der direkte Peer in `TRUSTED_PROXIES` steht – und dort steht
   **nur die feste Adresse des Proxys (/32)**, nie ein Docker-/LAN-Netz: Mit `COMPOSE_PROFILES=proxy` +
   `COMPOSE_FILE=docker-compose.yml:docker-compose.proxy.yml` hat Caddy im Netz `proxy` (festes Subnetz,
   `CADDY_IPV4`) eine feste Adresse, die API veröffentlicht keinen Host-Port und `TRUSTED_PROXIES` ist genau
   diese Adresse; auf `hel` (Rand-Caddy, kein eigener Caddy) ist es `10.10.10.70` (`deploy/hel/README.md`).
   Uvicorn läuft mit `--no-proxy-headers` (sonst würde es `X-Forwarded-For` von 127.0.0.1 glauben).
   Eine Limiter-Tabelle je Name (50 000 Schlüssel); IPv6 nach /64; bei voller Tabelle werden aktive Zähler nie
   verdrängt (abgelaufene fliegen raus). Neue Schlüssel zählen bei voller Tabelle im **Überlauf** (fail-closed, aber
   großzügig): gemeinsamer Eimer mit dem **50-fachen** Einzel-Limit (`OVERFLOW_FACTOR`: Login 10/min → 500/min,
   Registrierung 10/h → 500/h – ein Vielfaches des Andrangs auf dem Kongress, aber endlich) und je Gruppe (IPv6 als /48,
   IPv4 die Adresse; höchstens 256 Gruppen) ein Teil-Eimer mit dem **5-fachen** Limit: Ein Angreifer mit
   einer IPv6-Zuweisung erschöpft nur seinen Teil-Eimer, erst zehn Gruppen leeren den gemeinsamen. Speicher strikt
   begrenzt (`max_keys × Limit + 50 × Limit + 256 × 5 × Limit` Zeitstempel je Tabelle); die Log-Warnung nennt nur den
   Tabellennamen, nie eine Adresse. Zusätzlich Limits je Nutzer (`/api/match`, Fahrt anlegen, Anfrage senden).
   **Login-Limits (Entscheidung):** Das IP-Limit zählt jeden Versuch; je normalisierter E-Mail zählen nur
   **Fehlversuche** – eng je E-Mail+IP (5/min; IPv6 als /48, damit das Wechseln der /64-Präfixe einer
   Zuweisung nichts bringt), global je E-Mail deutlich höher (30/min). Wer die Adresse des Opfers
   kennt, kann dessen Login so von einer anderen Adresse aus nicht mehr aussperren (früher: 5 beliebige Anfragen/min
   genügten). Die Plätze werden *vor* der awaiteten Passwortprüfung belegt und bei richtigem Passwort zurückgegeben
   (atomar: parallele Versuche überschießen das Limit nicht). Ist ein Limit erreicht, kommt vor der Prüfung 429 – auch
   bei richtigem Passwort (sonst Passwort-Orakel, Raten unbegrenzt) und gleich für unbekannte E-Mails (kein
   Konto-Orakel); die 429-Antwort rechnet keinen Hash (gleich für bekannte/unbekannte E-Mails, die 401-Zeit ist wie
   bisher durch den Dummy-Hash angeglichen). Konfig-Schlüssel umbenannt: `RATE_LIMIT_LOGIN_PER_EMAIL_PER_MIN` →
   `RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_IP_PER_MIN` / `RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_PER_MIN` (ein alter Wert in einer
   vorhandenen `.env` wird still ignoriert – bei Ausrollen umbenennen).
   **Bekannte Grenzen:** (a) Wer von mindestens 6 verschiedenen Adressen (IPv4 bzw. IPv6-/48; bei den Standardwerten
   6 × 5 = 30 Fehlversuche/min) falsche Passwörter schickt, füllt das globale Fehlversuchs-Limit je E-Mail und sperrt
   das Konto dann minutenweise (bis das älteste Fenster abläuft) für Logins – das ist der Preis des Schutzes
   vor verteiltem Raten (größeres Botnetz nicht nötig, aber eine einzelne Adresse genügt nicht mehr); hinter gemeinsamer NAT (Kongress-WLAN) teilen sich Nutzer außerdem die E-Mail+IP-Grenze.
   (b) Sehr viele *verschiedene* IPv6-/48- bzw. IPv4-Adressen können den gemeinsamen Überlauf-Eimer weiterhin ausschöpfen
   (Registrierung dann bis zu 1 h für neue Clients ohne Zähler); bestehende Zähler bleiben immer unberührt.
   (c) Alle Zähler liegen im Prozessspeicher (ein Worker, Neustart leert sie); mehrere Worker bräuchten einen
   gemeinsamen Speicher. (d) Caddys automatische HTTP→HTTPS-Weiterleitung entsteht außerhalb der Site-Handler und trägt
   `Server: Caddy` sowie keine Sicherheits-Header (leerer Body, keine Daten).
   **Swagger/OpenAPI** (`/api/docs`, `/api/openapi.json`) nur außerhalb von `production`: die UI lädt Skripte vom
   CDN `cdn.jsdelivr.net`.
10. **Cookie/CSRF.** JWT in HttpOnly-Cookie, `SameSite=Strict`, `Secure` per Env
    abschaltbar (lokales HTTP). Zusätzlich Origin-Prüfung bei schreibenden Requests.
11. **CSP.** Der Tile-Host in `img-src` wird aus derselben Env (`TILE_URL`) abgeleitet.
    Leaflet-Marker-Icons werden lokal gebündelt.
12. **Datenbank-Tests** laufen gegen echtes PostGIS (Compose-DB lokal, Service-Container in
    der CI), nicht gemockt – insbesondere die Row-Locks (Phase 5). Datenbank-Constraint
    verhindert doppelte aktive Anfragen pro (Fahrt, Mitfahrer).
13. **Routing-Tests.** Matching-Logik gegen einen Fake-`RoutingClient`; wenige echte
    OSRM-Tests (Marker `osrm`) gegen das Hamburg-Extrakt.
14. **Make.** macOS liefert GNU Make 3.81 – die Makefiles nutzen keine Make-4-Features.
15. **Definition of Done „frischer Linux-Host“.** Lokal steht kein frischer Linux-Host zur
    Verfügung (macOS/arm64). Der Nachweis ist die CI auf `ubuntu-latest` (amd64). Ein Lauf
    auf einer echten Proxmox-VM bleibt für den Betreiber zu bestätigen und wird als solcher
    gekennzeichnet.
16. **Migrationen beim Start.** Der api-Container führt `alembic upgrade head` vor dem
    Start aus (Einzelinstanz-Betrieb), damit `make up` ohne Extra-Schritt funktioniert.
17. **Linear.** Das Linear-MCP ist in dieser Umgebung nicht authentifiziert und das Repo hat
    keinen `## Linear`-Abschnitt; Issues werden daher nicht gepflegt (TODO für den Betreiber).
18. **Matching: Standort-Orakel und Abtasten des Routenanfangs (Phase 4).** Die Anzeige-Route wird am Anfang um
    `MATCH_ROUTE_TRIM_START_M` gekürzt (entlang der Route, in Metern). Das schützt die **Anzeige**, nicht gegen aktives
    Abtasten: Vorfilter (`ST_DWithin` auf der vollen Route) und Umweg (ab dem exakten Fahrer-Start) brauchen die
    ungekürzte Route. Wer viele Abholpunkte abfragt, kann aus „Treffer ja/nein“ und den Umweg-Werten den Start grob
    (einige hundert Meter bis wenige km, je nach Rundung) eingrenzen. Entschärft durch (a) Anmeldepflicht, (b) **gröbere Ausgabe**
    (`detour_km` in 0,5-km-Schritten, `detour_minutes` ganzzahlig, `detour_ratio` 2 Stellen; Filter und Sortierung
    intern exakt), (c) Rate-Limit `RATE_LIMIT_MATCH_PER_MIN` je IP und `RATE_LIMIT_MATCH_USER_PER_MIN` je Nutzer (Phase 7). Verbleibende Grenze bewusst hingenommen
    (kurzlebige Event-App); README/Datenschutzdoku nennen sie in Phase 8.
    **Phase 5:** `RequestOut.detour_km`/`detour_minutes` (Anlegen, Listen, accept/decline/cancel) nutzen dieselben
    `coarse_detour_*`-Funktionen wie `/api/match`; `422 detour_too_large` entspricht dem Filter `acceptable`. Ein
    eigener Abholpunkt liefert damit keine genauere Umweg-Information als die Suche. Das enge Rate-Limit für
    `POST /api/rides/{id}/requests` gibt es seit Phase 7 (`RATE_LIMIT_REQUESTS_CREATE_PER_HOUR`, je Nutzer).

19. **Frontend (Phase 6).** SPA ohne Browser-Speicher, kein Auto-Login nach der Registrierung, Karte auch per Tastatur/Koordinaten
    bedienbar. Umweg-Werte werden so angezeigt, wie die API sie vergröbert liefert („≈ +1,5 km, ≈ +4 Min.“). Die **Quellenangabe**
    der Karte kommt aus `GET /api/config` (`map.attribution`, `TILE_ATTRIBUTION`), das Frontend lässt darin nur Text und
    https-Links zu. Der öffentliche OSM-Tile-Server bleibt **nur Entwicklung**; `TILE_URL`/`TILE_ATTRIBUTION` sind konfigurierbar,
    die CSP folgt `TILE_URL`. **Referrer-Policy:** SPA `strict-origin-when-cross-origin`, `/api` und Fehlerseiten `no-referrer`
    (Caddy setzt die Header je `handle`-Zweig; ein Site-weiter Block würde wegen `-Server` zurückgestellt laufen und `/api` überstimmen).
    Geocoder (Photon/Nominatim) sind bewusst nicht angebunden (externe Anfragen aus dem Browser).
20. **Scheinfahrten und das Kandidatenlimit (SEC-1).** Die Kandidaten werden nach Luftlinie sortiert und auf
    `MATCH_MAX_CANDIDATES` (30) gekürzt; ohne weitere Sperre könnte ein Konto mit ≥ 30 Fahrten exakt am Abholpunkt alle
    Plätze belegen und echte Fahrer aus dem Ergebnis verdrängen. Sperren: (a) `MAX_OPEN_RIDES_PER_DRIVER` (5): offene oder
    volle, noch nicht abgefahrene Fahrten je Fahrer, sonst `409 too_many_open_rides` (Nutzerzeile `FOR NO KEY UPDATE` gesperrt,
    damit parallele Anlagen die Grenze nicht überschreiten); (b) `MATCH_MAX_CANDIDATES_PER_DRIVER` (2): je Fahrer kommen nur
    die nächsten Fahrten in die Kandidatenliste (`row_number() OVER (PARTITION BY driver_id …)` im Vorfilter; der teure
    Anzeigeroutenteil läuft erst für die Gewinner). Folge für echte Fahrer: Wer mehr als 2 Fahrten mit gleichem Start im
    selben Zeitfenster anbietet, erscheint in der Suche mit den 2 frühesten; die übrigen sind über das Zeitfenster sichtbar.
    **Verbleibende Grenze (bewusst hingenommen):** Mit mehreren Konten (je 1 Scheinfahrt am Abholpunkt) lassen sich die 30 Plätze
    weiterhin füllen; Registrierung ist auf 10/h je IP begrenzt, eine E-Mail-Bestätigung fehlt (bräuchte SMTP → offene
    Entscheidung des Betreibers, Annahme 6). Gegenmittel im Betrieb: Konten löschen/Zugang sperren, `MATCH_MAX_CANDIDATES`
    erhöhen (mehr OSRM-Last je Suche).

## Offene Fragen (nicht blockierend)

- Endgültiger Ort und Datum des 40C3 → `EVENT_*` in `.env` setzen.
- Eigener Tile-Server vs. öffentlicher OSM-Tile-Server (nur Dev laut Tile Usage Policy).
- Soll Caddy auf der Proxmox-VM selbst TLS terminieren, oder sitzt davor ein Proxy?

## Phasen

- [x] **Phase 1** Repo-Skelett, Compose (db + osrm + api, Health), Migrationen, CI
- [x] **Phase 2** Auth, Einwilligung, Konto löschen
- [x] **Phase 3** Fahrten anlegen/listen/ändern/stornieren inkl. OSRM-Route und Snap-Prüfung
- [x] **Phase 4** Matching (Vorfilter + Umweg) inkl. Randfall-Tests, Demo-Daten (`make seed-demo`)
- [x] **Phase 5** Anfragen/Accept mit Locking, Kontaktfreigabe (E-Mail erst bei `accepted`, nur Beteiligte; grobe Umweg-Werte)
- [x] **Phase 6** Frontend (React/Vite/TypeScript/Leaflet, `web/`), Container `web`, Caddy liefert es aus, CI-Job `web`
- [x] **Phase 7** Retention-Cleanup, Backup (age), Security-Header, Rate-Limits (IP, E-Mail, Nutzer), Caddy
- [ ] Phase 8 E2E-Tests (**erledigt**, `e2e/`, CI-Job `e2e`), README, Datenschutzdoku, Aufräumen (offen)

Jede Phase: eigener Conventional-Commit, grüne Tests, sofort pushen.

## Was bisher tatsächlich geprüft wurde

Wird pro Phase ergänzt – nur Dinge, die wirklich ausgeführt wurden.

### Phase 1 (lokal: macOS arm64, Docker Desktop, Hamburg-Extrakt)

- `make env DEV=1` → `make osrm-prepare` (≈ 25 s) → `make up`: db, osrm und api werden **healthy**.
- `GET /api/health/ready` → 200, PostGIS 3.5, Eventziel 20,5 m von der nächsten Straße.
- Alembic läuft beim API-Start (`Running upgrade -> 0001`); Hoch-/Runter-/Hochmigration getestet.
- Schema-Garantien per Test gegen echtes PostGIS: GiST-/B-Tree-Indizes, Sitz-Constraints,
  max. eine aktive Anfrage pro (Fahrt, Mitfahrer), `ON DELETE CASCADE` bei Konto-Löschung.
- `make test`: **33 Tests grün** (davon 4 Live-Tests gegen echtes OSRM); `make lint` grün (ruff, mypy strict).
- Gefunden und behandelt: (a) fremder Postgres auf Port 5432 → Port 55432 + Schutzprüfung in den Test-Fixtures;
  (b) OSRM snappt München im Hamburg-Extrakt still auf eine Straße 592 km entfernt → Snap-Prüfung ist Pflicht.

- **CI auf ubuntu-latest (amd64) grün** (Run 42846007900): Lint, mypy, Tests sowie Stack-Lauf
  `env → osrm-prepare → up → Readiness → Tests` mit dem offiziellen `postgis/postgis`-amd64-Image.
  Im Log belegt: `MD5 geprüft` für den Hamburg-Download.
- **Wörtlicher DoD-Pfad** `cp .env.example .env && make osrm-prepare && make up`: Der Produktionsmodus
  verweigert Platzhalter-Secrets (`CHANGE_ME`) bewusst – das wurde als Fehlermeldung bestätigt. Deshalb füllt
  `make up`/`make test` Platzhalter automatisch mit Zufallswerten (`scripts/fill-secrets.sh`, idempotent,
  rührt echte Werte nie an). Die CI testet beide Wege (Matrix `make-env-dev` / `cp-env-example`).
  Einzige Abweichung in der CI: Hamburg statt `germany-latest` (4,9 GB).
- `alembic check` ist sauber → als Test aufgenommen (Modelle und Migration dürfen nicht driften). 34 Tests grün.

**Nicht geprüft:** `germany-latest` (Ressourcen nur extrapoliert, s. o.), `docker compose` unter Coolify.
Der Lauf in einem Proxmox-LXC mit Hamburg-Extrakt wurde am 2026-11-10 geprüft (siehe unten).

### Phase 3 (lokal: macOS arm64, echtes PostGIS + OSRM Hamburg)

- `make lint` (ruff, ruff format, mypy strict) grün; `make test`: **270 Tests grün** (inkl. Phase 2 und Live-Tests
  gegen echtes OSRM: `test_rides_live.py`, `test_osrm_live.py`).
- Geprüft: Anlegen mit Route und Snap-Prüfung (Start und Event), Fehlerpfade speichern nichts, Ändern/Stornieren mit
  Sperr-Reihenfolge Fahrt vor Anfragen, Fremdzugriff → 404, Pagination von `GET /api/rides/mine`.
- Rate-Limit pro Nutzer für `POST /api/rides`: seit Phase 7 vorhanden (`RATE_LIMIT_RIDES_CREATE_PER_HOUR`).

### Phase 4 (lokal: macOS arm64, echtes PostGIS + OSRM Hamburg)

- `make lint` grün; `make test` grün (Zahlen im Commit), inkl. Live-Tests gegen echtes OSRM (`test_match_osrm_live.py`).
- Geprüft: SQL-Vorfilter nutzt den GiST-Index (Plan-Test), Umweg je Kandidat parallel (max. 8), Randfälle
  (Länge-0-Route, `inf`-Verhältnis, Kandidat ohne Route, OSRM nicht erreichbar → 503, Pickup außerhalb → 422),
  gekürzte Anzeige-Route, Rundung der Umweg-Werte (Filter/Sortierung exakt).
- `make seed-demo` real gegen eine frische Scratch-Datenbank und eine lokal gestartete API (`APP_ENV=development`)
  ausgeführt: 7 Konten (`@demo.invalid`), 6 Fahrten, Kontrollsuche ab Altona 6 Treffer; zweiter Lauf idempotent
  (0 neu). **Erst nach Phase 5 möglich:** Demo-Anfragen/-Annahmen (das Skript legt noch keine Anfragen an).

### Phase 7 (lokal: macOS arm64, Docker Desktop, echtes PostGIS + OSRM Hamburg)

- Integriert aus dem Entwicklungszweig (Squash) und nachgezogen: Rate-Limiter je Name mit fail-closed-Verhalten,
  Login-Limit je E-Mail, Nutzer-Limits für Match/Fahrt anlegen/Anfrage senden, Docs nur außerhalb `production`,
  Caddy-Netz mit fester Adresse + `TRUSTED_PROXIES` als /32 (`docker-compose.proxy.yml`), Log-Rotation für alle Dienste,
  `--no-proxy-headers` im API-Image, `Via`-Header von Caddy entfernt.
- Ausgeführt: `make lint` grün; `make test` (Gesamtsuite, `REQUIRE_OPS_TESTS=1`, keine Tests übersprungen) – Zahl im
  Commit. Rate-Limits laufen auch über die echte App (`test_ratelimit_app.py`); Caddy → **echte API** im Wegwerf-Aufbau
  (`test_security_caddy_api.py`: Header je genau einmal, kein `Server`/`Via`, HTTP→HTTPS, `/api/health/ready` extern 404,
  Rate-Limit je echtem Client trotz gefälschtem `X-Forwarded-For`).
- Zusätzlich real ausgeführt: `docker compose … --profile proxy up` (Projektname `p7check`, Hochports) mit
  `docker-compose.proxy.yml`: Caddy `172.29.71.2`, API `172.29.71.129`, `TRUSTED_PROXIES=172.29.71.2`, API-Port nicht auf dem
  Host erreichbar, `/api/health/ready` über Caddy 404; danach `down -v`.
- **Nicht lokal geprüft:** `shellcheck` (nicht installiert; läuft in der CI), Let's-Encrypt-Zertifikate (lokal interne CA),
  Lauf auf `hel` (nichts ausgerollt), Last/Dauerbetrieb, mehrere Worker.

### Phase 6 (lokal: macOS arm64, Docker Desktop, echtes PostGIS + OSRM Hamburg)

- Integriert aus dem Entwicklungszweig (Squash) und nachgezogen: vergröberte Umweg-Werte (Typen/Anzeige/Tests), Fehlerbehandlung
  für `413`/`429`/`502-504` ohne JSON-Körper, `map.attribution` in `GET /api/config` (Vertrag, `test_config_endpoint.py`),
  Referrer-Policy je Zweig im Caddyfile, `web` im Netz `proxy` und als Caddy-Abhängigkeit, CI-Job `web` (tsc, eslint, vitest,
  Build, `check:external`, Docker-Build), `make web-check`.
- Ausgeführt: `make lint` grün; `make test` (Gesamtsuite, `REQUIRE_OPS_TESTS=1`): 783 passed; `make web-check`: tsc, eslint,
  vitest (102 Tests), Build, `check:external` grün.
- Real ausgeführt: `docker compose -f docker-compose.yml -f docker-compose.proxy.yml --profile proxy up` (api, web, caddy gegen
  die laufende DB/OSRM, Caddy auf `http://localhost:8088`): `/` und `/mitfahrt-suchen` 200 `text/html` mit `Cache-Control: no-store`,
  `/assets/*.js` `public, max-age=31536000, immutable`, Referrer-Policy SPA `strict-origin-when-cross-origin` / `/api/*` (auch 404, 401,
  413) `no-referrer`, CSP `img-src 'self' data: https://tile.openstreetmap.org`, `/api/health/ready` 404, 70-KB-Body → 413;
  Registrieren → Anmelden → `DELETE /api/me` → 204 mit gelöschtem Cookie, danach `GET /api/me` 401, die API weist Schreibzugriffe mit einer Origin außerhalb `ALLOWED_ORIGINS`/`DOMAIN` mit 403 ab (auch `http://localhost:8088`, solange `ALLOWED_ORIGINS` den Port nicht nennt; die curl-Läufe sendeten `Origin: http://localhost`).
  Der `web`-Container läuft mit `read_only`, `cap_drop: ALL`. Danach alles wieder entfernt.
- **Nicht geprüft:** Verhalten in einem echten Browser (der Playwright-Browser war in dieser Umgebung nicht verfügbar): CSP-Verstöße
  in der Konsole, Kartendarstellung, Tastaturbedienung – die Komponenten sind nur in jsdom getestet. Let's-Encrypt/TLS am `web`-Pfad.
  Frontend auf `hel`: nicht ausgerollt, der Rand-Caddy reicht weiter nur `/api/health` durch (siehe `deploy/hel/README.md`).

### Phase 8, Teil E2E (lokal: macOS arm64, Docker Desktop, echtes PostGIS + OSRM Hamburg + Chromium)

- `make e2e` startet das eigene Compose-Projekt `mitfahrer-e2e` (`e2e/stack.sh`; Caddy auf `http://127.0.0.1:18080`, Rate-Limits nur dort
  angehoben, Kachel-Host `tiles.e2e.invalid` wird im Test beantwortet) und führt Playwright 1.64.1 (Chromium) aus.
  **6 Tests grün** (4 Dateien: Konto+Fahrt, Mitfahrt+Kontaktfreigabe, Konto löschen, externe Requests+Tastatur), Testlauf ≈ 4 s;
  kalter Gesamtlauf `make e2e` (Stack bauen/starten + Tests) ≈ 24 s. Dreimal hintereinander ohne Fehler.
- Negativprüfungen wurden absichtlich gebrochen und schlugen fehl (E-Mail-Prüfung auf eine Antwort mit der eigenen Adresse; Kachel-Host
  fälschlich nicht erlaubt -> Verstoß gemeldet).
- Abweichung vom Auftrag: Die Routenvorschau gibt es im Produkt erst nach dem Speichern (Erfolgsseite mit Polyline, km und Minuten); der
  Test prüft genau das. Der Datumseingabe-Test per Tastatur setzt das `datetime-local`-Feld mit `fill()`.
- **CI (GitHub Actions, ubuntu-latest amd64): Job `e2e` grün**, 6 Tests, Playwright-Lauf ≈ 8 s, Gesamtdauer des Jobs ≈ 1 min 45 s bis
  1 min 51 s (Runs 42979852500 und 42980411936; Hamburg-Download + OSRM-Aufbereitung ≈ 20 s, Stack-Build ≈ 80 s, Chromium-Installation
  ≈ 20 s ohne Cache). Timeout 25 min. Caches: Chromium (`~/.cache/ms-playwright`, Schlüssel aus `e2e/package-lock.json`), npm, OSRM-Daten
  (Schlüssel `osrm-hamburg-v26.4.0-1`; beim ersten Lauf scheiterte das Speichern an root-eigenen Dateien, behoben). Bericht, Spuren und
  Bilder werden nur bei Fehlern als Artefakt `e2e-fehlerbericht` hochgeladen (nie `e2e/.stack` mit den Secrets).
- Nicht geprüft: Firefox/WebKit, Mobil-Viewport, echte Kacheln, TLS.

## Deployment auf Proxmox (hel) – Teilstand ausgeführt am 2026-11-10

Ziel war „dieselbe Proxmox-VM wie `m.sgr.ski`“. Tatsächlich läuft `m.sgr.ski` über den **winzigen Rand-LXC
CT 180 `sgr`** (1 GB RAM, nur Caddy) auf dem Wirt `hel`, der patchletter-Produktion und den Mailserver trägt und
RAM-seitig überbucht ist. Der Stack passt nicht in CT 180. Deshalb (mit Zustimmung des Betreibers): **eigener Gast
CT 220 `congress`** (LXC, 2 Kerne, 4 GB Kappe, 24 GB), `congress.sgr.ski` über einen zusätzlichen Block im Rand-Caddy,
**Hamburg**-Extrakt. Der Deutschland-Extrakt ist **nicht** ausgeführt (RAM-Spitze der Vorbereitung ≈ 20 GB, ungeprüft;
nicht auf diesem Wirt).

Alles Weitere – Aufbau, Firewall, Belege, offene Entscheidungen (PBS-Sicherung vor Phase 2!), Rückbau –
steht in **`deploy/hel/README.md`**.
