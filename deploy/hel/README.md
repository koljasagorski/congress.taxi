# Betrieb auf dem Proxmox-Wirt `hel` (congress.sgr.ski)

Stand **2026-11-10**: Teilstand (Phase 1: db + osrm + api, **Hamburg**-Extrakt) läuft in **CT 220 `congress`**.
Der Wirt und der Rand gehören zur Infrastruktur im Repository `patchletter.com` (`deploy/hel/`, dort
`README.md` und `sgr/README.md`) – **dort nachlesen, nicht raten**. Keine Zugangsdaten, Schlüssel oder
`.env`-Inhalte in dieses Repo oder in Chat-Protokolle.

```
Internet ─▶ sgr (CT 180) 65.21.92.102 / [2a01:4f9:3b:44ad::126] :443   Caddy, TLS (Let's Encrypt)
              └─ congress.sgr.ski ─▶ 10.10.10.80:8000   (CT 220 congress, Docker Compose)
                                       ├─ api  (FastAPI)           10.10.10.80:8000   ← nur von sgr erreichbar
                                       ├─ db   (PostGIS 16)        127.0.0.1:55432
                                       └─ osrm (v26.4.0, Hamburg)  127.0.0.1:55000
```

## Der Gast

| | |
|---|---|
| Gast | LXC 220 `congress`, unprivilegiert, `nesting=1,keyctl=1`, Debian 13, `onboot` |
| Ressourcen | 2 Kerne, **4 GB RAM-Kappe** (keine Reservierung), **kein Swap**, 24 GB `local-lvm` (online mit `pct resize` vergrößerbar) |
| Netz | nur `vmbr1`: `10.10.10.80/24` (GW `10.10.10.1`), `2a01:4f9:3b:44ad:1::80/80`; keine öffentliche Adresse |
| SSH | nur vom Wirt (`10.10.10.1`): `ssh -J hel -i ~/.ssh/patchletter_proxmox root@10.10.10.80`; Host-Key ED25519 `SHA256:vpy9DCWF0Z+lkjc6E9U+KyIx4xcZiY6UD/ccgENkl2g`; Notweg `ssh hel pct enter 220` |
| Firewall | `nftables.conf` (Tabelle `inet gast`): 22 nur vom Wirt, 8000 nur von sgr (`10.10.10.70`), **forward-Regel** gegen das Umgehen durch Docker-DNAT |
| Code | `/opt/congress` (per `git archive` übertragen, kein Git/Token auf dem Server); `/opt/congress/.deployed-rev` = übertragener Commit |
| Konfiguration | `/opt/congress/.env` (Rechte 600, Secrets zufällig auf dem Gast erzeugt, **nie ausgeben**) |

**Warum so klein?** Der Wirt ist überbucht (am 2026-11-10: 81 920 MB RAM zugesagt bei 64 GB; real ≈ 30 GB belegt,
≈ 34 GB verfügbar) und trägt Produktion von patchletter und den Mailserver. Der Hamburg-Teilstand braucht im Betrieb
≈ 0,2–2 GB. **Deutschland-Extrakt: nicht ausgeführt.** Die Vorbereitung (`osrm-extract`) braucht laut Extrapolation
≈ 20 GB Spitzen-RAM (ungeprüft) – das gehört nicht auf diesen Wirt, sondern auf eine stärkere Maschine
(Ergebnis `data/osrm/` dann kopieren) oder erst nach Absprache und Messung.

## Anlegen (reproduzierbar)

Auf dem Mac, Stand der Repos ausgecheckt (`congress.taxi` und `patchletter.com` nebeneinander):

```sh
S=$(mktemp -d) && cp ../patchletter.com/deploy/hel/gast-grundlage.sh deploy/hel/{nftables.conf,gast-anlegen.sh,gast-fertig.sh} "$S"/
tar -C "$S" -cf - . | ssh hel 'install -d -m 700 /root/congress-setup && tar -xf - -C /root/congress-setup && bash /root/congress-setup/gast-anlegen.sh'
ssh hel 'bash /root/congress-setup/gast-fertig.sh'      # wartet nicht: erst nach „FERTIG GRUNDLAGE“ im Gast-Protokoll
```

`gast-anlegen.sh` bricht ab, wenn ID 220 oder `10.10.10.80` belegt ist. Danach Code ausrollen:

```sh
git archive --format=tar HEAD | ssh -J hel -i ~/.ssh/patchletter_proxmox root@10.10.10.80 \
  'mkdir -p /opt/congress && tar -xf - -C /opt/congress && cd /opt/congress && sh deploy/hel/ausrollen.sh'
```

`ausrollen.sh` ist idempotent: erzeugt `.env` nur, wenn sie fehlt, setzt `APP_ENV=production`, `OSRM_PBF_URL`
(Hamburg), `API_BIND=10.10.10.80`, `DOMAIN`, `TRUSTED_PROXIES=10.10.10.70`, bereitet OSRM nur bei fehlenden Daten vor
und startet den Stack. Es gibt nie `.env` aus. **Immer nur einen CI-geprüften Commit ausrollen.**

