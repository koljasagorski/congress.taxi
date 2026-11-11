import { useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { endpoints } from "../api/endpoints";
import type { LatLon, PublicConfig, RideOut } from "../api/types";
import { ConfigGate } from "../components/ConfigGate";
import { MapView } from "../components/MapView";
import { PointPicker, type PointPickerHandle } from "../components/PointPicker";
import { Notice, TextField, useFocusFirstInvalid } from "../components/ui";
import { useI18n } from "../i18n";
import { errorMessage, invalidFields, isApiError } from "../lib/errors";
import { formatDateTime, formatNumber, localInputToIso, nowForInput } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

type Errors = Partial<Record<"start" | "departure" | "seats", string>>;

function OfferRideForm({ config }: { config: PublicConfig }) {
  const { t } = useI18n();
  const formRef = useRef<HTMLFormElement>(null);
  const pickerRef = useRef<PointPickerHandle>(null);
  const { min_seats, max_seats } = config.limits;
  const eventPosition: LatLon = { lat: config.event.lat, lon: config.event.lon };

  const [start, setStart] = useState<LatLon | null>(null);
  const [departure, setDeparture] = useState("");
  const [seats, setSeats] = useState("3");
  const [errors, setErrors] = useState<Errors>({});
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [pointError, setPointError] = useState<string | null>(null);
  const [created, setCreated] = useState<RideOut | null>(null);
  useFocusFirstInvalid(formRef, attempt);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setServerError(null);
    setPointError(null);

    // Getippte, noch nicht übernommene Koordinaten zählen: nie still den alten Punkt senden.
    const picked = pickerRef.current?.commit();
    const coordsInvalid = picked?.ok === false;
    const point = picked?.ok ? picked.point : start;

    const found: Errors = {};
    if (!coordsInvalid && !point) found.start = t("valid.coordRequired");
    const iso = localInputToIso(departure);
    if (!departure || !iso) found.departure = t("valid.departureRequired");
    else if (new Date(iso).getTime() <= Date.now()) found.departure = t("valid.departureFuture");
    const seatCount = Number(seats);
    if (!Number.isInteger(seatCount) || seatCount < min_seats || seatCount > max_seats) {
      found.seats = t("valid.seatsRange", { min: min_seats, max: max_seats });
    }
    setErrors(found);
    if (coordsInvalid || Object.keys(found).length > 0 || !point || !iso) {
      setAttempt((a) => a + 1);
      return;
    }

    setSubmitting(true);
    try {
      const ride = await endpoints.createRide({ start: point, departure_time: iso, seats: seatCount });
      setCreated(ride);
    } catch (err) {
      const message = errorMessage(err, t);
      if (isApiError(err, "outside_coverage") || isApiError(err, "no_route")) {
        setPointError(message);
        setAttempt((a) => a + 1);
      } else {
        const fields = invalidFields(err);
        const mapped: Errors = {};
        if (fields.includes("departure_time")) mapped.departure = t("error.fieldInvalid");
        if (fields.includes("seats")) mapped.seats = t("error.fieldInvalid");
        if (fields.includes("lat") || fields.includes("lon") || fields.includes("start")) {
          mapped.start = t("error.fieldInvalid");
        }
        setErrors(mapped);
        if (Object.keys(mapped).length > 0) setAttempt((a) => a + 1);
        setServerError(message);
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (created) {
    return (
      <section aria-labelledby="offer-done">
        <h1 id="offer-done">{t("offer.successTitle")}</h1>
        <Notice kind="success">
          {t("offer.summary", {
            distance: formatNumber(created.distance_km),
            duration: formatNumber(created.duration_min, 0),
            departure: formatDateTime(created.departure_time),
            seats: t("common.seats", { count: created.seats_total }),
          })}
        </Notice>
        <dl className="summary">
          <div>
            <dt>{t("offer.summaryDistance")}</dt>
            <dd>{t("common.km", { value: formatNumber(created.distance_km) })}</dd>
          </div>
          <div>
            <dt>{t("offer.summaryDuration")}</dt>
            <dd>{t("common.minutes", { value: formatNumber(created.duration_min, 0) })}</dd>
          </div>
          <div>
            <dt>{t("offer.summaryDeparture")}</dt>
            <dd>{formatDateTime(created.departure_time)}</dd>
          </div>
          <div>
            <dt>{t("offer.summarySeats")}</dt>
            <dd>{created.seats_available}</dd>
          </div>
        </dl>
        <MapView
          tileUrl={config.map.tile_url}
          attribution={config.map.attribution}
          center={created.start}
          markers={[
            { id: "start", position: created.start, label: t("map.markerStart"), kind: "start" },
            {
              id: "event",
              position: eventPosition,
              label: t("map.markerEvent", { name: config.event.name }),
              kind: "event",
            },
          ]}
          routes={[{ id: created.id, points: created.route, label: t("map.markerStart") }]}
          fitSignature={created.id}
          ariaLabel={t("map.regionLabel")}
        />
        <div className="actions">
          <Link className="btn" to="/meine-fahrten">
            {t("offer.toMine")}
          </Link>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => {
              setCreated(null);
              setStart(null);
              setDeparture("");
            }}
          >
            {t("offer.another")}
          </button>
        </div>
      </section>
    );
  }

  return (
    <section aria-labelledby="offer-title">
      <h1 id="offer-title">{t("offer.title")}</h1>
      <p>{t("offer.lead", { event: config.event.name })}</p>
      <form ref={formRef} onSubmit={(e) => void onSubmit(e)} noValidate className="form">
        <PointPicker
          ref={pickerRef}
          legend={t("offer.startLabel")}
          value={start}
          onChange={(p) => {
            setStart(p);
            setPointError(null);
            setErrors((prev) => ({ ...prev, start: undefined }));
          }}
          tileUrl={config.map.tile_url}
          attribution={config.map.attribution}
          eventPosition={eventPosition}
          eventName={config.event.name}
          geocoderId={config.map.geocoder}
          pointKind="start"
          pointLabel={t("map.markerStart")}
          error={pointError ?? errors.start}
        />
        <p className="hint">{t("offer.startPrivacy")}</p>
        <TextField
          label={t("field.departure")}
          type="datetime-local"
          name="departure_time"
          min={nowForInput()}
          value={departure}
          onChange={(e) => setDeparture(e.target.value)}
          error={errors.departure}
          required
        />
        <TextField
          label={t("field.seats")}
          type="number"
          name="seats"
          inputMode="numeric"
          min={min_seats}
          max={max_seats}
          step={1}
          value={seats}
          onChange={(e) => setSeats(e.target.value)}
          error={errors.seats}
          required
        />
        {serverError && !pointError && <Notice kind="error">{serverError}</Notice>}
        <div className="actions">
          <button type="submit" className="btn" disabled={submitting}>
            {submitting ? t("offer.submitting") : t("offer.submit")}
          </button>
        </div>
        <p role="status" className="sr-only">
          {submitting ? t("offer.submitting") : ""}
        </p>
      </form>
    </section>
  );
}

export function OfferRidePage() {
  const { t } = useI18n();
  usePageTitle(t("offer.title"));
  return <ConfigGate>{(config) => <OfferRideForm config={config} />}</ConfigGate>;
}
