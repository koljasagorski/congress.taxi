import { useId, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import { endpoints } from "../api/endpoints";
import type { RequestOut, RideOut } from "../api/types";
import { ConfirmButton, Notice } from "../components/ui";
import { useI18n, type MessageKey } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatNumber } from "../lib/format";
import { useLoad } from "../lib/useLoad";
import { usePageTitle } from "../lib/usePageTitle";

interface MineData {
  rides: RideOut[];
  incoming: RequestOut[];
  outgoing: RequestOut[];
}

async function loadMine(signal: AbortSignal): Promise<MineData> {
  const [rides, incoming, outgoing] = await Promise.all([
    endpoints.myRides(signal),
    endpoints.incoming(undefined, signal),
    endpoints.outgoing(undefined, signal),
  ]);
  return { rides: rides.items, incoming: incoming.items, outgoing: outgoing.items };
}

type Message = { kind: "success" | "error"; text: string } | null;

function Contact({ request }: { request: RequestOut }) {
  const { t } = useI18n();
  // Vertrag: counterpart_email nur bei accepted. Zur Sicherheit auch im Client nur dann zeigen.
  if (request.status === "accepted" && request.counterpart_email) {
    return (
      <p className="contact">
        <strong>{t("mine.contactEmail")}</strong>{" "}
        <a href={`mailto:${request.counterpart_email}`}>{request.counterpart_email}</a>
      </p>
    );
  }
  if (request.status === "pending") {
    return <p className="hint">{t("mine.contactHidden")}</p>;
  }
  return null;
}

function StatusBadge({ status }: { status: RequestOut["status"] | RideOut["status"] }) {
  const { t } = useI18n();
  const key = (
    status === "open" || status === "full"
      ? `status.ride.${status}`
      : status === "cancelled"
        ? "status.request.cancelled"
        : `status.request.${status}`
  ) as MessageKey;
  return <span className={`badge badge-${status}`}>{t(key)}</span>;
}

function RequestRow({
  request,
  mode,
  busy,
  onAccept,
  onDecline,
  onCancel,
}: {
  request: RequestOut;
  mode: "incoming" | "outgoing";
  busy: boolean;
  onAccept: () => void;
  onDecline: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const headingId = useId();
  const title =
    mode === "incoming"
      ? t("mine.requestFrom", { name: request.rider.display_name })
      : t("mine.requestTo", {
          name: request.ride.driver_display_name,
          time: formatDateTime(request.ride.departure_time),
        });
  const canCancel =
    mode === "outgoing"
      ? request.status === "pending" || request.status === "accepted"
      : request.status === "accepted";
  return (
    <li className="request" aria-labelledby={headingId}>
      <p id={headingId} className="request-title">
        <strong>{title}</strong> <StatusBadge status={request.status} />
      </p>
      <p>
        {t("mine.requestDetour", {
          km: formatNumber(request.detour_km),
          minutes: formatNumber(request.detour_minutes, 0),
        })}
      </p>
      <Contact request={request} />
      <div className="actions">
        {mode === "incoming" && request.status === "pending" && (
          <>
            <button type="button" className="btn" disabled={busy} onClick={onAccept} aria-describedby={headingId}>
              {t("mine.accept")}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busy}
              onClick={onDecline}
              aria-describedby={headingId}
            >
              {t("mine.decline")}
            </button>
          </>
        )}
        {canCancel &&
          (request.status === "accepted" ? (
            <ConfirmButton
              label={t("mine.cancel")}
              confirmLabel={t("mine.confirmYes")}
              onConfirm={onCancel}
              describedBy={headingId}
              disabled={busy}
            />
          ) : (
            <button
              type="button"
              className="btn btn-danger-outline"
              disabled={busy}
              onClick={onCancel}
              aria-describedby={headingId}
            >
              {t("mine.cancel")}
            </button>
          ))}
      </div>
    </li>
  );
}