### Reverse-Proxy-Vertrauen und Rate-Limits (Phase 7)

Auf `hel` läuft **kein eigener Caddy** im Gast: das Compose-Profil `proxy` und `docker-compose.proxy.yml` bleiben
aus (`COMPOSE_PROFILES`/`COMPOSE_FILE` in `.env` nicht setzen). Der Rand-Caddy auf CT 180 (`10.10.10.70`) spricht die
API direkt an und setzt `X-Forwarded-For` (`header_up X-Forwarded-For {remote_host}`, überschreibt gefälschte Werte).
Damit das Rate-Limit je Client zählt, muss die API genau diesem einen Proxy vertrauen:

```
TRUSTED_PROXIES=10.10.10.70      # nur diese Adresse (/32), nie ein ganzes Netz wie 10.10.10.0/24
APP_ENV=production               # schaltet /api/docs, /api/openapi.json und ReDoc in der API aus (404)
```

`ausrollen.sh` setzt beides (`EDGE_PROXY`, Standard `10.10.10.70`). Ein zu weiter Wert (z. B. das Gastnetz) würde jedem
Container dort erlauben, seine Client-Adresse zu fälschen. Ohne den Wert zählen **alle** Besucher als ein Client (die
API warnt einmal im Log). Die API-Port-Bindung (`API_BIND=10.10.10.80`) bleibt, weil der Rand-Caddy sie von außerhalb
des Gasts erreichen muss; die Firewall-Regel in `nftables.conf` begrenzt sie auf `10.10.10.70`.

## Rand-Caddy (CT 180 `sgr`)

Der Block steht in `deploy/hel/sgr-caddy-block.Caddyfile` und wurde ans Ende von
`patchletter.com/deploy/hel/sgr/Caddyfile` gehängt (Quelle der Wahrheit für den Rand). Einspielen nach dem
dortigen Muster: Sicherung → Datei neben die alte legen → `caddy validate` → `caddy reload` (kein Neustart; `m.` und
`dns.` bleiben unterbrechungsfrei). Sicherung dieser Änderung auf CT 180: `/opt/sgr/caddy/etc/Caddyfile.vor-congress`.

Im **Teilstand** reicht der Rand nur `/api/health` durch; `/`, `/api/docs`, `/api/openapi.json` und
`/api/health/ready` liefern `503 „im Aufbau“` (die API selbst antwortet auf die ersten beiden in `production` ohnehin
mit 404; `/api/health/ready` bleibt auch beim Öffnen des Rands von außen zu). Das wird mit Phase 6/7 geöffnet (Frontend, Security-Header,
Rate-Limits).

**Beim nächsten Ausrollen (Sicherheitsfix Login-Limits, auf `hel` noch nichts verändert):** Der Schlüssel
`RATE_LIMIT_LOGIN_PER_EMAIL_PER_MIN` heißt jetzt `RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_IP_PER_MIN` (5) und
`RATE_LIMIT_LOGIN_FAIL_PER_EMAIL_PER_MIN` (30); ein alter Eintrag in der `.env` auf CT 220 wird still ignoriert
(Standardwerte gelten) und kann entfernt werden. Details: `PLAN.md` Annahme 9.

**Frontend freischalten (Phase 6, noch nicht ausgeführt):** (1) `make up` baut seit Phase 6 auch den Container `web` (Node-Build, nginx, Port 8080,
standardmäßig nur `127.0.0.1`); für den Rand `WEB_BIND=10.10.10.80` in `.env` setzen. (2) In `nftables.conf` Port 8080 wie 8000 nur für
`10.10.10.70` öffnen (Eingabekette und `forward`-Regel, sonst umgehen veröffentlichte Docker-Ports die Kette). (3) Im Rand-Block
`caddy/edge-hardening.Caddyfile` einbinden (Referrer-Policy je Zweig: SPA `strict-origin-when-cross-origin`, `/api` `no-referrer`,
CSP-`img-src` passend zu `TILE_URL`) und `reverse_proxy 10.10.10.80:8080` für alles außer `/api`. (4) Vor dem Livegang `TILE_URL` und
`TILE_ATTRIBUTION` auf einen für den Betrieb zulässigen Kartenserver setzen (OSM-Standard-Tiles nur Entwicklung) sowie die Platzhalter in
Datenschutzerklärung/Impressum (`web/src/i18n/de.ts`) ausfüllen. Der Rand hat keine `log`-Direktive (kein Access-Log) **und** einen `handle_errors`-Block (Snippet `congress_error_pages`): Ohne ihn schreibt Caddy bei jedem 502 einen Fehlereintrag mit IP, `X-Forwarded-For` und Query-String (bei `/api/match`: Koordinaten) ins Log. Gilt auch für `m.`/`dns.` im selben Rand-Caddyfile (Repo `patchletter.com`, dort nicht geändert).

