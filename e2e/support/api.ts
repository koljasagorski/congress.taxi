import { expect, type APIRequestContext, type APIResponse } from "@playwright/test";

export interface User {
  email: string;
  password: string;
  displayName: string;
}

export interface LatLon {
  lat: number;
  lon: number;
}

export interface RideJson {
  id: string;
  status: string;
  seats_total: number;
  seats_available: number;
  pending_requests: number;
  accepted_requests: number;
  distance_km: number;
  duration_min: number;
  route: [number, number][];
}

export interface RequestJson {
  id: string;
  ride_id: string;
  status: string;
  counterpart_email: string | null;
}

export interface MatchJson {
  items: { ride_id: string; driver_display_name: string; detour_km: number; detour_minutes: number }[];
  total: number;
}

// Bekannte, an Straßen liegende Punkte im Hamburg-Extrakt (Abstand zur nächsten Straße < 100 m).
// Dieselben Orte erreicht man per Klick auf die Karte (Zoom 11, Mitte = Veranstaltungsort).
export const POINTS = {
  start: { lat: 53.55334, lon: 9.91984 }, // Holstenring
  pickup: { lat: 53.56966, lon: 9.9267 }, // Am Diebsteich
} satisfies Record<string, LatLon>;

/** Pixel-Versatz von der Kartenmitte (Veranstaltungsort), der obige Punkte trifft (Zoom 11). */
export const MAP_OFFSETS = {
  start: { dx: -100, dy: 20 },
  pickup: { dx: -90, dy: -20 },
};

export async function registerUser(api: APIRequestContext, user: User): Promise<void> {
  const res = await api.post("/api/auth/register", {
    data: {
      email: user.email,
      password: user.password,
      display_name: user.displayName,
      consent: true,
    },
  });
  expect(res.status(), "Registrierung per API").toBe(202);
}

export function login(api: APIRequestContext, user: User): Promise<APIResponse> {
  return api.post("/api/auth/login", { data: { email: user.email, password: user.password } });
}

export async function loginOk(api: APIRequestContext, user: User): Promise<void> {
  const res = await login(api, user);
  expect(res.status(), "Login per API").toBe(200);
}

export async function createRide(
  api: APIRequestContext,
  start: LatLon,
  departure: Date,
  seats: number,
): Promise<RideJson> {
  const res = await api.post("/api/rides", {
    data: { start, departure_time: departure.toISOString(), seats },
  });
  expect(res.status(), "Fahrt per API anlegen").toBe(201);
  return (await res.json()) as RideJson;
}

export async function myRides(api: APIRequestContext): Promise<RideJson[]> {
  const res = await api.get("/api/rides/mine");
  expect(res.status()).toBe(200);
  return ((await res.json()) as { items: RideJson[] }).items;
}

export async function match(
  api: APIRequestContext,
  pickup: LatLon,
  from: Date,
  to: Date,
): Promise<MatchJson> {
  const query = new URLSearchParams({
    lat: String(pickup.lat),
    lon: String(pickup.lon),
    time_from: from.toISOString(),
    time_to: to.toISOString(),
  });
  const res = await api.get(`/api/match?${query.toString()}`);
  expect(res.status(), "Matching per API").toBe(200);
  return (await res.json()) as MatchJson;
}

export async function createRequest(
  api: APIRequestContext,
  rideId: string,
  pickup: LatLon,
): Promise<RequestJson> {
  const res = await api.post(`/api/rides/${rideId}/requests`, { data: { pickup } });
  expect(res.status(), "Anfrage per API").toBe(201);
  return (await res.json()) as RequestJson;
}

export async function acceptRequest(api: APIRequestContext, requestId: string): Promise<RequestJson> {
  const res = await api.post(`/api/requests/${requestId}/accept`);
  expect(res.status(), "Anfrage annehmen per API").toBe(200);
  return (await res.json()) as RequestJson;
}
