import { useId, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { endpoints } from "../api/endpoints";
import type { LatLon, MatchOut, PublicConfig, RequestOut } from "../api/types";
import { ConfigGate } from "../components/ConfigGate";
import type { MapRoute } from "../components/MapView";
import { PointPicker, type PointPickerHandle } from "../components/PointPicker";
import { Notice, TextField, useFocusFirstInvalid } from "../components/ui";
import { useI18n } from "../i18n";
import { errorMessage, isApiError } from "../lib/errors";
import { formatDateTime, formatNumber, formatPercent, localInputToIso } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

type Errors = Partial<Record<"pickup" | "timeFrom" | "timeTo" | "minSeats", string>>;

interface SearchResult {
  id: number;
  pickup: LatLon;
  items: MatchOut[];
}

function MatchItem({
  match,
  highlighted,
  onToggleHighlight,
  request,
  requesting,
  error,
  onRequest,
}: {
  match: MatchOut;
  highlighted: boolean;
  onToggleHighlight: () => void;
  request: RequestOut | undefined;
  requesting: boolean;
  error: string | undefined;
  onRequest: () => void;
}) {
  const { t } = useI18n();
  const headingId = useId();
  const time = formatDateTime(match.departure_time);
  return (
    <li className="card" aria-labelledby={headingId}>
      <h3 id={headingId}>{t("search.driver", { name: match.driver_display_name })}</h3>
      <p>
        {t("search.departure", { time })} · {t("search.seatsFree", { seats: match.seats_available })}
      </p>
      <p className="detour">
        {t("search.detour", {
          km: formatNumber(match.detour_km),
          minutes: formatNumber(match.detour_minutes, 0),
          percent: formatPercent(match.detour_ratio),
        })}
      </p>
      <div className="actions">
        <button
          type="button"
          className="btn btn-secondary"
          aria-pressed={highlighted}
          onClick={onToggleHighlight}
          aria-describedby={headingId}
        >
          {highlighted ? t("search.hideRoute") : t("search.showRoute")}
        </button>
        {!request && (
          <button type="button" className="btn" disabled={requesting} onClick={onRequest} aria-describedby={headingId}>
            {requesting ? t("search.requesting") : t("search.request")}
          </button>
        )}
      </div>
      {request && (
        <Notice kind="success">
          {t("search.requested", {
            km: formatNumber(request.detour_km),
            minutes: formatNumber(request.detour_minutes, 0),
          })}{" "}
          <Link to="/meine-fahrten">{t("search.toMine")}</Link>
        </Notice>
      )}
      {error && <Notice kind="error">{error}</Notice>}
    </li>
  );
}

function SearchRideForm({ config }: { config: PublicConfig }) {
  const { t } = useI18n();
  const formRef = useRef<HTMLFormElement>(null);
  const pickerRef = useRef<PointPickerHandle>(null);
  const { min_seats, max_seats } = config.limits;
  const eventPosition: LatLon = { lat: config.event.lat, lon: config.event.lon };

  const [pickup, setPickup] = useState<LatLon | null>(null);
  const [timeFrom, setTimeFrom] = useState("");
  const [timeTo, setTimeTo] = useState("");
  const [minSeats, setMinSeats] = useState("1");
  const [errors, setErrors] = useState<Errors>({});
  const [attempt, setAttempt] = useState(0);
  const [searching, setSearching] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [pointError, setPointError] = useState<string | null>(null);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [requests, setRequests] = useState<Record<string, RequestOut>>({});
  const [requestErrors, setRequestErrors] = useState<Record<string, string>>({});
  const [requesting, setRequesting] = useState<Record<string, boolean>>({});
  useFocusFirstInvalid(formRef, attempt);

  const routes = useMemo<MapRoute[]>(
    () =>
      (result?.items ?? []).map((m) => ({
        id: m.ride_id,
        points: m.route,
        label: t("map.routeLabel", { name: m.driver_display_name }),
        highlighted: m.ride_id === highlighted,
        muted: highlighted !== null && m.ride_id !== highlighted,
      })),
    [result, highlighted, t],
  );

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (searching) return;
    setServerError(null);
    setPointError(null);

    // Getippte, noch nicht übernommene Koordinaten zählen: nie still den alten Punkt verwenden.
    const picked = pickerRef.current?.commit();
    const coordsInvalid = picked?.ok === false;
    const point = picked?.ok ? picked.point : pickup;

    const found: Errors = {};
    if (!coordsInvalid && !point) found.pickup = t("valid.coordRequired");
    const fromIso = localInputToIso(timeFrom);
    const toIso = localInputToIso(timeTo);
    if (fromIso && toIso && new Date(toIso) <= new Date(fromIso)) found.timeTo = t("valid.timeOrder");
    const seatCount = Number(minSeats);
    if (!Number.isInteger(seatCount) || seatCount < min_seats || seatCount > max_seats) {
      found.minSeats = t("valid.seatsRange", { min: min_seats, max: max_seats });
    }
    setErrors(found);
    if (coordsInvalid || Object.keys(found).length > 0 || !point) {
      setAttempt((a) => a + 1);
      return;
    }

    setSearching(true);
    try {
      const response = await endpoints.match({
        lat: point.lat,
        lon: point.lon,
        ...(fromIso ? { time_from: fromIso } : {}),
        ...(toIso ? { time_to: toIso } : {}),
        min_seats: seatCount,
      });
      setResult({ id: (result?.id ?? 0) + 1, pickup: point, items: response.items });
      setHighlighted(null);
      setRequests({});
      setRequestErrors({});
    } catch (err) {
      const message = errorMessage(err, t);
      if (isApiError(err, "outside_coverage")) {
        setPointError(message);
        setAttempt((a) => a + 1);
      } else {
        setServerError(message);
      }
    } finally {
      setSearching(false);
    }
  };

  const sendRequest = async (match: MatchOut) => {
    if (!result) return;
    const id = match.ride_id;
    setRequestErrors((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setRequesting((prev) => ({ ...prev, [id]: true }));
    try {
      const created = await endpoints.createRequest(id, result.pickup);
      setRequests((prev) => ({ ...prev, [id]: created }));
    } catch (err) {
      setRequestErrors((prev) => ({ ...prev, [id]: errorMessage(err, t) }));
    } finally {
      setRequesting((prev) => ({ ...prev, [id]: false }));
    }
  };

  return (
    <section aria-labelledby="search-title">
      <h1 id="search-title">{t("search.title")}</h1>
      <p>{t("search.lead")}</p>
      <form ref={formRef} onSubmit={(e) => void onSubmit(e)} noValidate className="form">
        <PointPicker
          ref={pickerRef}
          legend={t("search.pickupLabel")}
          value={pickup}
          onChange={(p) => {
            setPickup(p);
            setPointError(null);
            setErrors((prev) => ({ ...prev, pickup: undefined }));
          }}
          tileUrl={config.map.tile_url}
          attribution={config.map.attribution}
          eventPosition={eventPosition}
          eventName={config.event.name}
          geocoderId={config.map.geocoder}
          pointKind="pickup"
          pointLabel={t("map.markerPickup")}
          routes={routes}
          fitSignature={result ? String(result.id) : undefined}
          error={pointError ?? errors.pickup}
        />
        <div className="grid-2">
          <TextField
            label={t("field.timeFrom")}
            type="datetime-local"
            name="time_from"
            value={timeFrom}
            onChange={(e) => setTimeFrom(e.target.value)}
            error={errors.timeFrom}
          />
          <TextField
            label={t("field.timeTo")}
            type="datetime-local"
            name="time_to"
            value={timeTo}
            onChange={(e) => setTimeTo(e.target.value)}
            error={errors.timeTo}
          />
        </div>
        <TextField
          label={t("field.minSeats")}
          type="number"
          name="min_seats"
          inputMode="numeric"
          min={min_seats}
          max={max_seats}
          step={1}
          value={minSeats}
          onChange={(e) => setMinSeats(e.target.value)}
          error={errors.minSeats}
        />
        {serverError && !pointError && <Notice kind="error">{serverError}</Notice>}
        <div className="actions">
          <button type="submit" className="btn" disabled={searching}>
            {searching ? t("search.submitting") : t("search.submit")}
          </button>
        </div>
      </form>

      <div aria-live="polite" className="results">
        {result && (
          <section aria-labelledby="results-title">
            <h2 id="results-title">{t("search.resultsTitle")}</h2>
            {result.items.length === 0 ? (
              <p>{t("search.empty")}</p>
            ) : (
              <>
                <p>{t("search.resultCount", { count: result.items.length })}</p>
                <p className="hint">{t("search.routeNote")}</p>
                <ul className="card-list">
                  {result.items.map((match) => (
                    <MatchItem
                      key={match.ride_id}
                      match={match}
                      highlighted={highlighted === match.ride_id}
                      onToggleHighlight={() =>
                        setHighlighted((cur) => (cur === match.ride_id ? null : match.ride_id))
                      }
                      request={requests[match.ride_id]}
                      requesting={requesting[match.ride_id] === true}
                      error={requestErrors[match.ride_id]}
                      onRequest={() => void sendRequest(match)}
                    />
                  ))}
                </ul>
              </>
            )}
          </section>
        )}
      </div>
    </section>
  );
}

export function SearchRidePage() {
  const { t } = useI18n();
  usePageTitle(t("search.title"));
  return <ConfigGate>{(config) => <SearchRideForm config={config} />}</ConfigGate>;
}
