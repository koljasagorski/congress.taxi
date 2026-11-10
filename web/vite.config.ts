/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Entwickler-Proxy: /api -> lokale API (uvicorn auf 127.0.0.1:8000).
// Im Betrieb übernimmt Caddy diese Weiterleitung (siehe docs/API-CONTRACT.md).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  build: {
    target: "es2022",
    sourcemap: false,
    // Nichts als data:-URI einbetten: Bilder (Leaflet-Marker, Layer-Symbole) bleiben Dateien unter
    // /assets/. Damit genügt in der CSP (Phase 7) `img-src 'self' <Tile-Host>` ohne `data:`.
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
