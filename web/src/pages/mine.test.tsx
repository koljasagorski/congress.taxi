import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { RequestOut, RideOut } from "../api/types";
import { baseRoutes, errorBody, mockApi, page, request, ride } from "../test/mockApi";
import { renderApp } from "../test/render";

interface Store {
  rides: RideOut[];
  incoming: RequestOut[];
  outgoing: RequestOut[];
}

function listRoutes(store: Store) {
  return {
    ...baseRoutes(),
    "GET /api/rides/mine": () => ({ body: page(store.rides) }),
    "GET /api/requests/incoming": () => ({ body: page(store.incoming) }),
    "GET /api/requests/outgoing": () => ({ body: page(store.outgoing) }),
  };
}

describe("Meine Fahrten & Anfragen", () => {
  it("zeigt leere Zustände mit Links", async () => {
    mockApi(listRoutes({ rides: [], incoming: [], outgoing: [] }));
    renderApp("/meine-fahrten");
    expect(await screen.findByText(/noch keine Fahrt angeboten/)).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Fahrt anbieten" })).toHaveAttribute("href", "/fahrt-anbieten");
    expect(screen.getByText(/noch keine Mitfahrt angefragt/)).toBeInTheDocument();
  });

  it("zeigt eigene Fahrten mit eingehenden Anfragen; E-Mail erst nach Annahme", async () => {
    const store: Store = {
      rides: [ride({ id: "r-1", pending_requests: 1, seats_available: 2 })],
      incoming: [request({ id: "q-1", ride_id: "r-1", status: "pending", rider: { display_name: "Robin" } })],
      outgoing: [],
    };
    const api = mockApi({
      ...listRoutes(store),
      "POST /api/requests/q-1/accept": () => {
        const accepted = request({
          id: "q-1",
          ride_id: "r-1",
          status: "accepted",
          decided_at: "2026-11-03T10:00:00Z",
          counterpart_email: "robin@example.test",
        });
        store.incoming = [accepted];
        store.rides = [ride({ id: "r-1", accepted_requests: 1, seats_available: 1 })];
        return { body: accepted };
      },
    });
    const user = userEvent.setup();
    renderApp("/meine-fahrten");

    const row = await screen.findByRole("listitem", { name: /Anfrage von Robin/ });
    expect(within(row).getByText("Offen")).toBeInTheDocument();
    expect(screen.queryByText("robin@example.test")).not.toBeInTheDocument();
    expect(within(row).getByText(/erst nach Annahme/)).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Annehmen" }));
    expect(await screen.findByRole("link", { name: "robin@example.test" })).toHaveAttribute(
      "href",
      "mailto:robin@example.test",
    );
    expect(screen.getByText("Anfrage angenommen.")).toBeInTheDocument();
    expect(api.callsTo("POST /api/requests/q-1/accept")).toHaveLength(1);
    // nach der Annahme kommt "Stornieren" (Fahrer darf nur angenommene stornieren)
    const accepted = screen.getByRole("listitem", { name: /Anfrage von Robin/ });
    expect(within(accepted).queryByRole("button", { name: "Annehmen" })).not.toBeInTheDocument();
    expect(within(accepted).getByRole("button", { name: "Stornieren" })).toBeInTheDocument();
  });

  it("zeigt die E-Mail nicht, solange der Server sie nicht liefert (z. B. declined/cancelled)", async () => {
    mockApi(
      listRoutes({
        rides: [ride({ id: "r-1" })],
        incoming: [
          request({ id: "q-1", ride_id: "r-1", status: "declined", rider: { display_name: "Dana" } }),
          // Selbst wenn ein fehlerhaftes Backend die Adresse mitschicken würde: nur bei accepted anzeigen
          request({ id: "q-2", ride_id: "r-1", status: "cancelled", counterpart_email: "leak@example.test" }),
        ],
        outgoing: [],
      }),
    );
    renderApp("/meine-fahrten");
    await screen.findByRole("listitem", { name: /Anfrage von Dana/ });
    expect(screen.queryByText(/leak@example\.test/)).not.toBeInTheDocument();
  });

  it("lehnt eine Anfrage ab", async () => {
    const store: Store = {
      rides: [ride({ id: "r-1" })],
      incoming: [request({ id: "q-1", ride_id: "r-1" })],
      outgoing: [],
    };
    mockApi({
      ...listRoutes(store),
      "POST /api/requests/q-1/decline": () => {
        const declined = request({ id: "q-1", ride_id: "r-1", status: "declined" });
        store.incoming = [declined];
        return { body: declined };
      },
    });
    const user = userEvent.setup();
    renderApp("/meine-fahrten");
    const row = await screen.findByRole("listitem", { name: /Anfrage von Robin/ });
    await user.click(within(row).getByRole("button", { name: "Ablehnen" }));
    expect(await screen.findByText("Anfrage abgelehnt.")).toBeInTheDocument();
    const after = screen.getByRole("listitem", { name: /Anfrage von Robin/ });
    expect(within(after).getByText("Abgelehnt")).toBeInTheDocument();
    expect(within(after).queryByRole("button")).not.toBeInTheDocument();
  });

  it("storniert eine ausgehende Anfrage (offen: direkt, angenommen: mit Rückfrage)", async () => {
    const store: Store = {
      rides: [],
      incoming: [],
      outgoing: [
        request({ id: "q-1", status: "pending", ride: { departure_time: "2026-12-27T08:00:00Z", seats_available: 1, driver_display_name: "Sam" } }),
        request({
          id: "q-2",
          status: "accepted",
          counterpart_email: "sam@example.test",
          ride: { departure_time: "2026-12-28T08:00:00Z", seats_available: 1, driver_display_name: "Kim" },
        }),
      ],
    };
    const api = mockApi({
      ...listRoutes(store),
      "POST /api/requests/q-1/cancel": () => {
        const c = { ...store.outgoing[0]!, status: "cancelled" as const };
        store.outgoing = [c, store.outgoing[1]!];
        return { body: c };
      },
      "POST /api/requests/q-2/cancel": () => {
        const c = { ...store.outgoing[1]!, status: "cancelled" as const, counterpart_email: null };
        store.outgoing = [store.outgoing[0]!, c];
        return { body: c };
      },
    });
    const user = userEvent.setup();
    renderApp("/meine-fahrten");

    const pending = await screen.findByRole("listitem", { name: /Fahrt von Sam/ });
    // E-Mail des Fahrers nur bei der angenommenen Anfrage
    expect(screen.getByRole("link", { name: "sam@example.test" })).toBeInTheDocument();
    expect(within(pending).queryByRole("link", { name: /@/ })).not.toBeInTheDocument();

    await user.click(within(pending).getByRole("button", { name: "Stornieren" }));
    await waitFor(() => expect(api.callsTo("POST /api/requests/q-1/cancel")).toHaveLength(1));
    expect(await screen.findByText("Anfrage storniert.")).toBeInTheDocument();

    const accepted = screen.getByRole("listitem", { name: /Fahrt von Kim/ });
    await user.click(within(accepted).getByRole("button", { name: "Stornieren" }));
    // Rückfrage, noch kein Request
    expect(api.callsTo("POST /api/requests/q-2/cancel")).toHaveLength(0);
    await user.click(within(accepted).getByRole("button", { name: "Ja, stornieren" }));
    await waitFor(() => expect(api.callsTo("POST /api/requests/q-2/cancel")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("link", { name: "sam@example.test" })).not.toBeInTheDocument());
  });

  it("storniert eine Fahrt nach Rückfrage", async () => {
    const store: Store = { rides: [ride({ id: "r-1" })], incoming: [], outgoing: [] };
    const api = mockApi({
      ...listRoutes(store),
      "DELETE /api/rides/r-1": () => {
        store.rides = [ride({ id: "r-1", status: "cancelled" })];
        return { status: 204 };
      },
    });
    const user = userEvent.setup();
    renderApp("/meine-fahrten");
    await user.click(await screen.findByRole("button", { name: "Fahrt stornieren" }));
    expect(api.callsTo("DELETE /api/rides/r-1")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Nein, behalten" }));
    expect(screen.getByRole("button", { name: "Fahrt stornieren" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Fahrt stornieren" }));
    await user.click(screen.getByRole("button", { name: "Ja, stornieren" }));
    expect(await screen.findByText(/Fahrt storniert\. Alle offenen/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Fahrt stornieren" })).not.toBeInTheDocument());
    expect(api.callsTo("DELETE /api/rides/r-1")).toHaveLength(1);
  });

  it("meldet 409 invalid_state verständlich und lädt neu", async () => {
    const store: Store = {
      rides: [ride({ id: "r-1" })],
      incoming: [request({ id: "q-1", ride_id: "r-1" })],
      outgoing: [],
    };
    const api = mockApi({
      ...listRoutes(store),
      "POST /api/requests/q-1/accept": () => {
        store.incoming = [request({ id: "q-1", ride_id: "r-1", status: "cancelled" })];
        return { status: 409, body: errorBody("invalid_state", "x") };
      },
    });
    const user = userEvent.setup();
    renderApp("/meine-fahrten");
    const row = await screen.findByRole("listitem", { name: /Anfrage von Robin/ });
    await user.click(within(row).getByRole("button", { name: "Annehmen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Der Status hat sich inzwischen geändert");
    await waitFor(() => expect(api.callsTo("GET /api/requests/incoming").length).toBeGreaterThan(1));
    const after = await screen.findByRole("listitem", { name: /Anfrage von Robin/ });
    await waitFor(() => expect(within(after).getByText("Storniert")).toBeInTheDocument());
  });

  it("leitet bei 401 auf die Anmeldung um", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /api/rides/mine": { status: 401, body: errorBody("unauthorized", "x") },
      "GET /api/requests/incoming": { status: 401, body: errorBody("unauthorized", "x") },
      "GET /api/requests/outgoing": { status: 401, body: errorBody("unauthorized", "x") },
    });
    renderApp("/meine-fahrten");
    expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/anmelden?next=%2Fmeine-fahrten");
  });
});
