# Frontend (Phase 6)

Single-Page-App für das Mitfahrer-Matching: React 19, react-router, TypeScript (strict), Vite, Leaflet.
Sie spricht ausschließlich mit `/api` (gleiche Origin, Cookie `access_token`) und – nur für Kartenkacheln –
mit dem Tile-Server, dessen URL zur Laufzeit aus `GET /api/config` (`map.tile_url`) kommt. Verbindlich ist
`docs/API-CONTRACT.md`.

## Befehle (in `web/`)

| Befehl | Zweck |
|---|---|
| `npm ci` | Abhängigkeiten gemäß `package-lock.json` installieren |
| `npm run dev` | Entwicklungsserver (Port 5173); `/api` wird an `http://127.0.0.1:8000` weitergeleitet. Schreibende Aufrufe tragen die Origin `http://localhost:5173`; die API lehnt sie sonst mit 403 ab, daher in `.env` `ALLOWED_ORIGINS=http://localhost:5173` setzen (und `COOKIE_SECURE=false`) |
| `npm run typecheck` | `tsc --noEmit` (ist auch Teil von `npm run build`) |
| `npm run lint` | ESLint (typescript-eslint, react-hooks, jsx-a11y) |
| `npm test` | Vitest + Testing Library (gemocktes `fetch`, kein Backend nötig) |
| `npm run build` | Typprüfung und Produktions-Build nach `web/dist` |
| `npm run check:external` | Belegt, dass `web/dist` keine externen URLs enthält (begründete Ausnahmen im Skript) |

## Seiten

`/` Start (Erklärung, Datenschutzhinweis) · `/registrieren` · `/anmelden` · `/fahrt-anbieten` ·
`/mitfahrt-suchen` · `/meine-fahrten` (Fahrten, eingehende und ausgehende Anfragen) · `/datenschutz` und
`/impressum` (**Entwurf/Platzhalter, vor dem Betrieb ausfüllen**) · `/konto-loeschen`.

## Entscheidungen

* **Kein Auto-Login nach der Registrierung.** `POST /api/auth/register` antwortet immer mit 202; die Seite
  zeigt eine neutrale Meldung und verweist auf die Anmeldung (kein Nutzer-Orakel).
* **E-Mail des Gegenübers** wird nur angezeigt, wenn `status === "accepted"` *und* `counterpart_email`
  gesetzt ist. Bei `pending` steht ein Hinweis, dass sie erst nach Annahme erscheint.
* **Umweg-Werte sind vergröbert** (Phase 4/5): `detour_km` in 0,5-km-Schritten, `detour_minutes` ganzzahlig,
  `detour_ratio` mit 2 Nachkommastellen. Die Anzeige sagt „≈ +1,5 km, ≈ +4 Min. (≈ +12 %)“ und rechnet nichts nach.
* **Fehler** werden anhand des Fehlercodes (`error.code`) auf deutsche Texte abgebildet
  (`src/i18n/de.ts`, Schlüssel `error.*`); Eingabewerte aus Antworten werden nie angezeigt oder protokolliert.
  `401` löst die Weiterleitung zur Anmeldung aus (`?next=` nur für interne Pfade). Antworten ohne JSON-Körper
  (Caddy/nginx: `413` vom Body-Limit, `429`, `502`/`503`/`504`) werden über den Statuscode auf die Vertragscodes
  `payload_too_large`, `rate_limited` (mit `Retry-After` in ganzen Sekunden, sonst allgemeiner Text) und
  `service_unavailable` abgebildet.
* **Karte ohne Maus bedienbar:** Die Karte ist per Tab fokussierbar (Pfeiltasten verschieben, `+`/`-` zoomen).
  Punkte lassen sich zusätzlich per Koordinateneingabe oder über „Kartenmitte als Punkt übernehmen“ setzen.
* **Quellenangabe der Karte** kommt aus `GET /api/config` (`map.attribution`, Umgebungsvariable `TILE_ATTRIBUTION`
  der API) und gehört zum Kartenserver aus `map.tile_url`. `src/lib/attribution.ts` lässt nur Text und
  `<a href="https://…">`-Links zu (alles andere wird zu Text), bevor Leaflet sie als HTML einsetzt.
* **Kartenserver:** Der öffentliche OSM-Tile-Server (Standard in `.env.example`) ist laut Tile Usage Policy **nur für
  Entwicklung** gedacht; für den Betrieb `TILE_URL` und `TILE_ATTRIBUTION` auf einen eigenen oder kommerziellen
  Anbieter setzen. Die CSP (`img-src`) folgt `TILE_URL` automatisch (`caddy/tile-origin.sh`).
* **Marker-Icons und Leaflet-CSS** kommen aus dem Bundle (Vite-Assets als Dateien unter `/assets/`, `assetsInlineLimit: 0`;
  nichts wird als `data:`-URI eingebettet, `npm run check:external` prüft das). Einzige Ausnahme ist Leaflets
  eingebautes 1x1-GIF beim Abbrechen eines Kachelabrufs (`Util.emptyImageUrl`): Unter strenger CSP blockiert der
  Browser es und meldet eine Verletzung in der Konsole, ohne sichtbare Wirkung.
