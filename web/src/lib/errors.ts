import { ApiError } from "../api/client";
import type { MessageKey, TFunction } from "../i18n";
import { de } from "../i18n/de";

function isKey(key: string): key is MessageKey {
  return key in de;
}

/**
 * Bildet einen Fehler auf eine deutsche Meldung ab. Es werden nur Codes aus dem Vertrag
 * ausgewertet, nie Eingabewerte. Unbekannte Codes fallen auf die Server-Meldung (laut Vertrag
 * Deutsch) und zuletzt auf einen allgemeinen Text zurück.
 */
export function errorMessage(err: unknown, t: TFunction): string {
  if (!(err instanceof ApiError)) return t("error.unknown");
  if (err.code === "rate_limited" && err.retryAfter !== null) {
    return t("error.rate_limited_wait", { seconds: err.retryAfter });
  }
  const key = `error.${err.code}`;
  if (isKey(key)) return t(key);
  if (err.status === 409) return t("error.conflict");
  if (err.status >= 500) return t("error.internal_error");
  return err.message || t("error.unknown");
}

/** Letztes Pfadsegment der Server-Feldnamen, z. B. "body.start.lat" -> "lat". */
export function invalidFields(err: unknown): string[] {
  if (!(err instanceof ApiError) || err.code !== "validation_error") return [];
  return [...new Set(err.details.map((d) => d.field.split(".").filter(Boolean).slice(-1)[0] ?? ""))].filter(Boolean);
}

export function isApiError(err: unknown, code: string): boolean {
  return err instanceof ApiError && err.code === code;
}
