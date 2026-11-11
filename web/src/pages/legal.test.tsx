import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { baseRoutes, CONFIG, mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

describe("Rechtstexte", () => {
  it("Impressum nennt Anbieter, Register und Kontakt ohne Platzhalter", async () => {
    mockApi(baseRoutes());
    renderApp("/impressum");
    await screen.findByRole("heading", { level: 1, name: "Impressum" });
    const text = document.body.textContent ?? "";
    expect(text).toContain("patchletter UG (haftungsbeschränkt)");
    expect(text).toContain("HRB 20118");
    expect(text).toContain("hello@patchletter.com");
    expect(text).not.toMatch(/\[[^\]]+eintragen\]|\[Vor- und Nachname/);
  });

  it("Datenschutz nennt bei externem Kartenserver dessen Host", async () => {
    mockApi(baseRoutes());
    renderApp("/datenschutz");
    await screen.findByRole("heading", { level: 1, name: "Datenschutzerklärung" });
    const text = document.body.textContent ?? "";
    expect(text).toContain("tiles.example.test");
    expect(text).not.toContain("über den Server dieser Website");
    expect(text).not.toMatch(/\[[^\]]+\]/);
  });

  it("Datenschutz nennt die Adresssuche nur, wenn sie aktiv ist", async () => {
    mockApi(baseRoutes());
    const { unmount } = renderApp("/datenschutz");
    await screen.findByRole("heading", { level: 1, name: "Datenschutzerklärung" });
    expect(screen.queryByRole("heading", { name: "Adresssuche" })).not.toBeInTheDocument();
    unmount();

    mockApi({
      ...baseRoutes(),
      "GET /api/config": { body: { ...CONFIG, map: { ...CONFIG.map, geocoder: "nominatim" } } },
    });
    renderApp("/datenschutz");
    expect(await screen.findByRole("heading", { name: "Adresssuche" })).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).toContain("nicht an einen Dritten");
    expect(text).toContain("Gespeichert wird später nur der gewählte Kartenpunkt");
    expect(text).not.toMatch(/\[[^\]]+\]/);
  });

  it("Datenschutz beschreibt bei relativer TILE_URL den Kachel-Proxy", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /api/config": {
        body: { ...CONFIG, map: { ...CONFIG.map, tile_url: "/tiles/{z}/{x}/{y}.png" } },
      },
    });
    renderApp("/datenschutz");
    await screen.findByRole("heading", { level: 1, name: "Datenschutzerklärung" });
    const text = document.body.textContent ?? "";
    expect(text).toContain("über den Server dieser Website");
    expect(text).toContain("nicht deine IP-Adresse");
    expect(text).not.toContain("[Kartenserver]");
  });
});
