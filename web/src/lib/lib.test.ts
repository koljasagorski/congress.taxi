import { describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest, setUnauthorizedHandler } from "../api/client";
import { createGeocoder } from "../geocoder";
import { translate } from "../i18n";
import { de } from "../i18n/de";
import { en } from "../i18n/en";
import { errorMessage, invalidFields } from "./errors";
import { FALLBACK_ATTRIBUTION, safeAttributionHtml } from "./attribution";
import { hostOf, localInputToIso, parseDecimal, safeNext } from "./format";
import { errorBody, mockApi } from "../test/mockApi";

const t = (key: Parameters<typeof translate>[1], params?: Record<string, string | number>) =>
  translate("de", key, params);

describe("API-Client", () => {
  it("liest das Fehlerformat {error:{code,message,details}}", async () => {
    mockApi({
      "POST /api/x": {
        status: 422,
        body: errorBody("validation_error", "Ungültige Eingabe", [{ field: "body.seats", message: "zu groß" }]),
      },
    });
    const err = await apiRequest("POST", "/api/x", { body: {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(422);
    expect(apiErr.code).toBe("validation_error");
    expect(apiErr.details).toEqual([{ field: "body.seats", message: "zu groß" }]);
    expect(invalidFields(apiErr)).toEqual(["seats"]);
  });

  it("sendet Cookies nur an die gleiche Origin und JSON-Header", async () => {
    mockApi({ "POST /api/x": { body: { ok: true } } });
    await apiRequest("POST", "/api/x", { body: { a: 1 } });
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(init?.credentials).toBe("same-origin");
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("ruft bei 401 den Unauthorized-Handler, nicht bei invalid_credentials", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockApi({
      "GET /api/me": { status: 401, body: errorBody("unauthorized", "x") },
      "POST /api/auth/login": { status: 401, body: errorBody("invalid_credentials", "x") },
    });
    await apiRequest("GET", "/api/me").catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    await apiRequest("POST", "/api/auth/login", { body: {} }).catch(() => undefined);
    expect(handler).toHaveBeenCalledTimes(1);
    setUnauthorizedHandler(null);
  });

  it("macht aus Netzwerkfehlern und Nicht-JSON-Antworten sprechende Fehler", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const net = (await apiRequest("GET", "/api/config").catch((e: unknown) => e)) as ApiError;
    expect(net.code).toBe("network_error");
    expect(errorMessage(net, t)).toContain("nicht erreichbar");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>502</html>", { status: 502 })));
    const bad = (await apiRequest("GET", "/api/config").catch((e: unknown) => e)) as ApiError;
    expect(bad.code).toBe("service_unavailable");
    expect(errorMessage(bad, t)).toContain("nicht verfügbar");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>500</html>", { status: 500 })));
    const internal = (await apiRequest("GET", "/api/config").catch((e: unknown) => e)) as ApiError;
    expect(internal.code).toBe("internal_error");
    expect(errorMessage(internal, t)).toContain("interner Fehler");
  });

  it("413 vom Proxy (ohne JSON-Körper) wird zu payload_too_large mit eigener Meldung", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 413 })));
    const err = (await apiRequest("POST", "/api/rides", { body: { x: 1 } }).catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(413);
    expect(err.code).toBe("payload_too_large");
    expect(errorMessage(err, t)).toContain("zu groß");

    // und mit JSON-Fehlerformat der API selbst
    mockApi({ "POST /api/rides": { status: 413, body: errorBody("payload_too_large", "x") } });
    const fromApi = (await apiRequest("POST", "/api/rides", { body: {} }).catch((e: unknown) => e)) as ApiError;
    expect(fromApi.code).toBe("payload_too_large");
  });

  it("429 ohne JSON-Körper wird zu rate_limited; Retry-After nur als ganze Sekunden", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("", { status: 429, headers: { "Retry-After": "30" } })),
    );
    const err = (await apiRequest("GET", "/api/match").catch((e: unknown) => e)) as ApiError;
    expect(err.code).toBe("rate_limited");
    expect(err.retryAfter).toBe(30);
    expect(errorMessage(err, t)).toBe("Zu viele Anfragen. Bitte versuche es in etwa 30 Sekunden erneut.");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("", { status: 429, headers: { "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT" } }),
      ),
    );
    const dated = (await apiRequest("GET", "/api/match").catch((e: unknown) => e)) as ApiError;
    expect(dated.retryAfter).toBeNull();
    expect(errorMessage(dated, t)).toBe(
      "Zu viele Anfragen. Bitte warte einen Moment und versuche es dann erneut.",
    );
  });

  it("liefert Retry-After bei 429 und lässt Eingabewerte aus der Meldung heraus", async () => {
    mockApi({
      "GET /api/x": { status: 429, body: errorBody("rate_limited", "x"), headers: { "Retry-After": "12" } },
    });
    const err = (await apiRequest("GET", "/api/x", { query: { email: "geheim@example.test" } }).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(err.retryAfter).toBe(12);
    expect(errorMessage(err, t)).toBe("Zu viele Anfragen. Bitte versuche es in etwa 12 Sekunden erneut.");
    expect(JSON.stringify(err)).not.toContain("geheim");
  });

  it("kennt für jeden Vertrags-Fehlercode eine deutsche Meldung", () => {
    const codes = [
      "bad_request", "unauthorized", "forbidden", "not_found", "conflict", "validation_error", "rate_limited",
      "routing_unavailable", "internal_error", "outside_coverage", "no_route", "own_ride", "ride_full",
      "ride_departed", "ride_cancelled", "already_requested", "detour_too_large", "invalid_state",
      "has_accepted_requests", "seats_below_accepted", "invalid_credentials", "payload_too_large",
    ];
    for (const code of codes) {
      expect(`error.${code}` in de, code).toBe(true);
    }
  });
});

