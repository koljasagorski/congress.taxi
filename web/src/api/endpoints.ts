// Ein dünner, typisierter Wrapper je Vertrags-Endpunkt (docs/API-CONTRACT.md).

import { api } from "./client";
import type {
  GeocodeItem,
  LatLon,
  LoginUser,
  MatchResponse,
  Me,
  Page,
  PublicConfig,
  RegisterBody,
  RequestOut,
  RequestStatus,
  RideOut,
} from "./types";

export const endpoints = {
  config: (signal?: AbortSignal) => api.get<PublicConfig>("/api/config", { signal }),

  geocode: (q: string, signal?: AbortSignal) =>
    api.get<{ items: GeocodeItem[] }>("/api/geocode", { query: { q }, signal }),

  register: (body: RegisterBody) => api.post<{ message: string }>("/api/auth/register", body),
  login: (email: string, password: string) =>
    api.post<{ user: LoginUser }>("/api/auth/login", { email, password }),
  logout: () => api.post<void>("/api/auth/logout"),
  me: (signal?: AbortSignal) => api.get<Me>("/api/me", { signal }),
  deleteMe: () => api.delete<void>("/api/me"),

  createRide: (body: { start: LatLon; departure_time: string; seats: number }) =>
    api.post<RideOut>("/api/rides", body),
  myRides: (signal?: AbortSignal) =>
    api.get<Page<RideOut>>("/api/rides/mine", { query: { limit: 100 }, signal }),
  cancelRide: (id: string) => api.delete<void>(`/api/rides/${encodeURIComponent(id)}`),

  match: (
    query: { lat: number; lon: number; time_from?: string; time_to?: string; min_seats?: number },
    signal?: AbortSignal,
  ) => api.get<MatchResponse>("/api/match", { query: { ...query, limit: 20 }, signal }),

  createRequest: (rideId: string, pickup: LatLon) =>
    api.post<RequestOut>(`/api/rides/${encodeURIComponent(rideId)}/requests`, { pickup }),
  incoming: (status?: RequestStatus, signal?: AbortSignal) =>
    api.get<Page<RequestOut>>("/api/requests/incoming", { query: { status, limit: 100 }, signal }),
  outgoing: (status?: RequestStatus, signal?: AbortSignal) =>
    api.get<Page<RequestOut>>("/api/requests/outgoing", { query: { status, limit: 100 }, signal }),
  acceptRequest: (id: string) =>
    api.post<RequestOut>(`/api/requests/${encodeURIComponent(id)}/accept`),
  declineRequest: (id: string) =>
    api.post<RequestOut>(`/api/requests/${encodeURIComponent(id)}/decline`),
  cancelRequest: (id: string) =>
    api.post<RequestOut>(`/api/requests/${encodeURIComponent(id)}/cancel`),
};
