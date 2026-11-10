import { vi } from "vitest";
import type {
  MatchOut,
  Me,
  PublicConfig,
  RequestOut,
  RideOut,
} from "../api/types";

export interface MockResponse {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

export interface MockCall {
  method: string;
  path: string;
  query: URLSearchParams;
  body: unknown;
}

type Handler = MockResponse | ((call: MockCall) => MockResponse | Promise<MockResponse>);

export interface MockApi {
  calls: MockCall[];
  /** Aufrufe einer Route ("POST /api/rides") */
  callsTo: (key: string) => MockCall[];
  set: (key: string, handler: Handler) => void;
}

export function errorBody(code: string, message = "Fehler", details?: { field: string; message: string }[]) {
  return { error: { code, message, ...(details ? { details } : {}) } };
}

export const CONFIG: PublicConfig = {
  event: { name: "40C3", lat: 53.5615, lon: 9.9885, start_date: "2026-12-27", end_date: "2026-12-30" },
  matching: {
    prefilter_radius_m: 20000,
    max_detour_ratio: 0.2,
    max_detour_minutes: 25,
    detour_mode: "any",
    max_candidates: 30,
  },
  map: {
    tile_url: "https://tiles.example.test/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://tiles.example.test/copyright">Beispielkarte</a>-Mitwirkende',
    geocoder: "none",
  },
  limits: { min_seats: 1, max_seats: 8 },
  retention_days_after_event: 14,
};

export const USER: Me = {
  id: "u-1",
  display_name: "Alex",
  email: "alex@example.test",
  created_at: "2026-11-01T10:00:00Z",
};

export function ride(overrides: Partial<RideOut> = {}): RideOut {
  return {
    id: "r-1",
    start: { lat: 53.55, lon: 9.99 },
    end: { lat: 53.5615, lon: 9.9885 },
    departure_time: "2026-12-27T08:00:00Z",
    seats_total: 3,
    seats_available: 3,
    status: "open",
    distance_km: 12.34,
    duration_min: 21.4,
    route: [
      [53.55, 9.99],
      [53.5615, 9.9885],
    ],
    created_at: "2026-11-01T10:00:00Z",
    pending_requests: 0,
    accepted_requests: 0,
    ...overrides,
  };
}

export function match(overrides: Partial<MatchOut> = {}): MatchOut {
  return {
    ride_id: "r-9",
    driver_display_name: "Sam",
    departure_time: "2026-12-27T07:30:00Z",
    seats_available: 2,
    // Server liefert vergröbert: km in 0,5-Schritten, Minuten ganzzahlig, Verhältnis mit 2 Stellen
    detour_km: 2.5,
    detour_minutes: 4,
    detour_ratio: 0.12,
    route: [
      [53.56, 9.98],
      [53.5615, 9.9885],
    ],
    ...overrides,
  };
}

export function request(overrides: Partial<RequestOut> = {}): RequestOut {
  return {
    id: "q-1",
    ride_id: "r-1",
    status: "pending",
    pickup: { lat: 53.55, lon: 9.99 },
    detour_km: 1.5,
    detour_minutes: 3,
    created_at: "2026-11-02T10:00:00Z",
    decided_at: null,
    ride: { departure_time: "2026-12-27T08:00:00Z", seats_available: 2, driver_display_name: "Sam" },
    rider: { display_name: "Robin" },
    counterpart_email: null,
    ...overrides,
  };
}

export function page<T>(items: T[]) {
  return { items, total: items.length, limit: 100, offset: 0 };
}

/**
 * Ersetzt fetch durch einen Router nach "METHOD /pfad". Nicht registrierte Routen scheitern laut
 * (HTTP 500 und Eintrag in calls), damit unerwartete Requests im Test auffallen.
 */
export function mockApi(initial: Record<string, Handler> = {}): MockApi {
  const handlers = new Map<string, Handler>(Object.entries(initial));
  const calls: MockCall[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const call: MockCall = {
      method,
      path: url.pathname,
      query: url.searchParams,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    };
    calls.push(call);
    const handler = handlers.get(`${method} ${url.pathname}`);
    if (!handler) {
      return new Response(JSON.stringify(errorBody("internal_error", "unmocked")), { status: 500 });
    }
    const result = typeof handler === "function" ? await handler(call) : handler;
    const status = result.status ?? 200;
    const headers = { "Content-Type": "application/json", ...(result.headers ?? {}) };
    if (status === 204) return new Response(null, { status, headers });
    return new Response(JSON.stringify(result.body ?? {}), { status, headers });
  });
  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    callsTo: (key) => calls.filter((c) => `${c.method} ${c.path}` === key),
    set: (key, handler) => handlers.set(key, handler),
  };
}

/** Standardroutinen: Konfiguration und (angemeldete oder anonyme) Sitzung. */
export function baseRoutes(user: Me | null = USER): Record<string, Handler> {
  return {
    "GET /api/config": { body: CONFIG },
    "GET /api/me": user ? { body: user } : { status: 401, body: errorBody("unauthorized", "Nicht angemeldet") },
  };
}
