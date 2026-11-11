// Geocoder-Adapter (Adressen -> Koordinaten).
//
// Der Browser fragt NIE einen Geocoder-Dienst direkt: Das würde Adresseingaben und die IP-Adresse
// an Dritte senden. Stattdessen fragt "nominatim" den eigenen Backend-Endpunkt GET /api/geocode,
// der den Dienst serverseitig aufruft (siehe api/app/geocoding.py). "none" und "photon" (nicht
// angebunden) schalten die Adresssuche in der Oberfläche aus; Karten-Klick, Kartenmitte und
// Koordinaten funktionieren immer.

import { endpoints } from "../api/endpoints";
import type { LatLon } from "../api/types";

export interface GeocodeResult {
  label: string;
  position: LatLon;
}

export interface Geocoder {
  readonly id: string;
  /** false: die UI zeigt keine Adresssuche an. */
  readonly available: boolean;
  search(query: string, signal?: AbortSignal): Promise<GeocodeResult[]>;
}

export class NoneGeocoder implements Geocoder {
  readonly id = "none";
  readonly available = false;
  search(): Promise<GeocodeResult[]> {
    return Promise.resolve([]);
  }
}

/** Adresssuche über den Backend-Proxy (Nominatim). */
export class ApiGeocoder implements Geocoder {
  readonly id = "nominatim";
  readonly available = true;
  async search(query: string, signal?: AbortSignal): Promise<GeocodeResult[]> {
    const { items } = await endpoints.geocode(query, signal);
    return items.map((item) => ({ label: item.label, position: { lat: item.lat, lon: item.lon } }));
  }
}

/** "photon" ist nicht angebunden und wirkt wie "none". */
export function createGeocoder(id: string): Geocoder {
  return id === "nominatim" ? new ApiGeocoder() : new NoneGeocoder();
}
