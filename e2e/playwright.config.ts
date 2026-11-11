import { defineConfig, devices } from "@playwright/test";

// Ziel ist ein laufender Stack (Caddy -> SPA + API -> PostGIS + OSRM), siehe e2e/stack.sh / make e2e.
const baseURL = process.env["BASE_URL"] ?? "http://127.0.0.1:18080";
const inCI = process.env["CI"] !== undefined && process.env["CI"] !== "";

export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  // Jeder Test legt eigene Konten mit eindeutigen Namen an und räumt sie wieder auf.
  fullyParallel: true,
  workers: inCI ? 2 : undefined,
  forbidOnly: inCI,
  // Keine Wiederholungen: ein rotes Ergebnis soll nie durch einen zweiten Versuch verdeckt werden.
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL,
    locale: "de-DE",
    timezoneId: "Europe/Berlin",
    viewport: { width: 1280, height: 900 },
    // schaltet die Leaflet-Animationen ab (siehe MapView), damit nichts "nachläuft"
    reducedMotion: "reduce",
    actionTimeout: 10_000,
    navigationTimeout: 20_000,
    // Spuren und Bilder nur bei Fehlern (CI-Artefakt)
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
