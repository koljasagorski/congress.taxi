// Typisierter API-Client: fetch, gleiche Origin, Fehlerformat {error:{code,message,details}}.
// Es werden bewusst keine Eingabewerte in Fehlern oder Logs mitgeführt.

import type { ApiErrorDetail } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetail[];
  /** Sekunden aus dem Retry-After-Header (429), falls vorhanden. */
  readonly retryAfter: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: ApiErrorDetail[] = [],
    retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

export interface RequestOptions {
  body?: unknown;
  query?: Query;
  signal?: AbortSignal;
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

/** Der Auth-Kontext registriert hier, was bei 401 geschieht (Zustand leeren → Login-Weiterleitung). */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

function buildUrl(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

function parseErrorBody(
  status: number,
  data: unknown,
  retryAfter: number | null,
): ApiError {
  if (typeof data === "object" && data !== null && "error" in data) {
    const err = (data as { error?: unknown }).error;
    if (typeof err === "object" && err !== null) {
      const e = err as { code?: unknown; message?: unknown; details?: unknown };
      const details: ApiErrorDetail[] = Array.isArray(e.details)
        ? e.details.flatMap((d: unknown) => {
            if (typeof d === "object" && d !== null) {
              const { field, message } = d as { field?: unknown; message?: unknown };
              if (typeof field === "string") {
                return [{ field, message: typeof message === "string" ? message : "" }];
              }
            }
            return [];
          })
        : [];
      return new ApiError(
        status,
        typeof e.code === "string" ? e.code : "error",
        typeof e.message === "string" ? e.message : "",
        details,
        retryAfter,
      );
    }
  }
  return new ApiError(status, codeForStatus(status), "", [], retryAfter);
}

/**
 * Antworten von Zwischenstationen (Caddy, nginx) haben kein JSON-Fehlerformat, z. B. 413 vom
 * Body-Limit des Proxys oder 502/503/504, wenn die API nicht erreichbar ist. Der Statuscode
 * bestimmt dann den Vertragscode.
 */
function codeForStatus(status: number): string {
  switch (status) {
    case 400:
      return "bad_request";
    case 401:
      return "unauthorized";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 409:
      return "conflict";
    case 413:
      return "payload_too_large";
    case 422:
      return "validation_error";
    case 429:
      return "rate_limited";
    case 502:
    case 503:
    case 504:
      return "service_unavailable";
    default:
      return status >= 500 ? "internal_error" : "error";
  }
}

export async function apiRequest<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "network_error", "");
  }

  if (response.status === 204) return undefined as T;

  let data: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    const retryHeader = response.headers.get("Retry-After");
    const retryAfter = retryHeader !== null && /^\d+$/.test(retryHeader) ? Number(retryHeader) : null;
    const error = parseErrorBody(response.status, data, retryAfter);
    // 401 ohne gültige Anmeldung. Login-Fehler tragen den Code invalid_credentials und lösen
    // keine Weiterleitung aus.
    if (response.status === 401 && error.code !== "invalid_credentials") {
      onUnauthorized?.();
    }
    throw error;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => apiRequest<T>("GET", path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>("POST", path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>("PATCH", path, { ...options, body }),
  delete: <T>(path: string, options?: RequestOptions) => apiRequest<T>("DELETE", path, options),
};
