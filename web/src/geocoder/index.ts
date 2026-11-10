// Geocoder-Adapter (Adressen -> Koordinaten).
//
// Bewusst NUR die Schnittstelle und die Standardimplementierung "none": Ein Geocoder wie
// Photon oder Nominatim wäre aus dem Browser ein externer Request, der Eingaben (Adressen!) und
// die IP-Adresse an Dritte sendet. Das verbietet das Datenschutzkonzept, solange es keinen
// Backend-Proxy gibt. Die Ortswahl geschieht deshalb per Karten-Klick, Kartenmitte oder Koordinaten.
//
// Einen echten Adapter anzubinden bedeutet: Backend-Endpunkt (Proxy) schaffen, hier eine Klasse
// ergänzen, in createGeocoder() registrieren und in der CSP/Datenschutzerklärung nachführen.

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

/** "photon" und "nominatim" (laut /api/config möglich) sind nicht angebunden und liefern "none". */
export function createGeocoder(_id: string): Geocoder {
  void _id;
  return new NoneGeocoder();
}