* **Zeit:** Eingaben (`datetime-local`) sind lokale Zeit und werden als ISO 8601 mit Zeitzone (UTC) gesendet.
* **Kein Browser-Speicher:** Es werden weder `localStorage` noch Cookies vom Frontend gesetzt.
* **Referer an den Tile-Server:** Kacheln werden mit `referrerPolicy: "strict-origin"` geladen (nur Origin,
  kein Pfad), weil die OSM Tile Usage Policy einen Referer verlangt (`no-referrer` ist daher keine Option).
* **Was der Kartenserver erfährt:** IP-Adresse, angeforderte Kachelkoordinaten (also den angezeigten Ausschnitt,
  auch die Gegend von Start-/Abholpunkt) und die Origin als Referer. Das steht unter der Karte (`map.tilesNotice`)
  und in der Datenschutzerklärung (`privacy.s7`). Bei der Punktwahl ist der Zoom auf Stufe 16 begrenzt
  (`PICK_MAX_ZOOM`). **Bekannte Grenze:** Ein eigener Tile-Server oder Tile-Proxy hinter der eigenen Origin
  (Phase 7 / Betrieb) würde die Weitergabe an Dritte ganz vermeiden; das Frontend braucht dafür nur eine andere
  `TILE_URL`, der Text zeigt dann automatisch den neuen Host.
* **Getippte Koordinaten** werden beim Absenden übernommen (`PointPicker`-Handle `commit()`); sind sie ungültig,
  wird nichts gesendet und der Fehler am Feld angezeigt.
* **Kontrast:** Rahmen von Bedienelementen und Karte nutzen `--border-ui` (mind. 3:1, WCAG 1.4.11);
  `src/styles.contrast.test.ts` rechnet alle Token-Paare nach.

## Geocoder-Adapter (`src/geocoder/`)

Es gibt nur die Schnittstelle `Geocoder` und die Standardimplementierung **`none`** (Ortswahl per Karten-Klick,
Kartenmitte oder Koordinaten). **Photon und Nominatim sind bewusst nicht angebunden:** Aus dem Browser wären
das externe Requests, die Adresseingaben und die IP-Adresse an Dritte senden. Das widerspricht dem
Datenschutzkonzept, solange es keinen Backend-Proxy gibt. `GEOCODER=photon|nominatim` in `/api/config` wird
daher wie `none` behandelt. Wer einen Adapter ergänzen will: Backend-Proxy schaffen, Klasse in
`createGeocoder()` registrieren, Adresssuche in `PointPicker` anzeigen, CSP und Datenschutzerklärung nachführen.

## Sprachen

Standard ist Deutsch (`src/i18n/de.ts`, vollständig). `src/i18n/en.ts` enthält nur einen Teil; fehlende
Schlüssel fallen auf Deutsch zurück. Einen Sprachumschalter gibt es noch nicht (`I18nProvider` nimmt `locale`).

## Auslieferung und Reverse Proxy

* Der Container `web` (`Dockerfile`: Node-Build, dann `nginx-unprivileged`, Port 8080) liefert `dist/`: **SPA-Fallback**
  auf `index.html`, `/assets/*` (gehasht) mit `Cache-Control: public, max-age=31536000, immutable`, `index.html` mit
  `no-store`, `/api/*` als 404 (kein HTML-Fallback für falsch geleitete API-Aufrufe), kein Zugriffsprotokoll.
* Caddy (`caddy/Caddyfile`, Compose-Profil `proxy`) leitet `/api/*` an `api:8000`, alles andere an `web:8080`, und setzt
  die Sicherheits-Header. Die Referrer-Policy ist für die SPA `strict-origin-when-cross-origin` (die Tile Usage Policy
  braucht einen Referer; Kacheln werden zusätzlich mit `referrerpolicy=strict-origin` geladen, es geht also nur die
  Origin raus), für `/api` und Fehlerseiten `no-referrer`. Die CSP erlaubt `img-src 'self' data: <Host aus TILE_URL>`.
* Lokal prüfen: `docker compose -f docker-compose.yml -f docker-compose.proxy.yml --profile proxy up -d --build`
  (mit `CADDY_SITE_ADDRESS=http://localhost HTTP_PORT=8088` ohne TLS; für Anmeldungen im Browser zusätzlich `ALLOWED_ORIGINS=http://localhost:8088` und `COOKIE_SECURE=false` in `.env`, weil die Origin den Port enthält), dann `curl -i http://localhost:8088/` und
  `/api/health`. Das Frontend benötigt kein Inline-Skript; Leaflet setzt Inline-Stile, daher `style-src 'unsafe-inline'`.
  **Nicht in einem echten Browser verifiziert** – vor dem Livegang einmal mit offener Konsole prüfen (CSP-Verstöße).
