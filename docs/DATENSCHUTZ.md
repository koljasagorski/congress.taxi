# Datenschutzerklärung – Vorlage (ENTWURF)

> **ENTWURF – KEIN RECHTSRAT.** Dieses Dokument ist eine fachliche Vorlage, die beschreibt, was die Anwendung „Mitfahrer-Matching 40C3“ nach dem
> Stand des Quellcodes tatsächlich verarbeitet. Es ist **nicht rechtlich geprüft** und ersetzt keine Beratung durch eine Rechtsanwältin, einen
> Rechtsanwalt oder eine Datenschutzbeauftragte bzw. einen Datenschutzbeauftragten. Rechtliche Einordnungen („voraussichtlich“, „zu prüfen“) sind Hinweise,
> keine Feststellungen. Alle Stellen in `[eckigen Klammern]` sind Platzhalter, die der Verantwortliche vor dem Betrieb ausfüllen muss.
>
> Stand: 2026-11-11, bezogen auf den Commit-Stand des Repositorys. Die Aussagen wurden gegen Code, Schema und Tests geprüft; Quellen stehen jeweils in Klammern
> (`api/app/…`, `web/src/…`). Was nicht geprüft werden konnte, ist als solches gekennzeichnet.

## 1. Verantwortlicher und Kontakt

| | |
|---|---|
| Verantwortlicher (Art. 4 Nr. 7 DSGVO) | `[Name bzw. Firma, Anschrift des Betreibers eintragen]` |
| Kontakt für Datenschutzanfragen | `[E-Mail-Adresse eintragen]` |
| Datenschutzbeauftragte(r), falls benannt | `[Name und Kontakt, oder „nicht benannt“ – Pflicht prüfen, siehe Abschnitt 13]` |
| Zuständige Aufsichtsbehörde | `[Landesbehörde eintragen]` |

## 2. Worum es geht

Die Anwendung vermittelt Mitfahrgelegenheiten zu einer Veranstaltung (Platzhalter: 40C3). Fahrerinnen und Fahrer legen eine Fahrt an, Mitfahrerinnen und Mitfahrer
suchen und fragen an; erst nach Annahme einer Anfrage sehen sich beide die E-Mail-Adresse. Es gibt keinen Chat, keine Zahlung, keine Push-Nachrichten, keine Bewertungen.

## 3. Verarbeitungstätigkeiten im Überblick

| Nr. | Tätigkeit | Zweck | Daten | Rechtsgrundlage (Vorschlag, zu prüfen) | Speicherdauer |
|---|---|---|---|---|---|
| V1 | Konto anlegen und anmelden | Zugang zur Vermittlung | E-Mail, Anzeigename, Passwort-Hash, Zeitpunkt der Einwilligung | Einwilligung, Art. 6 Abs. 1 lit. a | bis Selbstlöschung, sonst Stichtag (Abschnitt 9) |
| V2 | Fahrt anbieten | Vermittlung | Startpunkt, Zielpunkt, Route, Abfahrt, Plätze, Status | Einwilligung, Art. 6 Abs. 1 lit. a (ggf. zusätzlich lit. b, Vertragsanbahnung; zu prüfen) | wie V1 |
| V3a | Adresssuche (optional) | Punkt per Adresse setzen | Suchtext (flüchtig; geht über unseren Server an den Adressdienst), Treffer im Arbeitsspeicher | Einwilligung, Art. 6 Abs. 1 lit. a (zu prüfen) | höchstens 10 Minuten, nicht persistent |
| V3 | Mitfahrt suchen | Vermittlung | Abholpunkt (nur flüchtig für die Abfrage, nicht gespeichert), Zeitfenster | wie V2 | nicht gespeichert |
| V4 | Anfrage stellen, annehmen, ablehnen | Vermittlung | Abholpunkt, Umweg, Status, Zeitstempel | wie V2 | wie V1 |
| V5 | Kontaktfreigabe | Verabredung ermöglichen | E-Mail des jeweils anderen, erst nach Annahme | Einwilligung (Annahme durch beide), Art. 6 Abs. 1 lit. a | wie V1 |
| V6 | Missbrauchsschutz (Rate-Limits) | Sicherheit, Verfügbarkeit | mit Salt gehashte IP/E-Mail/Nutzer-ID im Arbeitsspeicher | berechtigtes Interesse, Art. 6 Abs. 1 lit. f (zu prüfen) | Minuten bis Stunden, nicht persistent |
| V7 | Betrieb, Fehlersuche (Logs) | Stabilität | Methode, Pfad-Template, Status, Dauer (ohne IP, ohne Query) | berechtigtes Interesse, Art. 6 Abs. 1 lit. f (zu prüfen) | Rotation (Abschnitt 9) |
| V8 | Sicherung (Backup) | Wiederherstellung | verschlüsselte Kopie der Datenbank | berechtigtes Interesse, Art. 6 Abs. 1 lit. f (zu prüfen) | `BACKUP_KEEP_DAYS`, spätestens zur Frist (Abschnitt 9) |
| V9 | Kartenanzeige | Orientierung | IP, Kachelkoordinaten, Origin gehen an den Kartenserver | berechtigtes Interesse, Art. 6 Abs. 1 lit. f, oder Einwilligung (zu prüfen) | beim Kartenserver, nicht in der Anwendung |

