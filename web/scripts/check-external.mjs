#!/usr/bin/env node
// Belegt, dass das gebaute web/dist keine absoluten externen URLs enthält – außer den unten
// einzeln begründeten Ausnahmen. Jede Ausnahme ist ein Muster mit Begründung; alles andere
// lässt den Check scheitern (Exit-Code 1).
//
// Aufruf: npm run build && npm run check:external
//
// Hinweis zum Umfang: Geprüft wird der statische Text der Auslieferung. Die einzige Verbindung
// zu einem Fremdserver zur Laufzeit ist der Kartenserver, dessen URL erst zur Laufzeit aus
// GET /api/config kommt (nicht im Bundle), ebenso die Quellenangabe der Karte (map.attribution) samt Link.
// Das ist so gewollt und steht in der Datenschutzerklärung.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");

/**
 * Erlaubte Fundstellen. `scope` begrenzt, in welchen Dateitypen die Ausnahme gilt.
 * Jede Ausnahme ist ein reiner Text im Bundle, der NICHT automatisch geladen wird.
 */
const ALLOWED = [
  {
    pattern: /^https?:\/\/www\.w3\.org\/(1998\/Math\/MathML|1999\/(xhtml|xlink)|2000\/(svg|xmlns\/?)|XML\/1998\/namespace)$/,
    scope: ["js", "svg", "html"],
    reason:
      "XML-Namespace-Bezeichner (z. B. SVG/XHTML). Das sind Identifikatoren für createElementNS/xmlns, " +
      "keine Adressen, die der Browser abruft.",
  },
  {
    pattern: /^https:\/\/leafletjs\.com\/?$/,
    scope: ["js"],
    reason:
      "Leaflets Standard-Präfix der Attributionsleiste (<a href>). Reiner Link, den die Nutzerin oder " +
      "der Nutzer anklicken kann; wird nicht automatisch geladen. Die Lizenz verlangt die Namensnennung nicht " +
      "zwingend, wir behalten sie aber bewusst bei.",
  },
  {
    pattern: /^http:\/\/localhost$/,
    scope: ["js"],
    reason:
      "Platzhalter-Basis für das Parsen relativer Pfade in react-router (new URL(pfad, 'http://localhost')). " +
      "Es wird kein Request an localhost gesendet; der Wert dient nur als Parser-Basis.",
  },
  {
    pattern: /^https:\/\/github\.com\/ungap\/url-search-params$/,
    scope: ["js"],
    reason:
      "Hinweistext in einer react-router-Warnung (Polyfill-Empfehlung für Internet Explorer 11). " +
      "Nur Teil einer Meldungs-Zeichenkette, wird nie aufgerufen.",
  },
  {
    pattern: /^https:\/\/react\.dev\/errors?(\/.*)?$/,
    scope: ["js"],
    reason:
      "React-Fehlertexte in der Produktionsversion verweisen auf die Dokumentation (nur Text einer " +
      "Fehlermeldung, die React selbst nie abruft).",
  },
  {
    pattern: /^https:\/\/(reactjs\.org|react\.dev|reactrouter\.com)(\/.*)?$/,
    scope: ["js"],
    reason:
      "Dokumentationslinks innerhalb von Warn-/Fehlermeldungen von React bzw. react-router " +
      "(nur Zeichenketten in Meldungen; es erfolgt kein Request).",
  },
];

const TEXT_EXT = new Map([
  [".html", "html"],
  [".js", "js"],
  [".mjs", "js"],
  [".css", "css"],
  [".svg", "svg"],
  [".json", "json"],
  [".webmanifest", "json"],
  [".txt", "txt"],
  [".map", "json"],
]);

// Absolute URLs (inklusive WebSocket/FTP). Endet an Leerraum, Anführungszeichen, Klammern und Backslash.
const ABS_URL = /\b(?:https?|wss?|ftp):\/\/[^\s"'`<>()\\]+/gi;
// Protokollrelative Ziele in Attributen und CSS: src="//host/…", url(//host/…), @import "//…"
const PROTO_REL = /(?:\b(?:src|href|action|poster|data|srcset)\s*=\s*["']?\s*|url\(\s*["']?\s*|@import\s+["']?)\/\/[a-z0-9]/gi;

// Eingebettete Bilder (data:-URIs) würden unter der CSP `img-src 'self' <Tile-Host>` (ohne data:) blockiert.
// Erlaubt ist nur Leaflets leeres 1x1-GIF (Util.emptyImageUrl, nur beim Abbrechen eines Kachelabrufs).
const EMPTY_GIF = "data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=";
const DATA_IMAGE = /data:image\/[a-z0-9.+-]+[;,][^"'`)\s]*/gi;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) yield* walk(full);
    else yield full;
  }
}

function trimUrl(url) {
  // Satzzeichen und JS-Syntax am Ende gehören nicht zur URL
  return url.replace(/[.,;:!?}\]]+$/, "");
}

let exists = true;
try {
  statSync(dist);
} catch {
  exists = false;
}
if (!exists) {
  console.error("web/dist fehlt. Zuerst 'npm run build' ausführen.");
  process.exit(1);
}

const violations = [];
const allowedHits = new Map(); // reason -> {count, urls:Set, files:Set}
let filesScanned = 0;

for (const file of walk(dist)) {
  const kind = TEXT_EXT.get(extname(file).toLowerCase());
  if (!kind) continue; // Binärdateien (Bilder, Schriften) enthalten keine auswertbaren URLs
  filesScanned += 1;
  const rel = relative(root, file);
  const text = readFileSync(file, "utf8");

  for (const match of text.matchAll(ABS_URL)) {
    const url = trimUrl(match[0]);
    const rule = ALLOWED.find((a) => a.scope.includes(kind) && a.pattern.test(url));
    if (rule) {
      const entry = allowedHits.get(rule.reason) ?? { count: 0, urls: new Set(), files: new Set(), rule };
      entry.count += 1;
      entry.urls.add(url);
      entry.files.add(rel);
      allowedHits.set(rule.reason, entry);
    } else {
      violations.push(`${rel}: ${url}`);
    }
  }
  for (const match of text.matchAll(DATA_IMAGE)) {
    if (match[0] !== EMPTY_GIF) violations.push(`${rel}: eingebettetes Bild (${match[0].slice(0, 30)}…), CSP erlaubt kein data:`);
  }
  for (const match of text.matchAll(PROTO_REL)) {
    violations.push(`${rel}: protokollrelative URL (${match[0].trim()}…)`);
  }
}

console.log(`check:external – ${filesScanned} Textdateien in web/dist geprüft.`);
for (const { count, urls, files } of allowedHits.values()) {
  const rule = ALLOWED.find((a) => [...urls].some((u) => a.pattern.test(u)));
  console.log(`  erlaubt (${count}x in ${[...files].join(", ")}):`);
  for (const url of urls) console.log(`    - ${url}`);
  if (rule) console.log(`    Begründung: ${rule.reason}`);
}

if (violations.length > 0) {
  console.error("\nGEFUNDEN: nicht erlaubte externe URLs");
  for (const v of [...new Set(violations)]) console.error(`  - ${v}`);
  console.error(
    "\nEntweder die Abhängigkeit entfernen oder – nur wenn es sich um reinen Text/Attribution handelt – " +
      "die Ausnahme mit Begründung in scripts/check-external.mjs ergänzen.",
  );
  process.exit(1);
}
console.log("OK: keine externen URLs außer den begründeten Ausnahmen.");
