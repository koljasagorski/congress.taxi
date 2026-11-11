import { Link } from "react-router";
import { useConfig } from "../context/ConfigContext";
import { useI18n, type MessageKey } from "../i18n";
import { formatDate, hostOf } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

function Draft({ children }: { children: string }) {
  const { t } = useI18n();
  return (
    <p className="draft-banner" role="note">
      <strong>{t("common.draftBadge")}:</strong> {children}
    </p>
  );
}

const PRIVACY_SECTIONS = ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s7geo", "s8", "s9"] as const;

export function PrivacyPage() {
  const { t } = useI18n();
  const { config } = useConfig();
  usePageTitle(t("privacy.title"));
  const params = {
    days: config?.retention_days_after_event ?? "[Tage]",
    end: config ? formatDate(config.event.end_date) : "[Ende der Veranstaltung]",
    host: config ? hostOf(config.map.tile_url) || "[Kartenserver]" : "[Kartenserver]",
  };
  const viaProxy = config !== undefined && config !== null && hostOf(config.map.tile_url) === "";
  return (
    <article aria-labelledby="privacy-title">
      <h1 id="privacy-title">{t("privacy.title")}</h1>
      <Draft>{t("privacy.draftNotice")}</Draft>
      {PRIVACY_SECTIONS.filter((s) => s !== "s7geo" || config?.map.geocoder === "nominatim").map((s) => (
        <section key={s} aria-labelledby={`privacy-${s}`}>
          <h2 id={`privacy-${s}`}>{t(`privacy.${s}.title` as MessageKey)}</h2>
          <p>
            {t(
              (s === "s7" && viaProxy ? "privacy.s7proxy.body" : `privacy.${s}.body`) as MessageKey,
              params,
            )}
          </p>
        </section>
      ))}
      <p>
        <Link to="/impressum">{t("nav.imprint")}</Link>
      </p>
    </article>
  );
}

export function ImprintPage() {
  const { t } = useI18n();
  usePageTitle(t("imprint.title"));
  return (
    <article aria-labelledby="imprint-title">
      <h1 id="imprint-title">{t("imprint.title")}</h1>
      <Draft>{t("imprint.draftNotice")}</Draft>
      <h2>{t("imprint.provider")}</h2>
      <p className="pre-line">{t("imprint.providerBody")}</p>
      <h2>{t("imprint.contact")}</h2>
      <p className="pre-line">{t("imprint.contactBody")}</p>
      <h2>{t("imprint.responsible")}</h2>
      <p>{t("imprint.responsibleBody")}</p>
      <p>
        <Link to="/datenschutz">{t("nav.privacy")}</Link>
      </p>
    </article>
  );
}