## 4. Welche Daten verarbeitet werden (nur, was das Schema speichert)

Quelle: `api/app/models.py` und die Migration `api/alembic/versions/0001_initial_schema.py`. Es gibt keine weiteren Tabellen mit Personenbezug.

**Tabelle `users` (Konto)**

| Feld | Inhalt |
|---|---|
| `id` | zufällige UUID |
| `display_name` | Anzeigename, 1 bis 80 Zeichen, frei wählbar (kann ein Klarname sein) |
| `email` | E-Mail-Adresse, klein geschrieben und getrimmt, eindeutig |
| `password_hash` | argon2id-Hash; das Passwort selbst wird nicht gespeichert |
| `consent_at` | Zeitpunkt der Einwilligung bei der Registrierung |
| `created_at` | Zeitpunkt der Anlage |

**Tabelle `rides` (Fahrt)**

| Feld | Inhalt |
|---|---|
| `driver_id` | Verweis auf das Konto |
| `start_geom` | **exakter Startpunkt** als Kartenpunkt, so wie ihn die Fahrerin gesetzt hat (oft der Wohnort) |
| `end_geom` | Zielpunkt (der Veranstaltungsort zum Zeitpunkt der Anlage) |
| `route_geom` | **vollständige** Fahrtroute als Linie (OSRM) |
| `route_distance_m`, `route_duration_s` | Länge und Dauer der Route |
| `seats_total`, `seats_available` | Plätze |
| `departure_time` | Abfahrtszeit |
| `status` | `open`, `full`, `cancelled` |
| `created_at` | Zeitpunkt der Anlage |

**Tabelle `ride_requests` (Anfrage)**

| Feld | Inhalt |
|---|---|
| `ride_id`, `rider_id` | Verweise auf Fahrt und anfragendes Konto |
| `pickup_geom` | **exakter Abholpunkt** als Kartenpunkt |
| `detour_distance_m`, `detour_duration_s` | berechneter Umweg (exakt gespeichert; ausgegeben wird er gerundet) |
| `status` | `pending`, `accepted`, `declined`, `cancelled` |
| `created_at`, `decided_at` | Zeitstempel |

**Nicht gespeichert** (Schema und Code geprüft): Klartext-Adressen (gespeichert wird nur der Kartenpunkt; bei aktiver Adresssuche wird ein Suchtext flüchtig verarbeitet, aber nie gespeichert oder protokolliert, siehe Abschnitt 7), Kennzeichen, Telefonnummern, IP-Adressen
(weder in der Datenbank noch im Anwendungslog), Gerätekennungen, Standortverläufe. Das Frontend ruft die Geolocation-API des Browsers nicht auf (Suche in `web/src` ohne Treffer);
die Position wird durch Klick auf die Karte, die Kartenmitte, eingetippte Koordinaten oder (optional) die Adresssuche gesetzt.

**Technische Daten außerhalb der Datenbank**

| Daten | Wo | Inhalt |
|---|---|---|
| Cookie `access_token` | Browser | JWT mit Nutzer-ID, Ausstellungs- und Ablaufzeit; kein Name, keine E-Mail |
| Zähler der Rate-Limits | Arbeitsspeicher der API | Zeitstempel je Schlüssel; der Schlüssel ist ein mit einem bei jedem Prozessstart neu erzeugten Salt gehashter Bezeichner (IP-Adresse, E-Mail, Nutzer-ID). Die IP-Adresse wird also **flüchtig verarbeitet**, aber nicht gespeichert und nicht protokolliert |
| Anwendungslog | Container-Log der API | Methode, Routen-Template, Status, Dauer (z. B. `GET /api/match -> 200 (31 ms)`); kein Query-String, keine IP, keine Pfad-IDs |
| Fehlerlogs der Datenbank | Container-Log der Datenbank | **nicht auf Personenbezug geprüft** (Abschnitt 11, R-5) |

