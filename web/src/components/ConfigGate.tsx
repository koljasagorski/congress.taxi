import type { ReactNode } from "react";
import type { PublicConfig } from "../api/types";
import { useConfig } from "../context/ConfigContext";
import { useI18n } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Notice } from "./ui";

/** Zeigt Kinder erst, wenn GET /api/config geladen ist (Tile-URL und Eventziel kommen von dort). */
export function ConfigGate({ children }: { children: (config: PublicConfig) => ReactNode }) {
  const { config, status, error, reload } = useConfig();
  const { t } = useI18n();
  if (config) return <>{children(config)}</>;
  if (status === "error") {
    return (
      <Notice kind="error">
        {errorMessage(error, t)}{" "}
        <button type="button" className="link-button" onClick={reload}>
          {t("common.retry")}
        </button>
      </Notice>
    );
  }
  return (
    <p role="status" className="muted">
      {t("app.loading")}
    </p>
  );
}
