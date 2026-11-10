import { Link } from "react-router";
import { Notice } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useConfig } from "../context/ConfigContext";
import { useI18n } from "../i18n";
import { formatDate } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

export function HomePage() {
  const { t } = useI18n();
  const { status } = useAuth();
  const { config, status: configStatus, reload } = useConfig();
  const event = config?.event.name ?? "40C3";
  usePageTitle(t("nav.home"));

  return (
    <>
      <section aria-labelledby="home-title">
        <h1 id="home-title">{t("home.title", { event })}</h1>
        <p className="lead">{t("home.lead", { event })}</p>
        {config && (
          <p className="muted">
            {t("home.eventDates", {
              event: config.event.name,
              start: formatDate(config.event.start_date),
              end: formatDate(config.event.end_date),
            })}
          </p>
        )}
        {configStatus === "error" && (
          <Notice kind="error">
            {t("home.configError")}{" "}
            <button type="button" className="link-button" onClick={reload}>
              {t("common.retry")}
            </button>
          </Notice>
        )}
        {status === "anon" && (
          <div className="actions">
            <Link className="btn" to="/registrieren">
              {t("home.ctaRegister")}
            </Link>
            <Link className="btn btn-secondary" to="/anmelden">
              {t("home.ctaLogin")}
            </Link>
          </div>
        )}
        {status === "authed" && (
          <div className="actions">
            <Link className="btn" to="/fahrt-anbieten">
              {t("nav.offer")}
            </Link>
            <Link className="btn btn-secondary" to="/mitfahrt-suchen">
              {t("nav.search")}
            </Link>
          </div>
        )}
      </section>

      <section aria-labelledby="how-title">
        <h2 id="how-title">{t("home.howTitle")}</h2>
        <ol>
          <li>{t("home.step1")}</li>
          <li>{t("home.step2")}</li>
          <li>{t("home.step3")}</li>
          <li>{t("home.step4")}</li>
        </ol>
      </section>

      <section aria-labelledby="privacy-short" className="card">
        <h2 id="privacy-short">{t("home.privacyTitle")}</h2>
        <ul>
          <li>{t("home.privacy1")}</li>
          <li>{t("home.privacy2")}</li>
          <li>{t("home.privacy3")}</li>
          <li>{t("home.privacy4")}</li>
        </ul>
        <p>
          <Link to="/datenschutz">{t("home.privacyMore")}</Link>
        </p>
      </section>
    </>
  );
}