## 5. Wer welche Daten sieht (Sichtbarkeit zwischen Nutzern)

Alle Nutzer sind angemeldet, aber **nicht verifiziert** (die E-Mail-Adresse wird nicht bestätigt; Abschnitt 11). Eine Nutzerin sieht von anderen:

| Was | Wer sieht es | Ab wann / wie lange | Quelle |
|---|---|---|---|
| Anzeigename der Fahrerin, Abfahrtszeit, freie Plätze, **gekürzte und vereinfachte Route** einer offenen Fahrt | **alle** angemeldeten Nutzer (in der Suche, soweit die Fahrt zur Abfrage passt) | solange die Fahrt offen ist | `GET /api/match`, `app/services/matching.py` |
| Umweg (≈ km und Minuten, gerundet) und ein Verhältnis | die Suchende | bei der Suche | `app/schemas/match.py` |
| **Exakter Abholpunkt** und Anzeigename der Mitfahrerin | die Fahrerin der angefragten Fahrt | **ab dem Moment der Anfrage** (Status `pending`), danach **dauerhaft auch bei `declined` und `cancelled`** (bis zum Stichtag oder zur Löschung des Kontos) | `GET /api/requests/incoming` (kein Statusfilter im Standard), `app/schemas/requests.py` |
| Anzeigename der Fahrerin, Abfahrtszeit, Plätze | die Mitfahrerin | in ihren ausgehenden Anfragen | `GET /api/requests/outgoing` |
| **E-Mail-Adresse des Gegenübers** | **nur** die beiden Beteiligten | **erst bei Status `accepted`**; bei `pending`, `declined`, `cancelled` ist sie `null`; nie in Suchergebnissen oder Listen Dritter | `counterpart_email`, `app/services/requests.py`, Tests `test_requests_privacy.py`, E2E 02 |
| exakter **Startpunkt** und vollständige Route einer Fahrt | die Fahrerin selbst; **für andere nicht ausgegeben**, aber eingrenzbar (Abschnitt 11, R-1) | – | `RideOut` nur in `/api/rides/mine` |

Diese Sichtbarkeiten gehören in die Information, die Nutzerinnen vor der Einwilligung erhalten (Abschnitt 12, Abgleich mit der Oberfläche).

## 6. Kontaktfreigabe-Mechanik

1. Eine Mitfahrerin setzt einen Abholpunkt und fragt bei einer Fahrt an. Der Server berechnet den Umweg neu (Clientwerte gibt es nicht) und lehnt zu große Umwege ab.
2. Die Fahrerin sieht die Anfrage (Name, exakter Abholpunkt, gerundeter Umweg) und nimmt sie an oder lehnt sie ab. Das Annehmen reduziert die freien Plätze atomar (Zeilensperre).
3. **Erst bei `accepted`** liefert die API dem jeweils anderen Beteiligten die E-Mail-Adresse. Wird die Anfrage danach storniert, ist sie wieder `null`.
4. Wer die Adresse einmal gesehen hat, kann sie behalten; die Anwendung kann das nicht zurücknehmen.

Die Freigabe setzt auf einer **nicht bestätigten** Adresse auf: Es kann sein, dass die Adresse nicht der Person gehört, mit der man sich verabredet (Abschnitt 11, R-3).

## 7. Empfänger, Auftragsverarbeiter und Dritte

