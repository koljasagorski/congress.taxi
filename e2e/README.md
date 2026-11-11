# E2E-Tests (Playwright)

Ende-zu-Ende-Tests gegen den **echten Stack**: Caddy liefert SPA und API, die API nutzt PostGIS und OSRM (Hamburg-Extrakt).
Es wird nichts gemockt außer den Kartenkacheln (siehe unten).

## Ausführen

```sh
make osrm-prepare   # einmalig: Hamburg-Extrakt (siehe README/PLAN)
make e2e            # Stack starten/prüfen, Chromium installieren, Tests ausführen
make e2e E2E_ARGS='-g "Konto"'   # nur passende Tests
make e2e-down       # E2E-Stack stoppen und seine Volumes entfernen
```

`make e2e` startet ein **eigenes Compose-Projekt `mitfahrer-e2e`** (`e2e/stack.sh`), getrennt vom Dev-Stack `mitfahrer`:

| Was | Wert |
|---|---|
| Einstieg (Caddy, HTTP ohne TLS) | `http://127.0.0.1:18080` (`E2E_HTTP_PORT`) |
| DB / OSRM (nur localhost) | 55433 / 55001 (`E2E_DB_PORT`, `E2E_OSRM_PORT`) |
| Konfiguration | `e2e/.stack/e2e.env` (Zufalls-Secrets, nicht im Git, Rechte 600) |
| Rate-Limits | **nur in diesem Stack** angehoben (alle Browser teilen sich eine Client-IP; Standard: Registrierung 10/h je IP) |
| Netz | `172.29.72.0/24` (kollidiert nicht mit dem Standard `172.29.71.0/24`) |

Die Produktions-Standardwerte (`.env.example`, `docker-compose.yml`) bleiben unverändert; alle Abweichungen stehen in
`e2e/stack.sh` und `e2e/docker-compose.e2e.yml`. Gegen einen anderen Stack: `BASE_URL=… npx playwright test` (dessen
`ALLOWED_ORIGINS` muss diese Origin enthalten, sonst weist die API Schreibzugriffe mit 403 ab).

## Aufbau

- `tests/01-konto-und-fahrt.spec.ts`: Registrieren (Einwilligung Pflicht), Login, Fahrt anbieten (Karte anklicken, Route nach dem Speichern), Logout
- `tests/02-mitfahrt-und-kontaktfreigabe.spec.ts`: Fahrer und Mitfahrer in zwei Browser-Kontexten; Treffer mit Umweg, Anfrage, Annahme; E-Mail der Gegenseite erst danach (DOM **und** mitgeschnittene API-Antworten)
- `tests/03-konto-loeschen.spec.ts`: Konto löschen; kein Login mehr, Daten weg, Sitze wieder frei
- `tests/04-extern-und-tastatur.spec.ts`: keine externen Requests außer dem Kachelserver; Tastaturbedienung der Hauptformulare
- `tests/05-schmale-ansicht.spec.ts`: Reflow bei 360×740 (WCAG 1.4.10): 80-Zeichen-Anzeigename und ca. 100 Zeichen lange E-Mail-Adressen in Kopfzeile, Trefferliste und Kontaktfreigabe erzeugen keinen horizontalen Scroll
- `support/`: Fixtures (Netzwerkwächter, Konten, zweiter Kontext), API-Helfer, Zeit, UI-Helfer. Die Texte kommen aus `web/src/i18n/de.ts`.

## Regeln für neue Tests

- **Deterministisch:** keine Sleeps, nur Erwartungen (`expect(...).toBe…`, `toHaveURL`). Keine Wiederholungen (`retries: 0`).
- **Isoliert:** jedes Konto hat eine eindeutige, kleingeschriebene E-Mail und einen eindeutigen Anzeigenamen (`makeUser`);
  Treffer werden über den Anzeigenamen gefunden, nie über Reihenfolge oder Anzahl. Konten werden am Ende per API gelöscht.
  Eigene Abfahrtszeit je Test und enge Suchfenster, damit Altdaten keine Treffer verdrängen.
- **Kachelserver:** `TILE_URL` des E2E-Stacks ist ein Platzhalter-Host (`tiles.e2e.invalid`). Der Netzwerkwächter beantwortet
  Anfragen daran mit einer 1×1-PNG, bricht **jeden anderen** fremden Host ab und lässt den Test dann fehlschlagen. Der Test
  braucht kein Internet.
- Negativprüfungen brauchen eine Positivkontrolle (z. B. „Antworten wurden mitgeschnitten und enthalten den erwarteten Namen“).

## Bekannte Grenzen

- Nur Chromium (Desktop-Viewport 1280×900, `de-DE`, `Europe/Berlin`). Mobil nur im Test `05` (360×740), sonst kein Mobil-Viewport; kein Firefox/WebKit.
- Das Feld „Abfahrt“ (`datetime-local`) besteht aus mehreren Tab-Stopps und wird in den Tastaturtests per `fill()` gesetzt.
- Die Routenvorschau erscheint im Produkt erst **nach** dem Speichern der Fahrt (Erfolgsseite), nicht schon davor.
- Echte Kartenkacheln und TLS (Let's Encrypt) sind nicht Teil dieser Tests.
