import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { baseRoutes, CONFIG, errorBody, mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

const WITH_GEOCODER = { ...CONFIG, map: { ...CONFIG.map, geocoder: "nominatim" as const } };
const HITS = {
  items: [
    { label: "Marktstraße 5, Altona, Hamburg", lat: 53.5511, lon: 9.9937 },
    { label: "Marktstraße 5, Mitte, Berlin", lat: 52.52, lon: 13.405 },
  ],
};

function routes(extra: Parameters<typeof mockApi>[0] = {}, geocoder = true) {
  return { ...baseRoutes(), "GET /api/config": { body: geocoder ? WITH_GEOCODER : CONFIG }, ...extra };
}

describe("Adresssuche bei der Punktwahl", () => {
  it("zeigt ohne Geocoder keine Suche (Karte, Koordinaten, Kartenmitte bleiben)", async () => {
    mockApi(routes({}, false));
    renderApp("/fahrt-anbieten");
    await screen.findByRole("heading", { name: "Fahrt anbieten", level: 1 });
    expect(screen.queryByLabelText("Adresse oder Ort suchen")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Koordinaten übernehmen" })).toBeInTheDocument();
  });

  it("sucht per Button, zeigt Treffer und setzt Punkt samt Kartenfokus", async () => {
    const api = mockApi(routes({ "GET /api/geocode": { body: HITS } }));
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await user.type(await screen.findByLabelText("Adresse oder Ort suchen"), "  Marktstraße 5 ");
    await user.click(screen.getByRole("button", { name: "Suchen" }));

    const list = await screen.findByRole("list", { name: "Treffer der Adresssuche" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Treffer: 2 Ergebnisse. Wähle einen aus.")).toBeInTheDocument();
    expect(api.callsTo("GET /api/geocode")[0]?.query.get("q")).toBe("Marktstraße 5");

    await user.click(screen.getByRole("button", { name: "Marktstraße 5, Altona, Hamburg" }));
    expect(screen.queryByRole("list", { name: "Treffer der Adresssuche" })).not.toBeInTheDocument();
    expect(screen.getByText(/^Punkt gesetzt: Marktstraße 5, Altona, Hamburg\./)).toBeInTheDocument();
    expect(screen.getByLabelText("Breitengrad (lat)")).toHaveValue("53.5511");
    expect(screen.getByLabelText("Längengrad (lon)")).toHaveValue("9.9937");
    expect(screen.getByTestId("map").dataset.focus).toBe("53.5511,9.9937");
    expect(within(screen.getByTestId("map-markers")).getByText("Startpunkt")).toBeInTheDocument();
  });

  it("Enter sucht und sendet das Formular nicht ab", async () => {
    const api = mockApi(routes({ "GET /api/geocode": { body: HITS } }));
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await user.type(await screen.findByLabelText("Adresse oder Ort suchen"), "Marktstraße 5{Enter}");
    await screen.findByRole("list", { name: "Treffer der Adresssuche" });
    expect(api.callsTo("POST /api/rides")).toHaveLength(0);
    expect(screen.queryByText("Bitte setze einen Punkt auf der Karte oder gib Koordinaten ein.")).not.toBeInTheDocument();
  });

  it("verlangt mindestens 3 Zeichen, ohne den Server zu fragen", async () => {
    const api = mockApi(routes());
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await user.type(await screen.findByLabelText("Adresse oder Ort suchen"), "ab");
    await user.click(screen.getByRole("button", { name: "Suchen" }));
    expect(screen.getByText("Bitte gib mindestens 3 Zeichen ein.")).toBeInTheDocument();
    expect(api.callsTo("GET /api/geocode")).toHaveLength(0);
  });

  it("meldet keine Treffer", async () => {
    mockApi(routes({ "GET /api/geocode": { body: { items: [] } } }));
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await user.type(await screen.findByLabelText("Adresse oder Ort suchen"), "Nirgendwo 1");
    await user.click(screen.getByRole("button", { name: "Suchen" }));
    expect(await screen.findByText(/^Keine Treffer\./)).toBeInTheDocument();
  });

  it.each([
    [503, "geocoder_unavailable", /nicht erreichbar/],
    [503, "geocoder_busy", /ausgelastet/],
    [429, "rate_limited", /ausgelastet/],
    [404, "geocoder_disabled", /nicht erreichbar/],
  ])("Fehler %i %s zeigt eine Meldung, die anderen Wege bleiben nutzbar", async (status, code, text) => {
    mockApi(routes({ "GET /api/geocode": { status, body: errorBody(code) } }));
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await user.type(await screen.findByLabelText("Adresse oder Ort suchen"), "Marktstraße 5");
    await user.click(screen.getByRole("button", { name: "Suchen" }));
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Koordinaten übernehmen" })).toBeEnabled();
  });

  it("steht auch bei der Mitfahrsuche (Abholpunkt) zur Verfügung", async () => {
    mockApi(routes({ "GET /api/geocode": { body: HITS } }));
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await user.type(await screen.findByLabelText("Adresse oder Ort suchen"), "Marktstraße 5");
    await user.click(screen.getByRole("button", { name: "Suchen" }));
    await user.click(await screen.findByRole("button", { name: "Marktstraße 5, Altona, Hamburg" }));
    expect(screen.getByLabelText("Breitengrad (lat)")).toHaveValue("53.5511");
  });

  it("weist auf den Weg der Eingabe hin (Datenschutz)", async () => {
    mockApi(routes());
    renderApp("/fahrt-anbieten");
    expect(await screen.findByText(/Dein Browser kontaktiert ihn nicht direkt/)).toBeInTheDocument();
  });
});
