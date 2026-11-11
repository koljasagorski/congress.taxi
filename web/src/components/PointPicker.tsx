import {
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type Ref,
} from "react";
import { ApiError } from "../api/client";
import type { LatLon } from "../api/types";
import { createGeocoder, type GeocodeResult } from "../geocoder";
import { useI18n } from "../i18n";
import { formatCoord, isValidLatLon, parseDecimal } from "../lib/format";
import { MapView, type MapMarker, type MapRoute, type MapViewHandle } from "./MapView";

/** Ergebnis von `commit()`: gültiger Punkt (oder keiner gesetzt) bzw. ungültige Eingabe (Fehler ist angezeigt). */
export type PointCommit = { ok: true; point: LatLon | null } | { ok: false };

export interface PointPickerHandle {
  /**
   * Übernimmt getippte, noch nicht bestätigte Koordinaten (z. B. beim Absenden des Formulars), damit
   * nie still ein anderer Punkt verwendet wird, als die Eingabefelder zeigen.
   */
  commit(): PointCommit;
}

interface PointPickerProps {
  /** Beschriftung der Gruppe (z. B. "Startpunkt"). */
  legend: string;
  value: LatLon | null;
  onChange: (point: LatLon) => void;
  tileUrl: string;
  attribution: string;
  eventPosition: LatLon;
  eventName: string;
  /** Geocoder aus GET /api/config (`map.geocoder`); bei "none" gibt es keine Adresssuche. */
  geocoderId?: string;
  pointKind: "start" | "pickup";
  pointLabel: string;
  routes?: MapRoute[];
  fitSignature?: string;
  /** Fehlermeldung von außen (z. B. "Bitte Punkt setzen" oder outside_coverage). */
  error?: string | undefined;
  ref?: Ref<PointPickerHandle>;
}

/** Bei der Punktwahl nicht tiefer als Straßenebene zoomen: Kachelabrufe verraten den Ausschnitt an den Kartenserver. */
export const PICK_MAX_ZOOM = 16;

/** Mindestlänge des Suchtexts (wie der Server). */
const MIN_QUERY_LENGTH = 3;

type SearchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "results"; items: GeocodeResult[] }
  | { kind: "empty" }
  | { kind: "tooShort" }
  | { kind: "picked"; label: string }
  | { kind: "error"; message: "map.searchBusy" | "map.searchFailed" };

/**
 * Punktwahl: Adresssuche (optional) ODER Klick auf die Karte ODER Koordinateneingabe ODER
 * Kartenmitte (Tastatur-Alternative).
 */
