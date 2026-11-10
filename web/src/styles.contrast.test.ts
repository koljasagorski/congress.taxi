// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// Rechnerische Prüfung der Farb-Tokens (WCAG 2.2). jsdom kann Kontraste nicht messen, deshalb
// werden die Tokens direkt aus styles.css gelesen.
const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z-]+):\s*(#[0-9a-fA-F]{6})\b/g)) out[m[1] as string] = (m[2] as string).toLowerCase();
  return out;
}

function lightBlock(): string {
  const start = css.indexOf(":root {");
  return css.slice(start, css.indexOf("}", start));
}

function darkBlock(): string {
  const media = css.indexOf("@media (prefers-color-scheme: dark)");
  const start = css.indexOf(":root {", media);
  return css.slice(start, css.indexOf("}", start));
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const themes = {
  hell: { ...tokens(lightBlock()) },
  dunkel: { ...tokens(lightBlock()), ...tokens(darkBlock()) },
};

/** [Vordergrund, Hintergrund, Mindestkontrast, Begründung] */
const PAIRS: [string, string, number, string][] = [
  ["fg", "bg", 4.5, "Fließtext"],
  ["fg", "bg-alt", 4.5, "Text auf Karten"],
  ["muted", "bg", 4.5, "Hilfetext"],
  ["muted", "bg-alt", 4.5, "Hilfetext auf Karten"],
  ["primary", "bg", 4.5, "Links"],
  ["primary-fg", "primary", 4.5, "Text auf Schaltflächen"],
  ["danger", "bg", 4.5, "Fehlertext"],
  ["success-fg", "success-bg", 4.5, "Erfolgsmeldung"],
  ["error-fg", "error-bg", 4.5, "Fehlermeldung"],
  ["info-fg", "info-bg", 4.5, "Infomeldung"],
  ["focus", "bg", 3, "Fokusrahmen (1.4.11)"],
  ["border-ui", "bg", 3, "Rahmen von Eingabefeldern und Karte (1.4.11)"],
  ["border-ui", "bg-alt", 3, "Rahmen auf Kartenflächen (1.4.11)"],
];

describe("Farbkontraste der Design-Tokens", () => {
  for (const [name, tokensOfTheme] of Object.entries(themes)) {
    for (const [fg, bg, min, why] of PAIRS) {
      it(`${name}: ${fg} auf ${bg} hat mindestens ${min}:1 (${why})`, () => {
        const a = tokensOfTheme[fg];
        const b = tokensOfTheme[bg];
        expect(a, `Token --${fg} fehlt`).toBeDefined();
        expect(b, `Token --${bg} fehlt`).toBeDefined();
        expect(contrast(a as string, b as string)).toBeGreaterThanOrEqual(min);
      });
    }
  }
});
