import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { baseRoutes, CONFIG, errorBody, mockApi, USER } from "../test/mockApi";
import { renderApp } from "../test/render";

const CONFIG_ROUTE = { body: CONFIG };

async function fillRegister(user: ReturnType<typeof userEvent.setup>, consent = true) {
  await user.type(await screen.findByLabelText("E-Mail-Adresse"), "neu@example.test");
  await user.type(screen.getByLabelText("Passwort"), "ein-langes-passwort");
  await user.type(screen.getByLabelText("Anzeigename"), "Neu");
  if (consent) await user.click(screen.getByRole("checkbox"));
}

describe("Registrierung", () => {
  it("202 ohne Auto-Login: zeigt Erfolg und führt zur Anmeldung", async () => {
    const api = mockApi({
      ...baseRoutes(null),
      "POST /api/auth/register": { status: 202, body: { message: "ok" } },
    });
    const user = userEvent.setup();
    renderApp("/registrieren");
    await fillRegister(user);
    await user.click(screen.getByRole("button", { name: "Konto anlegen" }));

    expect(await screen.findByRole("heading", { name: "Registrierung entgegengenommen" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Zur Anmeldung" })).toHaveAttribute("href", "/anmelden");

    const body = api.callsTo("POST /api/auth/register")[0]?.body;
    expect(body).toEqual({
      email: "neu@example.test",
      password: "ein-langes-passwort",
      display_name: "Neu",
      consent: true,
    });
    // Kein Auto-Login: weder Login-Aufruf noch zweiter /api/me-Abruf nach der Registrierung.
    expect(api.callsTo("POST /api/auth/login")).toHaveLength(0);
    expect(api.callsTo("GET /api/me")).toHaveLength(1);
    // Die Erfolgsmeldung verrät nicht, ob die E-Mail schon existierte.
    expect(screen.queryByText(/bereits|vergeben|existiert/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Zur Anmeldung" }));
    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
  });

  it("validiert clientseitig (Einwilligung, Passwortlänge, Name) und sendet nichts", async () => {
    const api = mockApi(baseRoutes(null));
    const user = userEvent.setup();
    renderApp("/registrieren");
    await user.type(await screen.findByLabelText("E-Mail-Adresse"), "kaputt");
    await user.type(screen.getByLabelText("Passwort"), "kurz");
    await user.click(screen.getByRole("button", { name: "Konto anlegen" }));

    expect(await screen.findByText("Bitte gib eine gültige E-Mail-Adresse ein.")).toBeInTheDocument();
    expect(screen.getByText("Das Passwort muss zwischen 10 und 128 Zeichen lang sein.")).toBeInTheDocument();
    expect(screen.getByText("Bitte gib einen Anzeigenamen ein (1 bis 80 Zeichen).")).toBeInTheDocument();
    expect(screen.getByText("Ohne deine Einwilligung kann kein Konto angelegt werden.")).toBeInTheDocument();
    expect(screen.getByLabelText("E-Mail-Adresse")).toBeInvalid();
    expect(screen.getByLabelText("E-Mail-Adresse")).toHaveFocus();
    expect(api.callsTo("POST /api/auth/register")).toHaveLength(0);
  });

  it("bildet 422 validation_error auf deutsche Feldfehler ab, ohne Server-Texte zu übernehmen", async () => {
    mockApi({
      ...baseRoutes(null),
      "POST /api/auth/register": {
        status: 422,
        body: errorBody("validation_error", "Ungültige Eingabe", [
          { field: "body.email", message: "value is not a valid email address" },
        ]),
      },
    });
    const user = userEvent.setup();
    renderApp("/registrieren");
    await fillRegister(user);
    await user.click(screen.getByRole("button", { name: "Konto anlegen" }));

    expect(await screen.findByText("Bitte prüfe deine Eingaben.")).toBeInTheDocument();
    expect(screen.getByText("Dieses Feld ist ungültig.")).toBeInTheDocument();
    expect(screen.queryByText(/not a valid email/)).not.toBeInTheDocument();
  });

  it("meldet 429 verständlich", async () => {
    mockApi({
      ...baseRoutes(null),
      "POST /api/auth/register": { status: 429, body: errorBody("rate_limited", "x"), headers: { "Retry-After": "30" } },
    });
    const user = userEvent.setup();
    renderApp("/registrieren");
    await fillRegister(user);
    await user.click(screen.getByRole("button", { name: "Konto anlegen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("in etwa 30 Sekunden");
  });
});

describe("Login", () => {
  it("zeigt bei falschen Zugangsdaten eine generische Meldung und bleibt auf der Seite", async () => {
    mockApi({
      ...baseRoutes(null),
      "POST /api/auth/login": { status: 401, body: errorBody("invalid_credentials", "x") },
    });
    const user = userEvent.setup();
    renderApp("/anmelden");
    await user.type(await screen.findByLabelText("E-Mail-Adresse"), "a@example.test");
    await user.type(screen.getByLabelText("Passwort"), "falsch-falsch");
    await user.click(screen.getByRole("button", { name: "Anmelden" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("E-Mail-Adresse oder Passwort ist falsch.");
    expect(screen.getByTestId("location")).toHaveTextContent("/anmelden");
    expect(screen.getByLabelText("Passwort")).toHaveValue("");
  });

  it("meldet an, lädt /api/me und leitet auf das Ziel aus ?next= weiter", async () => {
    let loggedIn = false;
    const api = mockApi({
      "GET /api/config": CONFIG_ROUTE,
      "GET /api/me": () =>
        loggedIn ? { body: USER } : { status: 401, body: errorBody("unauthorized", "Nicht angemeldet") },
      "POST /api/auth/login": () => {
        loggedIn = true;
        return { body: { user: { id: USER.id, display_name: USER.display_name } } };
      },
      "GET /api/rides/mine": { body: { items: [], total: 0, limit: 100, offset: 0 } },
      "GET /api/requests/incoming": { body: { items: [], total: 0, limit: 100, offset: 0 } },
      "GET /api/requests/outgoing": { body: { items: [], total: 0, limit: 100, offset: 0 } },
    });
    const user = userEvent.setup();
    renderApp("/meine-fahrten");

    // Geschützte Seite ohne Anmeldung -> Login mit Rücksprungadresse
    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/anmelden?next=%2Fmeine-fahrten");

    await user.type(screen.getByLabelText("E-Mail-Adresse"), "alex@example.test");
    await user.type(screen.getByLabelText("Passwort"), "richtiges-passwort");
    await user.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByRole("heading", { name: "Meine Fahrten & Anfragen" })).toBeInTheDocument();
    expect(api.callsTo("POST /api/auth/login")[0]?.body).toEqual({
      email: "alex@example.test",
      password: "richtiges-passwort",
    });
    expect(screen.getByText("Angemeldet als Alex")).toBeInTheDocument();
  });
});

describe("Abmelden", () => {
  it("ruft logout auf und zeigt wieder Anmelden/Registrieren", async () => {
    const api = mockApi({ ...baseRoutes(USER), "POST /api/auth/logout": { status: 204 } });
    const user = userEvent.setup();
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Hauptnavigation" });
    await user.click(await within(nav).findByRole("button", { name: "Abmelden" }));
    await waitFor(() => expect(within(nav).getByRole("link", { name: "Anmelden" })).toBeInTheDocument());
    expect(api.callsTo("POST /api/auth/logout")).toHaveLength(1);
  });
});
