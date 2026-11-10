# PLAN – Mitfahrer-Matching für den 40C3

Stand: 2026-11-10 · Status: **Phasen 1–3 abgeschlossen** · Teilstand läuft auf Proxmox (hel, CT 220) unter https://congress.sgr.ski (nur `/api/health`)

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
   `accepted`-Anfragen gut (`release_seats_of_rider` in `app/api/me.py`). Phase 5 darf diese
   Funktion durch ihren eigenen Mechanismus ersetzen.
7. **Backups enthalten personenbezogene Daten.** Rotation (`BACKUP_KEEP_DAYS`, Standard 7)
   muss kürzer sein als die Retention-Frist; nach einem Restore muss der Cleanup-Job erneut
   laufen (dokumentiert, Phase 7).
8. **Logs ohne Personenbezug.** `/api/match?lat=&lon=` trägt Koordinaten im Query-String.
   Uvicorn-Access-Log ist aus (`--no-access-log`); eigenes Request-Log schreibt nur
   Methode, Routen-Template, Status, Dauer – keine IP, kein Query. Caddy-Access-Log wird
   in Phase 7 entsprechend abgeschaltet/gefiltert.
9. **Rate-Limit.** In-Memory, daher **ein** Uvicorn-Worker (dokumentierte Grenze). Echte
   Client-IP nur aus `X-Forwarded-For`, wenn der direkte Peer in `TRUSTED_PROXIES` steht.
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
    intern exakt), (c) Rate-Limit `RATE_LIMIT_MATCH_PER_MIN` (Phase 7). Verbleibende Grenze bewusst hingenommen
    (kurzlebige Event-App); README/Datenschutzdoku nennen sie in Phase 8.

## Offene Fragen (nicht blockierend)

- Endgültiger Ort und Datum des 40C3 → `EVENT_*` in `.env` setzen.
- Eigener Tile-Server vs. öffentlicher OSM-Tile-Server (nur Dev laut Tile Usage Policy).
- Soll Caddy auf der Proxmox-VM selbst TLS terminieren, oder sitzt davor ein Proxy?

## Phasen

- [x] **Phase 1** Repo-Skelett, Compose (db + osrm + api, Health), Migrationen, CI
- [x] **Phase 2** Auth, Einwilligung, Konto löschen
- [x] **Phase 3** Fahrten anlegen/listen/ändern/stornieren inkl. OSRM-Route und Snap-Prüfung
- [x] **Phase 4** Matching (Vorfilter + Umweg) inkl. Randfall-Tests, Demo-Daten (`make seed-demo`)
- [ ] Phase 5 Anfragen/Accept mit Locking, Kontaktfreigabe
- [ ] Phase 6 Frontend
- [ ] Phase 7 Retention-Cleanup, Backup, Security-Header, Rate-Limits
- [ ] Phase 8 E2E-Tests, README, Datenschutzdoku, Aufräumen

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
- Offen für Phase 7: Rate-Limit pro Nutzer für `POST /api/rides` (bisher nur das globale IP-Limit vorgesehen).

### Phase 4 (lokal: macOS arm64, echtes PostGIS + OSRM Hamburg)

- `make lint` grün; `make test` grün (Zahlen im Commit), inkl. Live-Tests gegen echtes OSRM (`test_match_osrm_live.py`).
- Geprüft: SQL-Vorfilter nutzt den GiST-Index (Plan-Test), Umweg je Kandidat parallel (max. 8), Randfälle
  (Länge-0-Route, `inf`-Verhältnis, Kandidat ohne Route, OSRM nicht erreichbar → 503, Pickup außerhalb → 422),
  gekürzte Anzeige-Route, Rundung der Umweg-Werte (Filter/Sortierung exakt).
- `make seed-demo` real gegen eine frische Scratch-Datenbank und eine lokal gestartete API (`APP_ENV=development`)
  ausgeführt: 7 Konten (`@demo.invalid`), 6 Fahrten, Kontrollsuche ab Altona 6 Treffer; zweiter Lauf idempotent
  (0 neu). **Erst nach Phase 5 möglich:** Demo-Anfragen/-Annahmen (das Skript legt noch keine Anfragen an).

## Deployment auf Proxmox (hel) – Teilstand ausgeführt am 2026-11-10

Ziel war „dieselbe Proxmox-VM wie `m.sgr.ski`“. Tatsächlich läuft `m.sgr.ski` über den **winzigen Rand-LXC
CT 180 `sgr`** (1 GB RAM, nur Caddy) auf dem Wirt `hel`, der patchletter-Produktion und den Mailserver trägt und
RAM-seitig überbucht ist. Der Stack passt nicht in CT 180. Deshalb (mit Zustimmung des Betreibers): **eigener Gast
CT 220 `congress`** (LXC, 2 Kerne, 4 GB Kappe, 24 GB), `congress.sgr.ski` über einen zusätzlichen Block im Rand-Caddy,
**Hamburg**-Extrakt. Der Deutschland-Extrakt ist **nicht** ausgeführt (RAM-Spitze der Vorbereitung ≈ 20 GB, ungeprüft;
nicht auf diesem Wirt).

Alles Weitere – Aufbau, Firewall, Belege, offene Entscheidungen (PBS-Sicherung vor Phase 2!), Rückbau –
steht in **`deploy/hel/README.md`**.
