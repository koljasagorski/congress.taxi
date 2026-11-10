import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { baseRoutes, errorBody, mockApi, ride } from "../test/mockApi";
import { renderApp } from "../test/render";

const FUTURE = "2099-12-27T09:30";

async function pickAndFill(user: ReturnType<typeof userEvent.setup>, seats = "3") {
  await user.click(await screen.findByRole("button", { name: "Testklick auf die Karte" }));
  fireEvent.change(screen.getByLabelText("Abfahrt (Datum und Uhrzeit)"), { target: { value: FUTURE } });
  const seatsInput = screen.getByLabelText("Freie Plätze");
  await user.clear(seatsInput);
  await user.type(seatsInput, seats);
}

describe("Fahrt anbieten", () => {
  it("validiert: Startpunkt, Abfahrt in der Zukunft, Plätze 1 bis 8 (Grenzen aus /api/config)", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await screen.findByRole("heading", { name: "Fahrt anbieten", level: 1 });

    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));
    expect(await screen.findByText("Bitte setze einen Punkt auf der Karte oder gib Koordinaten ein.")).toBeInTheDocument();
    expect(screen.getByText("Bitte gib die Abfahrtszeit ein.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Abfahrt (Datum und Uhrzeit)"), { target: { value: "2020-01-01T10:00" } });
    const seats = screen.getByLabelText("Freie Plätze");
    await user.clear(seats);
    await user.type(seats, "9");
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));
    expect(await screen.findByText("Die Abfahrt muss in der Zukunft liegen.")).toBeInTheDocument();
    expect(screen.getByText("Bitte gib eine Zahl zwischen 1 und 8 ein.")).toBeInTheDocument();
    expect(api.callsTo("POST /api/rides")).toHaveLength(0);
  });

  it("legt die Fahrt an und zeigt Zusammenfassung und Route aus der API-Antwort", async () => {
    const created = ride({ id: "neu-1", distance_km: 12.34, duration_min: 21.4, seats_total: 2, seats_available: 2 });
    const api = mockApi({ ...baseRoutes(), "POST /api/rides": { status: 201, body: created } });
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await pickAndFill(user, "2");
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));

    expect(await screen.findByRole("heading", { name: "Fahrt angelegt" })).toBeInTheDocument();
    const status = screen.getByText(/^Route: /);
    expect(status).toHaveTextContent("12,3 km");
    expect(status).toHaveTextContent("21 Minuten");
    expect(within(screen.getByTestId("map-routes")).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Zu meinen Fahrten" })).toHaveAttribute("href", "/meine-fahrten");

    const body = api.callsTo("POST /api/rides")[0]?.body as {
      start: { lat: number; lon: number };
      departure_time: string;
      seats: number;
    };
    expect(body.start).toEqual({ lat: 53.55, lon: 9.99 });
    expect(body.seats).toBe(2);
    // ISO 8601 mit Zeitzone (UTC)
    expect(body.departure_time).toMatch(/^2099-12-27T\d{2}:30:00\.000Z$/);
  });

  it("begrenzt den Zoom bei der Punktwahl (Datenschutz: Kachelabrufe verraten den Ausschnitt)", async () => {
    mockApi(baseRoutes());
    renderApp("/fahrt-anbieten");
    expect((await screen.findByTestId("map")).getAttribute("data-max-zoom")).toBe("16");
  });

  it("übernimmt getippte, noch nicht bestätigte Koordinaten beim Absenden", async () => {
    const api = mockApi({ ...baseRoutes(), "POST /api/rides": { status: 201, body: ride({ id: "neu-2" }) } });
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await pickAndFill(user);
    const lat = screen.getByLabelText("Breitengrad (lat)");
    await user.clear(lat);
    await user.type(lat, "48,1");
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));

    expect(await screen.findByRole("heading", { name: "Fahrt angelegt" })).toBeInTheDocument();
    const body = api.callsTo("POST /api/rides")[0]?.body as { start: { lat: number; lon: number } };
    expect(body.start).toEqual({ lat: 48.1, lon: 9.99 });
  });

  it("sendet nichts, wenn getippte Koordinaten ungültig sind, und zeigt den Fehler an", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await pickAndFill(user);
    const lat = screen.getByLabelText("Breitengrad (lat)");
    await user.clear(lat);
    await user.type(lat, "abc");
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));

    expect(await screen.findByText("Bitte gib gültige Zahlen für Breiten- und Längengrad ein.")).toBeInTheDocument();
    expect(api.callsTo("POST /api/rides")).toHaveLength(0);
  });

  it("zeigt outside_coverage am Kartenpunkt und lässt die Eingaben stehen", async () => {
    mockApi({
      ...baseRoutes(),
      "POST /api/rides": { status: 422, body: errorBody("outside_coverage", "x") },
    });
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await pickAndFill(user);
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("nicht in einem Gebiet");
    expect(screen.getByLabelText("Breitengrad (lat)")).toHaveValue("53.55");
    expect(screen.getByLabelText("Abfahrt (Datum und Uhrzeit)")).toHaveValue(FUTURE);
  });

  it("zeigt routing_unavailable als Hinweis zum späteren Wiederholen", async () => {
    mockApi({
      ...baseRoutes(),
      "POST /api/rides": { status: 503, body: errorBody("routing_unavailable", "x") },
    });
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await pickAndFill(user);
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Routenberechnung ist gerade nicht verfügbar");
    expect(screen.getByRole("button", { name: "Fahrt anbieten" })).toBeEnabled();
  });

  it("erlaubt den Startpunkt per Koordinateneingabe (Tastatur-Alternative zum Klick)", async () => {
    const api = mockApi({ ...baseRoutes(), "POST /api/rides": { status: 201, body: ride() } });
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await screen.findByRole("heading", { name: "Fahrt anbieten", level: 1 });

    await user.type(screen.getByLabelText("Breitengrad (lat)"), "53,5511");
    await user.type(screen.getByLabelText("Längengrad (lon)"), "9.9937{Enter}");
    expect(screen.getByText(/Punkt gesetzt: 53\.55110, 9\.99370/)).toBeInTheDocument();
    expect(within(screen.getByTestId("map-markers")).getByText("Startpunkt")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Abfahrt (Datum und Uhrzeit)"), { target: { value: FUTURE } });
    await user.click(screen.getByRole("button", { name: "Fahrt anbieten" }));
    await screen.findByRole("heading", { name: "Fahrt angelegt" });
    expect((api.callsTo("POST /api/rides")[0]?.body as { start: unknown }).start).toEqual({ lat: 53.5511, lon: 9.9937 });
  });

  it("lehnt ungültige Koordinaten ab und kann die Kartenmitte übernehmen", async () => {
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/fahrt-anbieten");
    await screen.findByRole("heading", { name: "Fahrt anbieten", level: 1 });

    await user.type(screen.getByLabelText("Breitengrad (lat)"), "95");
    await user.type(screen.getByLabelText("Längengrad (lon)"), "9");
    await user.click(screen.getByRole("button", { name: "Koordinaten übernehmen" }));
    expect(await screen.findByText("Breitengrad: -90 bis 90, Längengrad: -180 bis 180.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Kartenmitte als Punkt übernehmen" }));
    expect(screen.getByText(/Punkt gesetzt: 53\.60000, 10\.00000/)).toBeInTheDocument();
  });
});