export function MyRidesPage() {
  const { t } = useI18n();
  usePageTitle(t("mine.title"));
  const { data, status, error, reload } = useLoad(loadMine);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  // Antworten der Aktionen sofort anzeigen (E-Mail nach Annahme), bis die Neuladung eintrifft.
  const [overrides, setOverrides] = useState<{ base: MineData | null; map: Record<string, RequestOut> }>({
    base: null,
    map: {},
  });
  const activeOverrides = overrides.base === data ? overrides.map : {};

  const run = async (
    key: string,
    action: () => Promise<RequestOut | void>,
    done: MessageKey,
  ) => {
    setBusy((b) => ({ ...b, [key]: true }));
    setMessage(null);
    try {
      const result = await action();
      if (result) {
        setOverrides((o) => ({
          base: data,
          map: { ...(o.base === data ? o.map : {}), [result.id]: result },
        }));
      }
      setMessage({ kind: "success", text: t(done) });
      reload();
    } catch (err) {
      setMessage({ kind: "error", text: errorMessage(err, t) });
      // Zustand hat sich geändert (409/404): Listen neu laden.
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) reload();
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  };

  const apply = (r: RequestOut): RequestOut => activeOverrides[r.id] ?? r;
  const incoming = (data?.incoming ?? []).map(apply);
  const outgoing = (data?.outgoing ?? []).map(apply);
  const rides = data?.rides ?? [];

  return (
    <section aria-labelledby="mine-title">
      <div className="title-row">
        <h1 id="mine-title">{t("mine.title")}</h1>
        <button type="button" className="btn btn-secondary" onClick={reload}>
          {t("mine.refresh")}
        </button>
      </div>

      <div aria-live="polite">
        {message && <Notice kind={message.kind}>{message.text}</Notice>}
      </div>

      {status === "error" && <Notice kind="error">{errorMessage(error, t)}</Notice>}
      {status === "loading" && !data && (
        <p role="status" className="muted">
          {t("app.loading")}
        </p>
      )}

      {data && (
        <>
          <section aria-labelledby="rides-title">
            <h2 id="rides-title">{t("mine.ridesTitle")}</h2>
            {rides.length === 0 ? (
              <p>
                {t("mine.ridesEmpty")} <Link to="/fahrt-anbieten">{t("mine.ridesEmptyCta")}</Link>
              </p>
            ) : (
              <ul className="card-list">
                {rides.map((ride) => {
                  const rideRequests = incoming.filter((r) => r.ride_id === ride.id);
                  return (
                    <li key={ride.id} className={`card ride ride-${ride.status}`} aria-labelledby={`ride-${ride.id}`}>
                      <h3 id={`ride-${ride.id}`}>
                        {formatDateTime(ride.departure_time)} <StatusBadge status={ride.status} />
                      </h3>
                      <p>
                        {t("mine.rideSummary", {
                          distance: formatNumber(ride.distance_km),
                          duration: formatNumber(ride.duration_min, 0),
                        })}{" "}
                        · {t("mine.seatsOf", { available: ride.seats_available, total: ride.seats_total })}
                      </p>
                      {ride.status !== "cancelled" && (
                        <p className="muted">
                          {t("mine.pendingCount", { count: ride.pending_requests })} ·{" "}
                          {t("mine.acceptedCount", { count: ride.accepted_requests })}
                        </p>
                      )}
                      {ride.status !== "cancelled" && (
                        <div className="actions">
                          <ConfirmButton
                            label={t("mine.cancelRide")}
                            confirmLabel={t("mine.confirmYes")}
                            describedBy={`ride-${ride.id}`}
                            disabled={busy[`ride-${ride.id}`] === true}
                            onConfirm={() =>
                              void run(`ride-${ride.id}`, () => endpoints.cancelRide(ride.id), "mine.done.rideCancelled")
                            }
                          />
                        </div>
                      )}
                      <h4>{t("mine.incomingTitle")}</h4>
                      {rideRequests.length === 0 ? (
                        <p className="muted">{t("mine.incomingEmpty")}</p>
                      ) : (
                        <ul className="request-list">
                          {rideRequests.map((req) => (
                            <RequestRow
                              key={req.id}
                              request={req}
                              mode="incoming"
                              busy={busy[req.id] === true}
                              onAccept={() => void run(req.id, () => endpoints.acceptRequest(req.id), "mine.done.accepted")}
                              onDecline={() => void run(req.id, () => endpoints.declineRequest(req.id), "mine.done.declined")}
                              onCancel={() => void run(req.id, () => endpoints.cancelRequest(req.id), "mine.done.cancelled")}
                            />
                          ))}
                        </ul>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby="outgoing-title">
            <h2 id="outgoing-title">{t("mine.outgoingTitle")}</h2>
            {outgoing.length === 0 ? (
              <p>
                {t("mine.outgoingEmpty")} <Link to="/mitfahrt-suchen">{t("mine.outgoingEmptyCta")}</Link>
              </p>
            ) : (
              <ul className="card-list">
                {outgoing.map((req) => (
                  <li key={req.id} className="card">
                    <ul className="request-list">
                      <RequestRow
                        request={req}
                        mode="outgoing"
                        busy={busy[req.id] === true}
                        onAccept={() => undefined}
                        onDecline={() => undefined}
                        onCancel={() => void run(req.id, () => endpoints.cancelRequest(req.id), "mine.done.cancelled")}
                      />
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </section>
  );
}