describe("i18n", () => {
  it("fällt bei fehlenden englischen Texten auf Deutsch zurück", () => {
    expect(translate("en", "login.title")).toBe("Log in");
    expect(translate("en", "home.privacy1")).toBe(de["home.privacy1"]);
  });

  it("englische Schlüssel existieren auch auf Deutsch", () => {
    for (const key of Object.keys(en)) expect(key in de, key).toBe(true);
  });

  it("unterstützt Platzhalter und Pluralformen", () => {
    expect(t("search.resultCount", { count: 1 })).toBe("1 Fahrt gefunden");
    expect(t("search.resultCount", { count: 3 })).toBe("3 Fahrten gefunden");
    expect(t("nav.signedInAs", { name: "Alex" })).toBe("Angemeldet als Alex");
  });
});

describe("Hilfsfunktionen", () => {
  it("parseDecimal akzeptiert Komma und Punkt, sonst null", () => {
    expect(parseDecimal("53,5615")).toBe(53.5615);
    expect(parseDecimal(" -9.5 ")).toBe(-9.5);
    expect(parseDecimal("abc")).toBeNull();
    expect(parseDecimal("1e3")).toBeNull();
    expect(parseDecimal("")).toBeNull();
  });

  it("safeNext lässt nur interne Pfade zu", () => {
    expect(safeNext("/meine-fahrten")).toBe("/meine-fahrten");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/\\evil.example")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });

  it("localInputToIso liefert ISO mit Zeitzone", () => {
    expect(localInputToIso("2099-06-01T12:00")).toBe("2099-06-01T10:00:00.000Z"); // Europe/Berlin, Sommerzeit
    expect(localInputToIso("")).toBeNull();
  });

  it("hostOf liest den Kartenserver aus der Tile-URL", () => {
    expect(hostOf("https://tile.openstreetmap.org/{z}/{x}/{y}.png")).toBe("tile.openstreetmap.org");
    expect(hostOf("https://{s}.tiles.example/{z}/{x}/{y}.png")).toBe("a.tiles.example");
    expect(hostOf("kaputt")).toBe("");
  });
});

describe("Geocoder-Adapter", () => {
  it("liefert für alle Konfigurationswerte die Standardimplementierung none", async () => {
    for (const id of ["none", "photon", "nominatim"]) {
      const geocoder = createGeocoder(id);
      expect(geocoder.id).toBe("none");
      expect(geocoder.available).toBe(false);
      await expect(geocoder.search("Hamburg")).resolves.toEqual([]);
    }
  });
});

describe("Kartenquellenangabe (map.attribution)", () => {
  it("lässt Text und https-Links zu und löst Entitäten auf", () => {
    const html = safeAttributionHtml(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
    );
    expect(html).toBe(
      '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>-Mitwirkende',
    );
  });

  it("entschärft Skripte, Ereignis-Attribute, Bilder und fremde Schemata", () => {
    const html = safeAttributionHtml(
      '<img src=x onerror="alert(1)"><script>alert(2)</script>' +
        '<a href="javascript:alert(3)" onclick="x()">Link</a>' +
        '<a href="http://insecure.example/">kein https</a><b onmouseover="y()">fett</b> &lt;tag&gt;',
    );
    expect(html).not.toMatch(/<img|<script|onerror|onclick|onmouseover|javascript:|http:\/\//i);
    expect(html).toContain("Link");
    expect(html).toContain("kein https");
    expect(html).toContain("&lt;tag&gt;");
    expect(html).not.toContain("<a ");
  });

  it("fällt bei leerem oder fehlendem Wert auf einen Text ohne Link zurück", () => {
    expect(safeAttributionHtml("")).toBe(FALLBACK_ATTRIBUTION);
    expect(safeAttributionHtml(undefined)).toBe(FALLBACK_ATTRIBUTION);
    expect(safeAttributionHtml("<script>x</script>")).toBe(FALLBACK_ATTRIBUTION);
  });
});