| Empfänger | Was geht dorthin | Rolle (zu prüfen) | Vertrag |
|---|---|---|---|
| Hosting/Betreiber der Server (`[Hoster eintragen]`; im Teilstand ein Proxmox-Wirt mit Rand-Caddy) | alle Daten des Betriebs, Netzverkehr (IP-Adressen auf Netzebene) | Auftragsverarbeiter oder eigener Betrieb | AV-Vertrag nach Art. 28 DSGVO, falls ein externer Hoster beteiligt ist |
| **Kartenserver** (`TILE_URL`, `[Anbieter eintragen]`) | der **Browser** der Nutzerin sendet **IP-Adresse**, die **Kachelkoordinaten** des angezeigten Ausschnitts (damit die Gegend von Start- und Abholpunkt) und die Origin der Seite als Referer. Nicht: Konto-, Fahrt- oder Anfragedaten, die gesetzten Punkte selbst (die Zoomstufe bei der Punktwahl ist auf 16 begrenzt) | eigene Verantwortung des Kartenanbieters oder Auftragsverarbeiter, je nach Konstruktion | beim Anbieter klären; bei einem **eigenen** Kartenserver hinter der eigenen Origin entfällt die Weitergabe an Dritte |
| **Adressdienst** (nur bei `GEOCODER=nominatim`: Nominatim von OpenStreetMap, `GEOCODER_URL`) | der **Server** (nicht der Browser) sendet den **Suchtext der Nutzerin** (eine Adresse oder einen Ort) und seine eigene Adresse; keine Cookies, kein Referer, keine Nutzer-ID. Der Browser kontaktiert den Dienst nie. Der Suchtext wird bei uns weder gespeichert noch protokolliert; Treffer liegen bis zu 10 Minuten im Arbeitsspeicher, je Nutzer getrennt | eigene Verantwortung des Dienstbetreibers; bei einem **eigenen** Nominatim-Server entfällt die Weitergabe an Dritte | Nutzungsregeln des Dienstes (1 Anfrage/s, erkennbare Kennung) werden eingehalten; Datenschutzhinweise des Betreibers prüfen `[Rechtlich prüfen.]` |
| Zertifizierungsstelle (Let's Encrypt, nur bei Caddy mit öffentlicher Domain) | Domainname bei der Zertifikatsausstellung | kein Nutzerbezug | – |
| DNS-Anbieter (`[Anbieter eintragen]`) | Domainname | kein Nutzerbezug | – |
| Sicherungsdienst der Plattform (z. B. PBS auf dem Proxmox-Wirt) | Abbild des Gasts, **einschließlich der Datenbank** | Teil des Hostings | siehe Abschnitt 9 und 11 (R-6) |
| Andere Nutzer | siehe Abschnitt 5 | – | – |
| Geofabrik | nur der Betreiber lädt dort bei der Einrichtung die Kartendaten (PBF) herunter; keine Nutzerdaten | – | – |

Die Anwendung bindet **keine** Tracker, Analyse-Dienste, externen Schriften, CDNs oder Skripte Dritter ein. Das ausgelieferte Frontend wird mit `npm run check:external` auf externe URLs geprüft,
und ein E2E-Test (`04-extern-und-tastatur`) lässt einen Test fehlschlagen, sobald das Frontend einen anderen Host als den Kartenserver anspricht. (Ausnahme nur in Entwicklung: Die
Swagger-Oberfläche unter `/api/docs` lädt Skripte von `cdn.jsdelivr.net`; sie ist in Produktion abgeschaltet.)

**Drittlandtransfer.** Ob personenbezogene Daten in ein Drittland gelangen, hängt allein von den vom Betreiber gewählten Dienstleistern ab (Hoster, Kartenanbieter, DNS-Anbieter, Sicherungsdienst)
und lässt sich aus dem Code nicht beantworten. `[Betreiber prüfen und ggf. Garantien nach Kapitel V DSGVO eintragen, oder „kein Drittlandtransfer“]`. Insbesondere für den **Kartenserver** ist
Standort und Rechtsform des Betreibers zu klären; zum öffentlichen OSM-Server (nur für Entwicklung vorgesehen) wird hier bewusst nichts behauptet.

## 8. Rechtsgrundlagen

- **Einwilligung (Art. 6 Abs. 1 lit. a DSGVO).** Die Registrierung erfordert die ausdrückliche Zustimmung (`consent: true`, sonst 422; Checkbox mit Verweis auf die Datenschutzhinweise,
  `web/src/i18n/de.ts`, `register.consent`); der Zeitpunkt wird in `consent_at` gespeichert. Widerruf mit Wirkung für die Zukunft: Konto löschen (`DELETE /api/me`). Die Einwilligung ist nur
  wirksam, wenn die Nutzerin vorher **informiert** ist; dazu müssen die Sichtbarkeiten aus Abschnitt 5 in der Oberfläche stehen (derzeit unvollständig, Abschnitt 12).
- **Vertragsanbahnung (Art. 6 Abs. 1 lit. b)** kommt für die Vermittlung unter Nutzern *möglicherweise* zusätzlich in Betracht. `[Rechtlich prüfen.]`
- **Berechtigte Interessen (Art. 6 Abs. 1 lit. f)** für Missbrauchsschutz, Betriebslogs und Sicherungen; eine Interessenabwägung ist zu dokumentieren. `[Rechtlich prüfen.]`
- **Endgeräte-Zugriff (§ 25 TDDDG).** Es wird ein einziges, für die Anmeldung technisch notwendiges Cookie gesetzt; das Frontend nutzt weder `localStorage` noch `sessionStorage` noch
  IndexedDB (Suche in `web/src` ohne Treffer). Eine Einwilligung für diesen Zugriff ist voraussichtlich nicht erforderlich. `[Rechtlich prüfen.]`
- **Minderjährige.** Es gibt keine Altersabfrage. `[Prüfen, ob eine Altersgrenze oder ein Hinweis nötig ist (Art. 8 DSGVO, Einwilligungsfähigkeit).]`

## 9. Speicherdauer und Löschung

| Daten | Dauer | Mechanismus |
|---|---|---|
| Konto, Fahrten, Anfragen | **Selbstlöschung jederzeit**; spätestens zum Stichtag | `DELETE /api/me` (204): löscht Konto und per Fremdschlüssel (`ON DELETE CASCADE`) alle Fahrten und Anfragen des Kontos, **einschließlich fremder Anfragen auf eigene Fahrten**; Plätze angenommener Mitfahrten werden vorher zurückgegeben (`api/app/api/me.py`, `app/services/requests.py`) |
| Alle Nutzerdaten | **Stichtag: Ende des Tages `EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT` (UTC)**, Standard 14 Tage nach dem Veranstaltungsende | Dienst `cleanup`: löscht alle Anfragen, Fahrten und Konten; läuft beim Start und **alle 6 Stunden** (bei Fehler nach 10 min), idempotent. Die tatsächliche Löschung erfolgt also **bis zu 6 Stunden nach** dem Stichtag. Ausgabe nur Zählwerte (`api/app/cleanup.py`, Tests `test_cleanup.py`) |
| Stornierte Fahrten, abgelehnte/stornierte Anfragen | **bis zum Stichtag** bzw. zur Selbstlöschung | Das Stornieren setzt nur einen Status; exakte Koordinaten bleiben (Abschnitt 11, R-6) |
| Cookie `access_token` | `JWT_TTL_MINUTES`, Standard **720 min (12 h)** | Ablauf im Browser; **nicht widerrufbar**: Das Abmelden löscht nur das Cookie, ein kopiertes Token bliebe bis zum Ablauf gültig (Abschnitt 11, R-4) |
| Rate-Limit-Zähler | Fenster von Minuten bis einer Stunde | im Arbeitsspeicher; ein Neustart leert sie |
| Container-Logs | 3 Dateien à 10 MB je Dienst (Rotation) | enthalten keine IP, keinen Query-String, keine Koordinaten (Ausnahme: Datenbank, Abschnitt 11) |
| Backups | höchstens `BACKUP_KEEP_DAYS` (Standard 7 Tage, **kleiner** als die Retention); **ab dem Stichtag werden alle Backups gelöscht** | Skripte `scripts/backup.sh` (`make backup`, `make purge-backups`); setzt einen **Cron-Eintrag** voraus, der stündlich `make purge-backups` ausführt (nicht automatisch eingerichtet) |
| Sicherungen der Plattform (PBS auf dem Proxmox-Wirt) | im Teilstand **bis zu 6 Monate** | **nicht Teil dieser Anwendung**; widerspricht der Retention-Zusage, solange der Gast nicht ausgenommen ist (Abschnitt 11, R-6) |

**Physische Löschung.** Die Löschung erfolgt per SQL-`DELETE`. Die Zeilen verbleiben bis zu `VACUUM`/Überschreiben als tote Zeilen in den Datendateien und im Write-Ahead-Log der
Datenbank; sie sind nur mit Zugriff auf Datenträger oder Volume lesbar. Der Hinweis „endgültig gelöscht“ in der Oberfläche ist in diesem Sinn zu verstehen (Abschnitt 11, R-6).

**Nach dem Event** gehört der Stack gestoppt: Die API bleibt nach Fristablauf offen; neu angelegte Konten würde der nächste Cleanup-Lauf ohne Rückfrage löschen, oder (bei Daten, die mehr als einen
Tag nach der Frist entstanden sind) verweigert er das Löschen und beendet sich mit Exit-Code 3, bis der Betreiber es bestätigt (`CLEANUP_CONFIRM_EXPIRED=1`).

## 10. Betroffenenrechte

| Recht | Wie umgesetzt |
|---|---|
| Auskunft (Art. 15) | Nutzerinnen sehen ihre Daten in der Oberfläche (`GET /api/me`: E-Mail, Anzeigename, Anlagezeit; eigene Fahrten und Anfragen). **Es gibt keinen vollständigen Selbst-Export.** Weitergehende Auskunft: `[Prozess des Betreibers, z. B. Abfrage in der Datenbank anhand der Konto-ID, festlegen]` |
| Berichtigung (Art. 16) | **Es gibt keine Funktion, E-Mail, Anzeigename oder Passwort zu ändern** und keinen Passwort-Reset. Berichtigung nur durch Löschen und Neuanlegen des Kontos oder durch den Betreiber. `[Prozess festlegen]` |
| Löschung (Art. 17) | **Selbstlöschung** jederzeit in der Oberfläche („Konto löschen“) bzw. `DELETE /api/me`; automatische Löschung zum Stichtag. Grenzen: Abschnitt 9 (tote Zeilen, Sicherungen) |
| Einschränkung (Art. 18), Widerspruch (Art. 21) | über den Betreiber; es gibt keine technische Funktion. `[Prozess festlegen]` |
| Datenübertragbarkeit (Art. 20) | kein Export; über den Betreiber. `[Prozess festlegen]` |
| Widerruf der Einwilligung (Art. 7 Abs. 3) | durch Konto löschen; die Verarbeitung bis dahin bleibt rechtmäßig |
| Beschwerde (Art. 77) | bei der zuständigen Aufsichtsbehörde (Abschnitt 1) |

## 11. Technisch-organisatorische Maßnahmen und Restrisiken

**Maßnahmen** (Art. 32 DSGVO; Auszug, jeweils mit Beleg)

| Bereich | Maßnahme | Beleg |
|---|---|---|
| Datenminimierung | keine Klartext-Adressen, Kennzeichen, Telefonnummern im Schema; Umweg-Werte gerundet ausgegeben (0,5-km-Schritte, ganze Minuten); Anzeige-Route am Anfang um 1000 m gekürzt | Schema, `matching.py`, Tests `test_match_coarse_output.py`, `test_match_route_trim.py` |
| Vertraulichkeit der Kontakte | E-Mail erst bei `accepted`, nur für Beteiligte | Tests `test_requests_privacy.py`, E2E 02 (DOM und mitgeschnittene Antworten) |
| Passwörter | argon2id, Länge 10 bis 128 Zeichen; generische Login-Fehler, Zeitangleichung mit Dummy-Hash; Registrierung antwortet immer gleich (keine Konto-Aufzählung) | `app/auth/passwords.py`, `app/api/auth.py`, Tests `test_auth_*.py` |
| Sitzung | JWT (HS256) im Cookie `HttpOnly`, `SameSite=Strict`, `Secure` in Produktion; Origin-Prüfung bei Schreibzugriffen | `app/auth/tokens.py`, `app/middleware/security.py` |
| Zugriffskontrolle | Objekt-Autorisierung (fremde oder unbekannte Objekte → 404, kein Existenz-Orakel); Anmeldung für Suche, Fahrten und Anfragen | Vertrag, Tests |
| Missbrauchsschutz | Rate-Limits je IP, E-Mail und Nutzer; Obergrenze für offene Fahrten je Konto; Body-Limit 64 KB | `app/ratelimit.py`, `.env.example` |
| Transport | TLS und HSTS am Caddy bzw. Rand-Caddy; **innerhalb** von Docker bzw. des Gastnetzes unverschlüsselt (HTTP zur API, SQL zur Datenbank) | `caddy/Caddyfile`, `deploy/hel/` |
| Browser-Härtung | CSP (nur eigene Quellen + Kartenserver), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` | `caddy/Caddyfile`, Tests `test_security_caddy*.py` |
| Logs | Anwendungslog ohne IP, Query und Koordinaten; Uvicorn und nginx ohne Access-Log; Caddy ohne Access-Log, Betriebslog nur WARN ohne Request-Objekt, `handle_errors`; `osrm-routed -l WARNING` | `app/main.py`, `caddy/Caddyfile`, `docker-compose.yml`, Tests `test_security_logging.py`, `test_security_edge_logging.py`, `test_compose_config.py` |
| Backups | `pg_dump` direkt in `age` (nie unverschlüsselt auf der Platte), Dateirechte 0600, Aufbewahrung kürzer als die Retention, automatische Löschung zur Frist; privater Schlüssel nie auf dem Server | `scripts/backup.sh`, `scripts/restore.sh`, Tests `test_security_backup*.py` |
| Löschung | automatisch zum Stichtag, Schutz vor falschem Datum (Exit-Code 3), Selbstlöschung | `app/cleanup.py`, Tests `test_cleanup.py`, `test_me_account.py` |
| Container und Netz | `web`, `caddy` mit `read_only`, `cap_drop: ALL`; `api` als unprivilegierter Nutzer; Datenbank und OSRM nur auf `127.0.0.1` veröffentlicht; im Teilstand auf `hel` nftables-Regeln | `docker-compose.yml`, `api/Dockerfile`, `deploy/hel/nftables.conf` |
| Verschlüsselung ruhender Daten | **keine auf Anwendungsebene**; die Datenbankdateien liegen unverschlüsselt im Docker-Volume. Verschlüsselung von Platte/Volume ist Sache des Hosts. `[Betreiber prüfen]` | – |

**Restrisiken und bekannte Grenzen** (Auszug aus der Liste im [README](../README.md#bekannte-grenzen-und-offene-punkte), dort mit Nachweis-Kennzeichnung)

| Nr. | Restrisiko |
|---|---|
| R-1 (README D-1) | **Standort-Rückschluss auf den Fahrerstart.** Wer als angemeldetes, nicht verifiziertes Konto viele Abholpunkte abfragt, kann aus „Treffer ja/nein“ und den gerundeten Umweg-Werten den **Startpunkt einer Fahrt eingrenzen** (oft der Wohnort). Gemessen in einer Prüfung (Hamburg-Extrakt): 8 bis 15 Abfragen genügen, Fehler Median ≈ 200 m, Bestfall ≈ 20 bis 50 m, Einzelfälle bis ≈ 900 m. Die Kürzung der Anzeige-Route schützt die Anzeige, nicht gegen dieses gezielte Abtasten. Rat an Nutzerinnen: einen Startpunkt in der Nähe statt der Haustür wählen. Ob technisch nachgeschärft wird, ist eine offene Betreiberentscheidung |
| R-2 (D-2) | **Exakter Abholpunkt** und Anzeigename gehen bei jeder Anfrage an die Fahrerin, die ein beliebiger, nicht verifizierter Nutzer ist, und bleiben dort auch nach Ablehnen/Stornieren sichtbar |
| R-3 (S-1, S-2) | **Nicht bestätigte E-Mail-Adressen:** Wer eine fremde Adresse zuerst registriert, besetzt sie (Pre-Hijacking). Das Gegenüber kann bei der Kontaktfreigabe eine Adresse erhalten, die nicht der Person gehört, mit der es sich verabredet hat. Scheinkonten und Scheinfahrten sind möglich |
| R-4 (S-3, S-4) | Kein Passwort-Reset und keine Änderung von E-Mail/Name/Passwort; **JWT nicht widerrufbar** (bis zu 12 h nach dem Abmelden, auch für `GET /api/me` und `DELETE /api/me`) |
| R-5 (D-5, D-6) | **Logs:** Das Log des Datenbank-Containers ist nicht auf Personenbezug ausgelegt: Bei Constraint-Verletzungen schreibt PostgreSQL Zeileninhalt (`Failing row contains …`, mit Geometrie als Hex) und die SQL-Anweisung; im Normalbetrieb sollte das nicht vorkommen (in einem Testlauf bei absichtlich ausgelösten Verletzungen beobachtet). Bereits geschriebene Logs und Sicherungen des Wirts können Koordinaten aus der Zeit vor den Log-Fixes enthalten |
| R-6 (D-3, D-4, B-1) | **Löschung:** Stornierte Fahrten und abgelehnte Anfragen behalten exakte Koordinaten bis zum Stichtag; gelöschte Zeilen bleiben bis `VACUUM`/Überschreiben auf dem Datenträger; Sicherungen der Plattform können länger aufbewahrt werden als die Retention |
| R-7 (D-7) | **Kartenserver** erfährt IP, Ausschnitt (Gegend von Start-/Abholpunkt) und Origin |
| R-8 | **Zeitzone:** Abfahrtszeiten werden in der Browser-Zeit ein- und ausgegeben, ohne Hinweis (kein Datenschutz-, aber ein Verständnisrisiko) |

## 12. Abgleich mit der Oberfläche (zu erledigen)

Die Datenschutzerklärung in der Oberfläche (`web/src/i18n/de.ts`, `privacy.*` und `home.privacy*`) ist ein Platzhalter-Entwurf. Beim Vergleich mit Abschnitt 5 und 11 fällt auf, was **fehlt oder zu
knapp ist**. Die Oberfläche wurde in dieser Runde **nicht** geändert:

| Stelle | Heute | Zu ergänzen |
|---|---|---|
| `privacy.s4` „Weitergabe von Kontaktdaten“ und `home.privacy3` | nennt nur die E-Mail-Adresse | dass die Fahrerin bei jeder Anfrage Anzeigename und **exakten Abholpunkt** sieht (auch nach Ablehnen/Stornieren), und dass **Fahrten** (Anzeigename, Abfahrt, Plätze, gekürzte Route) für alle angemeldeten Nutzer sichtbar sind |
| `privacy.s9` „Bekannte Grenzen“ | nur: E-Mail-Adressen werden nicht bestätigt | Abtasten des Fahrerstarts (R-1), nicht widerrufbare Sitzung (R-4), fehlender Passwort-Reset, Logs/Sicherungen (R-5, R-6) |
| `privacy.s5` „Speicherdauer“ | „Sicherungskopien werden verschlüsselt und nur kurz aufbewahrt“ | tatsächliche Dauern; Hinweis, dass die Löschung bis zu 6 h nach dem Stichtag erfolgt und dass Plattform-Sicherungen länger bestehen können; „endgültig gelöscht“ differenzieren (Abschnitt 9) |
| `privacy.s3` „Zweck und Rechtsgrundlage“ | nur Einwilligung | ggf. lit. b/lit. f (Abschnitt 8) nach rechtlicher Prüfung |
| `privacy.s7geo` „Adresssuche“ | **ergänzt** (erscheint nur bei `GEOCODER=nominatim`): Suchtext geht über unseren Server an Nominatim; nicht gespeichert | rechtlich prüfen; Abschnitt 7 ist maßgeblich |
| `privacy.s8` „Deine Rechte“ | verweist auf den Verantwortlichen | ehrlich benennen: kein Selbst-Export, keine Änderungsfunktion; Ablauf für Anfragen |
| `privacy.s1`, `s8`, `imprint.*` | Platzhalter | Verantwortlicher, Kontakt, Aufsichtsbehörde, Anbieterangaben nach § 5 DDG |

## 13. Offene Punkte für den Betreiber

Diese Punkte kann der Code nicht abnehmen; sie sind vor dem Echtbetrieb zu klären.

1. **Verantwortlicher und Kontakt** eintragen (Abschnitt 1, Oberfläche `privacy.s1`, `imprint.*`).
2. **Datenschutzbeauftragte(r):** prüfen, ob eine Benennung erforderlich ist (Art. 37 DSGVO, § 38 BDSG), und das Ergebnis dokumentieren.
3. **Verzeichnis von Verarbeitungstätigkeiten** (Art. 30 DSGVO) anlegen; Abschnitt 3 kann als Grundlage dienen.
4. **Datenschutz-Folgenabschätzung prüfen** (Art. 35 DSGVO): Es werden Standortdaten (Start- und Abholpunkte, oft Wohnorte) und Routen verarbeitet; ob eine DSFA erforderlich ist, ist
   zu bewerten und die Entscheidung zu dokumentieren.
5. **Auftragsverarbeitungsverträge** (Art. 28) mit Hoster, Kartenanbieter, ggf. Sicherungsdienst und DNS-Anbieter prüfen; Drittlandtransfer (Abschnitt 7) klären.
6. **Kartenserver:** Auf `hel` läuft der Kachel-Proxy (`TILE_URL=/tiles/...`, Oberfläche zeigt `privacy.s7proxy`): OSM sieht nur die Serveradresse. Prüfen, ob die leichte Nutzung nach der OSM-Policy für die erwartete Nutzerzahl reicht; sonst eigener Server oder Anbieter und `TILE_URL`/`TILE_ATTRIBUTION` setzen (Abschnitt 7 prüfen).
7. **Sicherungen der Plattform** (PBS auf `hel`, Entscheidung offen) mit der Retention in Einklang bringen; **Backup-Cron** einrichten oder bewusst darauf verzichten; Stichtag und `BACKUP_KEEP_DAYS` abstimmen.
8. **Log-Härtung der Datenbank** entscheiden (R-5) und die Ausrollen-Schritte nach den Log-Fixes durchführen (OSRM-Container neu erstellen, Rand-Caddy-Block ersetzen, bereits geschriebene Logs und Sicherungen bewerten).
9. **E-Mail-Verifikation** (benötigt SMTP) und die Härtung gegen Abtasten (Abschnitt 11, R-1) als Entscheidung dokumentieren: umsetzen oder bewusst hinnehmen.
10. **Oberfläche angleichen** (Abschnitt 12), danach die Einwilligungsformulierung prüfen.
11. **Prozesse für Betroffenenanfragen** (Auskunft, Berichtigung, Einschränkung, Export) festlegen, da die Anwendung dafür keine Selbstbedienung bietet; **Meldeprozess** für Datenpannen (Art. 33/34) festlegen.
12. **Impressum** (§ 5 DDG) vollständig ausfüllen; Hinweis auf die Rechtsprüfung dieses Entwurfs.
13. **Löschung nach dem Event:** Stack nach Fristablauf stoppen; vor Änderungen von `EVENT_END_DATE` `make cleanup DRY=1` ausführen.
14. **Verschlüsselung ruhender Daten** (Platte/Volume) des Hosts bewerten.
