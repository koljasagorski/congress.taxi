import { Link } from "react-router";
import { useI18n } from "../i18n";
import { usePageTitle } from "../lib/usePageTitle";

export function NotFoundPage() {
  const { t } = useI18n();
  usePageTitle(t("notfound.title"));
  return (
    <section aria-labelledby="nf-title">
      <h1 id="nf-title">{t("notfound.title")}</h1>
      <p>{t("notfound.body")}</p>
      <p>
        <Link to="/">{t("notfound.home")}</Link>
      </p>
    </section>
  );
}
