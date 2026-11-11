// Typen nach docs/API-CONTRACT.md (verbindlich). Koordinaten überall (lat, lon).

export interface LatLon {
  lat: number;
  lon: number;
}

/** Treffer der Adresssuche (GET /api/geocode) */
export interface GeocodeItem {
  label: string;
  lat: number;
  lon: number;
}

/** Routenlinie als [[lat, lon], …] */
export type RoutePoints = [number, number][];

export interface PublicConfig {
  event: {
    name: string;
    lat: number;
    lon: number;
    start_date: string;
    end_date: string;
  };
  matching: {
    prefilter_radius_m: number;
    max_detour_ratio: number;
    max_detour_minutes: number;
    detour_mode: "any" | "all";
    max_candidates: number;
  };
  map: {
    tile_url: string;
    /** Quellenangabe (Text, optional https-Links); Pflichtangabe der Kartendaten-Lizenz. */
    attribution: string;
    geocoder: "none" | "photon" | "nominatim";
  };
  limits: { min_seats: number; max_seats: number };
  retention_days_after_event: number;
}

export interface Me {
  id: string;
  display_name: string;
  email: string;
  created_at: string;
}

export interface LoginUser {
  id: string;
  display_name: string;
}

export interface RegisterBody {
  email: string;
  password: string;
  display_name: string;
  consent: boolean;
}

export type RideStatus = "open" | "full" | "cancelled";

export interface RideOut {
  id: string;
  start: LatLon;
  end: LatLon;
  departure_time: string;
  seats_total: number;
  seats_available: number;
  status: RideStatus;
  distance_km: number;
  duration_min: number;
  route: RoutePoints;
  created_at: string;
  pending_requests: number;
  accepted_requests: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface MatchOut {
  ride_id: string;
  driver_display_name: string;
  departure_time: string;
  seats_available: number;
  detour_km: number;
  detour_minutes: number;
  detour_ratio: number;
  route: RoutePoints;
}

export interface MatchResponse {
  items: MatchOut[];
  total: number;
}

export type RequestStatus = "pending" | "accepted" | "declined" | "cancelled";

export interface RequestOut {
  id: string;
  ride_id: string;
  status: RequestStatus;
  pickup: LatLon;
  detour_km: number;
  detour_minutes: number;
  created_at: string;
  decided_at: string | null;
  ride: {
    departure_time: string;
    seats_available: number;
    driver_display_name: string;
  };
  rider: { display_name: string };
  /** Nur gesetzt, wenn status === "accepted" (Vertrag). */
  counterpart_email: string | null;
}

export interface ApiErrorDetail {
  field: string;
  message: string;
}
