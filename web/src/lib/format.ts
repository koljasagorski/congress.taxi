import type { LatLon } from "../api/types";

const LOCALE = "de-DE";

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" }).format(d);
}

export function formatDate(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "long" }).format(d);
}

/** Zahl mit höchstens einer Nachkommastelle, deutsches Dezimalkomma. */
export function formatNumber(value: number, digits = 1): string {
  return new Intl.NumberFormat(LOCALE, { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(value);
}

export function formatPercent(ratio: number): string {
  return formatNumber(ratio * 100, 0);
}

export function formatCoord(value: number): string {
  return value.toFixed(5);
}

/** <input type="datetime-local">-Wert (lokale Zeit) -> ISO 8601 mit Zeitzone (UTC). */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** Aktuelle lokale Zeit im Format für datetime-local (auf Minuten gerundet). */
export function nowForInput(offsetMinutes = 0): string {
  const d = new Date(Date.now() + offsetMinutes * 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Dezimalzahl mit Komma oder Punkt. Liefert null bei ungültiger Eingabe. */
export function parseDecimal(text: string): number | null {
  const normalized = text.trim().replace(",", ".");
  if (!/^[+-]?\d+(\.\d+)?$/.test(normalized)) return null;
  return Number(normalized);
}

export function isValidLatLon(p: LatLon): boolean {
  return p.lat >= -90 && p.lat <= 90 && p.lon >= -180 && p.lon <= 180;
}

/** Sicheres Weiterleitungsziel: nur interne Pfade. */
export function safeNext(next: string | null): string {
  if (next && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\")) return next;
  return "/";
}

export function hostOf(url: string): string {
  try {
    return new URL(url.replace("{z}", "0").replace("{x}", "0").replace("{y}", "0").replace("{s}", "a")).host;
  } catch {
    return "";
  }
}