export function PointPicker({
  legend,
  value,
  onChange,
  tileUrl,
  attribution,
  eventPosition,
  eventName,
  geocoderId = "none",
  pointKind,
  pointLabel,
  routes,
  fitSignature,
  error,
  ref,
}: PointPickerProps) {
  const { t } = useI18n();
  const mapRef = useRef<MapViewHandle | null>(null);
  const uid = useId();
  const errorId = `${uid}-error`;
  const coordErrorId = `${uid}-coord-error`;

  const geocoder = useMemo(() => createGeocoder(geocoderId), [geocoderId]);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<SearchState>({ kind: "idle" });
  const searchAbort = useRef<AbortController | null>(null);
  useEffect(() => () => searchAbort.current?.abort(), []);

  const runSearch = async () => {
    const text = query.trim();
    searchAbort.current?.abort();
    if (text.length < MIN_QUERY_LENGTH) {
      setSearch({ kind: "tooShort" });
      return;
    }
    const controller = new AbortController();
    searchAbort.current = controller;
    setSearch({ kind: "loading" });
    try {
      const items = await geocoder.search(text, controller.signal);
      if (controller.signal.aborted) return;
      setSearch(items.length > 0 ? { kind: "results", items } : { kind: "empty" });
    } catch (err) {
      if (controller.signal.aborted) return;
      const busy =
        err instanceof ApiError && (err.code === "geocoder_busy" || err.code === "rate_limited");
      setSearch({ kind: "error", message: busy ? "map.searchBusy" : "map.searchFailed" });
    }
  };

  const pickResult = (item: GeocodeResult) => {
    onChange(item.position);
    mapRef.current?.focus(item.position);
    setSearch({ kind: "picked", label: item.label });
  };

  const onSearchKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault(); // nie das umgebende Formular absenden
      void runSearch();
    }
  };

  const [latText, setLatText] = useState(value ? String(value.lat) : "");
  const [lonText, setLonText] = useState(value ? String(value.lon) : "");
  const [prevValue, setPrevValue] = useState(value);
  const [coordError, setCoordError] = useState<string | null>(null);

  // Wird der Punkt per Kartenklick gesetzt, ziehen die Eingabefelder nach.
  if (value !== prevValue) {
    setPrevValue(value);
    setLatText(value ? String(value.lat) : "");
    setLonText(value ? String(value.lon) : "");
    setCoordError(null);
  }

  const apply = (): PointCommit => {
    const lat = parseDecimal(latText);
    const lon = parseDecimal(lonText);
    if (lat === null || lon === null) {
      setCoordError(t("valid.coordNumber"));
      return { ok: false };
    }
    if (!isValidLatLon({ lat, lon })) {
      setCoordError(t("valid.coordRange"));
      return { ok: false };
    }
    setCoordError(null);
    onChange({ lat, lon });
    return { ok: true, point: { lat, lon } };
  };

  useImperativeHandle(ref, () => ({
    commit: () => {
      const dirty =
        latText !== (value ? String(value.lat) : "") || lonText !== (value ? String(value.lon) : "");
      return dirty ? apply() : { ok: true, point: value };
    },
  }));

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      apply();
    }
  };

  const markers: MapMarker[] = [
    {
      id: "event",
      position: eventPosition,
      label: t("map.markerEvent", { name: eventName }),
      kind: "event",
    },
  ];
  if (value) markers.push({ id: "point", position: value, label: pointLabel, kind: pointKind });

  const describedBy = [error ? errorId : null, coordError ? coordErrorId : null].filter(Boolean).join(" ");

  return (
    <fieldset className="point-picker" aria-describedby={describedBy || undefined}>
      <legend>{legend}</legend>
      {geocoder.available && (
        <div role="group" aria-label={t("map.searchGroup")} className="geo-search">
          <div className="field">
            <label htmlFor={`${uid}-search`}>{t("map.searchLabel")}</label>
            <div className="geo-search-row">
              <input
                id={`${uid}-search`}
                type="search"
                autoComplete="off"
                maxLength={200}
                placeholder={t("map.searchPlaceholder")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={onSearchKeyDown}
                aria-describedby={`${uid}-search-help`}
              />
              <button
                type="button"
                className="btn btn-secondary"
                disabled={search.kind === "loading"}
                onClick={() => void runSearch()}
              >
                {t("map.searchButton")}
              </button>
            </div>
          </div>
          <p id={`${uid}-search-help`} className="hint">
            {t("map.searchHelp")} {t("map.searchPrivacy")}
          </p>
          <div role="status" aria-live="polite" className="geo-status">
            {search.kind === "loading" && <p className="hint">{t("map.searchLoading")}</p>}
            {search.kind === "tooShort" && <p className="field-error">{t("map.searchTooShort")}</p>}
            {search.kind === "empty" && <p className="hint">{t("map.searchNoResults")}</p>}
            {search.kind === "error" && <p className="field-error">{t(search.message)}</p>}
            {search.kind === "picked" && (
              <p className="hint">{t("map.searchPicked", { label: search.label })}</p>
            )}
            {search.kind === "results" && (
              <p className="hint">{t("map.searchResults", { count: search.items.length })}</p>
            )}
          </div>
          {search.kind === "results" && (
            <ul className="geo-results" aria-label={t("map.searchResultsList")}>
              {search.items.map((item, i) => (
                <li key={`${i}-${item.position.lat}-${item.position.lon}`}>
                  <button type="button" className="geo-result" onClick={() => pickResult(item)}>
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <p className="hint">{t("map.clickHint")}</p>
      <MapView
        ref={mapRef}
        tileUrl={tileUrl}
        attribution={attribution}
        center={value ?? eventPosition}
        maxZoom={PICK_MAX_ZOOM}
        markers={markers}
        routes={routes ?? []}
        onPick={onChange}
        fitSignature={fitSignature}
        ariaLabel={t("map.regionLabel")}
        showCrosshair
      />
      <p className="hint">
        {value
          ? t("map.pointSet", { lat: formatCoord(value.lat), lon: formatCoord(value.lon) })
          : t("map.pointNone")}
      </p>
      <div role="group" aria-label={t("map.coordsGroup")} className="coords">
        <p className="hint">{t("map.coordsHelp")}</p>
        <div className="coords-fields">
          <div className="field">
            <label htmlFor={`${uid}-lat`}>{t("field.lat")}</label>
            <input
              id={`${uid}-lat`}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={latText}
              onChange={(e) => setLatText(e.target.value)}
              onKeyDown={onKeyDown}
              aria-invalid={coordError || error ? true : undefined}
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-lon`}>{t("field.lon")}</label>
            <input
              id={`${uid}-lon`}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={lonText}
              onChange={(e) => setLonText(e.target.value)}
              onKeyDown={onKeyDown}
              aria-invalid={coordError || error ? true : undefined}
            />
          </div>
        </div>
        <div className="actions">
          <button type="button" className="btn btn-secondary" onClick={() => apply()}>
            {t("map.applyCoords")}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              const center = mapRef.current?.getCenter();
              if (center) onChange(center);
            }}
          >
            {t("map.useCenter")}
          </button>
        </div>
        {coordError && (
          <p id={coordErrorId} className="field-error">
            {coordError}
          </p>
        )}
      </div>
      {error && (
        <p id={errorId} className="field-error" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}