## DNS (Cloudflare, Zone `sgr.ski`, „DNS only“, TTL 60)

`congress` → A `65.21.92.102`, AAAA `2a01:4f9:3b:44ad::126` (dieselben Werte wie `m` und `dns`, live gelesen).
Angelegt über den Cloudflare-MCP am 2026-11-10.

## Belege (2026-11-10, ausgeführt)

| Prüfung | Ergebnis |
|---|---|
| CI für den ausgerollten Commit `97687a9` | grün (alle Jobs) |
| Gast: Docker, Stack | `hello-world` ok; db, osrm, api **healthy**; `docker ps` zeigt `10.10.10.80:8000->8000` |
| Firewall-Nachweis | von sgr → `10.10.10.80:8000/api/health` **200**, `/ready` **200**; von **hel** und von **pl** → Zeitüberschreitung (curl-Exit 28) |
| Neustart `pct reboot 220` | alle drei Container kamen ohne Eingriff **healthy** zurück, `/ready` 200 nach 6 s, Firewall-Regel geladen |
| Zertifikat | Let's Encrypt (YE1) für `congress.sgr.ski`, gültig bis 2027-02-08; HTTP → HTTPS 308 |
| Öffentlich | `https://congress.sgr.ski/api/health` → 200 `{"status":"ok"}`; `/`, `/api/docs`, `/api/openapi.json`, `/api/health/ready` → 503 |
| IPv4 und IPv6 | `congress`, `m`, `dns` antworten über beides (IPv6 von `pl` aus geprüft; der Mac hat kein IPv6) |
| Bestehende Namen | `m.sgr.ski` 302 / Matrix-`versions` ok, `dns.sgr.ski` 200 – nach dem Reload unverändert |
| Ressourcen danach | Gast: 233 MB RAM belegt, Platte 3,2 von 24 GB |

**Nicht geprüft:** Deutschland-Extrakt; Last/Dauerbetrieb; `/api/health/ready` über den Rand (bewusst zu).

## Offene Entscheidungen für den Betreiber

1. **Sicherung (PBS).** Neue Gäste landen automatisch im Job `taeglich` von `hel` (`all 1`, nur `exclude 210`),
   also im PBS von patchletter mit bis zu 6 Monaten Aufbewahrung. Heute enthält die Datenbank **keine
   Nutzerdaten**, deshalb ist das unkritisch. **Vor dem Live-Gang von Phase 2** entscheiden: Die App verspricht
   Backups mit kürzerer Aufbewahrung als die Retention der Nutzerdaten. Ausnehmen geht nur so, dass die bestehende
   Liste erhalten bleibt: `pvesh set /cluster/backup/taeglich --exclude 210,220` (ein Befehl **ersetzt** die Liste –
   fehlt die 210, landet Kali im PBS). Eigene Sicherung der App: `pg_dump` + age (Phase 7).
2. **Wächter.** `hel-wache` überwacht nur eigene Gäste (`MUSS_LAUFEN`); CT 220 ist dort **nicht** eingetragen.
3. **Deutschland-Extrakt** und danach mehr RAM/Platte (`pct set 220 --memory …`, `pct resize 220 rootfs +…G`).

## Nach Fristablauf (Event vorbei)

Der Cleanup in CT 220 löscht alle Nutzerdaten am Ende des Tages `EVENT_END_DATE + RETENTION_DAYS_AFTER_EVENT`
(UTC). Die API bleibt danach **offen**: Neue Konten würden vom nächsten Lauf (bis 6 h später) ohne Rückfrage entfernt.
Deshalb nach Fristablauf den Gast stoppen (`ssh hel 'pct stop 220'`) und mit dem Rückbau unten fortfahren.
Vor jeder Änderung von `EVENT_END_DATE`/`RETENTION_DAYS_AFTER_EVENT` auf hel `make cleanup DRY=1` bzw.
`python -m app.cleanup --dry-run` im Gast laufen lassen. Bei falschem Datum (Daten, die >1 Tag nach Fristablauf
entstanden) verweigert der Cleanup (Exit-Code 3, Container `unhealthy`), statt zu löschen; `CLEANUP_CONFIRM_EXPIRED=1`
erzwingt das Löschen bewusst.

## Rückbau (vollständig, in dieser Reihenfolge)

1. DNS: A- und AAAA-Eintrag `congress` bei Cloudflare löschen.
2. Rand: `ssh hel "pct exec 180 -- cp /opt/sgr/caddy/etc/Caddyfile.vor-congress /opt/sgr/caddy/etc/Caddyfile"`, dann
   `caddy reload` im Container `sgr-caddy-1` (`--config /etc/caddy/Caddyfile --adapter caddyfile`); danach `m.`/`dns.` prüfen.
3. Gast: `ssh hel 'pct stop 220 && pct destroy 220 --purge'`.
4. Im Repo `patchletter.com` den Block aus `deploy/hel/sgr/Caddyfile` wieder entfernen.
