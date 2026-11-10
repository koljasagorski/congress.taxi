import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { baseRoutes, errorBody, mockApi } from "../test/mockApi";
import { renderApp } from "../test/render";

describe("Konto löschen", () => {
  it("verlangt eine ausdrückliche Bestätigung", async () => {
    const api = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/konto-loeschen");
    await user.click(await screen.findByRole("button", { name: "Konto endgültig löschen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("bestätige die Löschung");
    expect(api.callsTo("DELETE /api/me")).toHaveLength(0);
  });

  it("löscht das Konto, meldet ab und bestätigt", async () => {
    const api = mockApi({ ...baseRoutes(), "DELETE /api/me": { status: 204 } });
    const user = userEvent.setup();
    renderApp("/konto-loeschen");
    await user.click(await screen.findByRole("checkbox", { name: /unwiderruflich löschen/ }));
    await user.click(screen.getByRole("button", { name: "Konto endgültig löschen" }));

    expect(await screen.findByRole("heading", { name: "Konto gelöscht" })).toBeInTheDocument();
    expect(screen.getByText(/alle zugehörigen Fahrten und Anfragen wurden gelöscht/)).toBeInTheDocument();
    expect(api.callsTo("DELETE /api/me")).toHaveLength(1);
    // Navigation zeigt wieder den abgemeldeten Zustand
    expect(screen.getByRole("link", { name: "Anmelden" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Konto löschen" })).not.toBeInTheDocument();
  });

  it("zeigt Serverfehler und behält das Konto-Formular", async () => {
    mockApi({ ...baseRoutes(), "DELETE /api/me": { status: 500, body: errorBody("internal_error", "x") } });
    const user = userEvent.setup();
    renderApp("/konto-loeschen");
    await user.click(await screen.findByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Konto endgültig löschen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("interner Fehler");
    expect(screen.getByRole("button", { name: "Konto endgültig löschen" })).toBeEnabled();
  });

  it("verlangt Anmeldung", async () => {
    mockApi(baseRoutes(null));
    renderApp("/konto-loeschen");
    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
  });
});
