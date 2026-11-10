import { screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { describe, expect, it } from "vitest";
import { baseRoutes, match, mockApi, page, request, ride } from "../test/mockApi";
import { renderApp } from "../test/render";

// Farbkontrast lässt sich in jsdom nicht berechnen (kein Layout/Styling); html-has-lang prüft das
// Testdokument, nicht web/index.html (dort steht lang="de").
const AXE_OPTIONS: axe.RunOptions = {
  rules: { "color-contrast": { enabled: false }, "html-has-lang": { enabled: false } },
};

async function expectNoViolations() {
  const results = await axe.run(document.body, AXE_OPTIONS);
  const summary = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(summary).toEqual([]);
}

function expectAllControlsLabelled() {
  const controls = document.querySelectorAll<HTMLInputElement>("main input, main select, main textarea");
  expect(controls.length).toBeGreaterThan(0);
  for (const control of controls) {
    expect(control.labels?.length ?? 0, `Feld ${control.name || control.type} ohne <label>`).toBeGreaterThan(0);
  }
}

const mineRoutes = {
  "GET /api/rides/mine": { body: page([ride()]) },
  "GET /api/requests/incoming": { body: page([request()]) },
  "GET /api/requests/outgoing": { body: page([request({ id: "q-2", status: "accepted", counterpart_email: "x@example.test" })]) },
};

describe("Barrierefreiheit (axe + Rollen)", () => {
  it.each([
    ["/registrieren", "Registrieren"],
    ["/anmelden", "Anmelden"],
  ])("Formular %s: Labels, Rollen, keine axe-Verstöße", async (route, heading) => {
    mockApi(baseRoutes(null));
    renderApp(route);
    await screen.findByRole("heading", { level: 1, name: heading });
    expectAllControlsLabelled();
    await expectNoViolations();
  });

  it("Fahrt anbieten: Eingaben, Karte und Koordinatenfelder sind bedienbar benannt", async () => {
    mockApi(baseRoutes());
    renderApp("/fahrt-anbieten");
    await screen.findByRole("heading", { level: 1, name: "Fahrt anbieten" });
    expect(screen.getByRole("group", { name: "Startpunkt" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Breitengrad (lat)" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Längengrad (lon)" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Freie Plätze" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Karte" })).toBeInTheDocument();
    expectAllControlsLabelled();
    await expectNoViolations();
  });

  it("Mitfahrt suchen inkl. Trefferliste", async () => {
    mockApi({ ...baseRoutes(), "GET /api/match": { body: { items: [match()], total: 1 } } });
    renderApp("/mitfahrt-suchen");
    await screen.findByRole("heading", { level: 1, name: "Mitfahrt suchen" });
    expectAllControlsLabelled();
    await expectNoViolations();
  });

  it("Meine Fahrten & Anfragen", async () => {
    mockApi({ ...baseRoutes(), ...mineRoutes });
    renderApp("/meine-fahrten");
    await screen.findByRole("heading", { level: 2, name: "Meine Fahrten" });
    await waitFor(() => expect(screen.getAllByRole("listitem").length).toBeGreaterThan(3));
    await expectNoViolations();
  });

  it.each(["/", "/datenschutz", "/impressum", "/konto-loeschen"])("Seite %s", async (route) => {
    mockApi(baseRoutes());
    renderApp(route);
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(screen.queryByText("Wird geladen …")).not.toBeInTheDocument());
    await expectNoViolations();
  });

  it("hat Skip-Link, Landmarken und sichtbare Entwurfskennzeichnung bei Rechtstexten", async () => {
    mockApi(baseRoutes());
    renderApp("/impressum");
    await screen.findByRole("heading", { level: 1, name: "Impressum" });
    expect(screen.getByRole("link", { name: "Zum Inhalt springen" })).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/ENTWURF|Entwurf/);
  });
});
