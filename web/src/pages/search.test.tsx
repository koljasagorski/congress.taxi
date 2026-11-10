import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { baseRoutes, CONFIG, errorBody, match, mockApi, request } from "../test/mockApi";
import { renderApp } from "../test/render";

async function pickAndSearch(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Testklick auf die Karte" }));
  await user.click(screen.getByRole("button", { name: "Fahrten suchen" }));
}

describe("Mitfahrt suchen", () => {
  it("verlangt einen Abholpunkt", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await user.click(await screen.findByRole("button", { name: "Fahrten suchen" }));
    expect(await screen.findByText("Bitte setze einen Punkt auf der Karte oder gib Koordinaten ein.")).toBeInTheDocument();
    expect(api.callsTo("GET /api/match")).toHaveLength(0);
  });

  it("zeigt Trefferliste mit Umweg in km und Minuten und zeichnet die Routen", async () => {
    const api = mockApi({
      ...baseRoutes(),
      "GET /api/match": {
        body: {
          items: [
            match({ ride_id: "r-9", driver_display_name: "Sam", detour_km: 2.5, detour_minutes: 4, detour_ratio: 0.12 }),
            match({ ride_id: "r-10", driver_display_name: "Kim", detour_km: 5, detour_minutes: 9, detour_ratio: 0.2 }),
          ],
          total: 2,
        },
      },
    });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    fireEvent.change(await screen.findByLabelText("Benötigte Plätze"), { target: { value: "2" } });
    await pickAndSearch(user);

    expect(await screen.findByText("2 Fahrten gefunden")).toBeInTheDocument();
    const sam = screen.getByRole("listitem", { name: "Fahrt von Sam" });
    expect(within(sam).getByText("Umweg: ≈ +2,5 km, ≈ +4 Min. (≈ +12 %)")).toBeInTheDocument();
    expect(within(sam).getByText(/2 frei/)).toBeInTheDocument();
    const kim = screen.getByRole("listitem", { name: "Fahrt von Kim" });
    expect(within(kim).getByText("Umweg: ≈ +5 km, ≈ +9 Min. (≈ +20 %)")).toBeInTheDocument();
    expect(within(screen.getByTestId("map-routes")).getAllByRole("listitem")).toHaveLength(2);

    const query = api.callsTo("GET /api/match")[0]?.query;
    expect(query?.get("lat")).toBe("53.55");
    expect(query?.get("lon")).toBe("9.99");
    expect(query?.get("min_seats")).toBe("2");
    expect(query?.has("time_from")).toBe(false);
  });

  it("reicht Kachel-URL und Quellenangabe aus GET /api/config an die Karte weiter", async () => {
    mockApi(baseRoutes());
    renderApp("/mitfahrt-suchen");
    const map = await screen.findByTestId("map");
    expect(map).toHaveAttribute("data-tile-url", CONFIG.map.tile_url);
    expect(map).toHaveAttribute("data-attribution", CONFIG.map.attribution);
  });

  it("sucht mit getippten, noch nicht bestätigten Koordinaten statt mit dem alten Punkt", async () => {
    const api = mockApi({ ...baseRoutes(), "GET /api/match": { body: { items: [], total: 0 } } });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await user.click(await screen.findByRole("button", { name: "Testklick auf die Karte" }));
    const lat = screen.getByLabelText("Breitengrad (lat)");
    await user.clear(lat);
    await user.type(lat, "48.1");
    await user.click(screen.getByRole("button", { name: "Fahrten suchen" }));
    await screen.findByText(/Keine passenden Fahrten/);
    const query = api.callsTo("GET /api/match")[0]?.query;
    expect(query?.get("lat")).toBe("48.1");
    expect(query?.get("lon")).toBe("9.99");
  });

  it("sucht nicht, wenn getippte Koordinaten außerhalb des Wertebereichs liegen", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await user.click(await screen.findByRole("button", { name: "Testklick auf die Karte" }));
    const lat = screen.getByLabelText("Breitengrad (lat)");
    await user.clear(lat);
    await user.type(lat, "95");
    await user.click(screen.getByRole("button", { name: "Fahrten suchen" }));
    expect(await screen.findByText("Breitengrad: -90 bis 90, Längengrad: -180 bis 180.")).toBeInTheDocument();
    expect(api.callsTo("GET /api/match")).toHaveLength(0);
  });

  it("sendet das Zeitfenster als ISO 8601 mit Zeitzone", async () => {
    const api = mockApi({ ...baseRoutes(), "GET /api/match": { body: { items: [], total: 0 } } });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    fireEvent.change(await screen.findByLabelText("Frühestens ab (optional)"), { target: { value: "2099-12-27T06:00" } });
    fireEvent.change(screen.getByLabelText("Spätestens bis (optional)"), { target: { value: "2099-12-27T12:00" } });
    await pickAndSearch(user);
    await screen.findByText(/Keine passenden Fahrten/);
    const query = api.callsTo("GET /api/match")[0]?.query;
    expect(query?.get("time_from")).toMatch(/^2099-12-27T\d{2}:00:00\.000Z$/);
    expect(query?.get("time_to")).toMatch(/Z$/);
  });

  it("prüft die Reihenfolge des Zeitfensters", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    fireEvent.change(await screen.findByLabelText("Frühestens ab (optional)"), { target: { value: "2099-12-27T12:00" } });
    fireEvent.change(screen.getByLabelText("Spätestens bis (optional)"), { target: { value: "2099-12-27T06:00" } });
    await pickAndSearch(user);
    expect(await screen.findByText("Das Ende muss nach dem Beginn liegen.")).toBeInTheDocument();
    expect(api.callsTo("GET /api/match")).toHaveLength(0);
  });

  it("zeigt bei leerer Liste einen Hinweis statt einer Liste", async () => {
    mockApi({ ...baseRoutes(), "GET /api/match": { body: { items: [], total: 0 } } });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await pickAndSearch(user);
    expect(await screen.findByText(/Keine passenden Fahrten gefunden/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /./ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mitfahrt anfragen" })).not.toBeInTheDocument();
  });

  it("hebt die Route hervor und schaltet zurück", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /api/match": { body: { items: [match({ ride_id: "a" }), match({ ride_id: "b", driver_display_name: "Kim" })], total: 2 } },
    });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await pickAndSearch(user);
    const sam = await screen.findByRole("listitem", { name: "Fahrt von Sam" });
    const toggle = within(sam).getByRole("button", { name: /Route auf der Karte hervorheben/ });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    const routes = within(screen.getByTestId("map-routes"));
    expect(routes.getAllByRole("listitem").map((li) => li.getAttribute("data-highlighted"))).toEqual(["true", "false"]);
    await user.click(toggle);
    expect(routes.getAllByRole("listitem").map((li) => li.getAttribute("data-highlighted"))).toEqual(["false", "false"]);
  });

  it("stellt eine Anfrage mit dem Abholpunkt und zeigt den serverseitig berechneten Umweg", async () => {
    const api = mockApi({
      ...baseRoutes(),
      "GET /api/match": { body: { items: [match({ ride_id: "r-9" })], total: 1 } },
      "POST /api/rides/r-9/requests": {
        status: 201,
        body: request({ id: "q-9", ride_id: "r-9", detour_km: 2.5, detour_minutes: 5 }),
      },
    });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await pickAndSearch(user);
    const sam = await screen.findByRole("listitem", { name: "Fahrt von Sam" });
    await user.click(within(sam).getByRole("button", { name: "Mitfahrt anfragen" }));

    expect(await within(sam).findByText(/Anfrage gesendet/)).toHaveTextContent("≈ +2,5 km, ≈ +5 Min.");
    expect(within(sam).queryByRole("button", { name: "Mitfahrt anfragen" })).not.toBeInTheDocument();
    expect(within(sam).getByRole("link", { name: "Zu meinen Anfragen" })).toHaveAttribute("href", "/meine-fahrten");
    // Der Client sendet nur den Abholpunkt, keine Umwegwerte
    expect(api.callsTo("POST /api/rides/r-9/requests")[0]?.body).toEqual({ pickup: { lat: 53.55, lon: 9.99 } });
  });

  it("bildet Fehlercodes der Anfrage ab (already_requested, detour_too_large)", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /api/match": { body: { items: [match({ ride_id: "a" }), match({ ride_id: "b", driver_display_name: "Kim" })], total: 2 } },
      "POST /api/rides/a/requests": { status: 409, body: errorBody("already_requested", "x") },
      "POST /api/rides/b/requests": { status: 422, body: errorBody("detour_too_large", "x") },
    });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await pickAndSearch(user);
    const sam = await screen.findByRole("listitem", { name: "Fahrt von Sam" });
    const kim = screen.getByRole("listitem", { name: "Fahrt von Kim" });
    await user.click(within(sam).getByRole("button", { name: "Mitfahrt anfragen" }));
    await user.click(within(kim).getByRole("button", { name: "Mitfahrt anfragen" }));
    expect(await within(sam).findByRole("alert")).toHaveTextContent("bereits eine aktive Anfrage");
    expect(await within(kim).findByRole("alert")).toHaveTextContent("Umweg für diese Fahrt wäre zu groß");
    // erneut versuchbar
    expect(within(sam).getByRole("button", { name: "Mitfahrt anfragen" })).toBeEnabled();
  });

  it("zeigt outside_coverage und 503 bei der Suche", async () => {
    const api = mockApi({
      ...baseRoutes(),
      "GET /api/match": { status: 422, body: errorBody("outside_coverage", "x") },
    });
    const user = userEvent.setup();
    renderApp("/mitfahrt-suchen");
    await pickAndSearch(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("nicht in einem Gebiet");

    api.set("GET /api/match", { status: 503, body: errorBody("routing_unavailable", "x") });
    await user.click(screen.getByRole("button", { name: "Fahrten suchen" }));
    expect(await screen.findByText(/Routenberechnung ist gerade nicht verfügbar/)).toBeInTheDocument();
  });
});
