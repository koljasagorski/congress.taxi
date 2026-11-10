import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router";
import { Notice } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../i18n";
import { errorMessage } from "../lib/errors";
import { usePageTitle } from "../lib/usePageTitle";

export function DeleteAccountPage() {
  const { t } = useI18n();
  usePageTitle(t("delete.title"));
  const { status, deleteAccount } = useAuth();
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [deleted, setDeleted] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    if (!confirmed) {
      setError(t("delete.confirmRequired"));
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await deleteAccount();
      setDeleted(true);
    } catch (err) {
      setError(errorMessage(err, t));
    } finally {
      setSubmitting(false);
    }
  };

  // Nach erfolgreichem Löschen ist der Nutzer abgemeldet: Bestätigung statt Login-Weiterleitung.
  if (deleted) {
    return (
      <section aria-labelledby="delete-done">
        <h1 id="delete-done">{t("delete.doneTitle")}</h1>
        <Notice kind="success">{t("delete.done")}</Notice>
        <p>
          <Link to="/">{t("notfound.home")}</Link>
        </p>
      </section>
    );
  }
  if (status === "loading") return <p role="status">{t("app.loading")}</p>;
  if (status === "anon") return <Navigate to="/anmelden?next=%2Fkonto-loeschen" replace />;

  return (
    <section aria-labelledby="delete-title">
      <h1 id="delete-title">{t("delete.title")}</h1>
      <p>{t("delete.lead")}</p>
      <form onSubmit={(e) => void onSubmit(e)} noValidate className="form">
        <div className="field field-check">
          <input
            id="delete-confirm"
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
            aria-describedby={error ? "delete-error" : undefined}
          />
          <label htmlFor="delete-confirm">{t("delete.confirm")}</label>
        </div>
        {error && (
          <Notice kind="error" id="delete-error">
            {error}
          </Notice>
        )}
        <div className="actions">
          <button type="submit" className="btn btn-danger" disabled={submitting}>
            {submitting ? t("delete.submitting") : t("delete.submit")}
          </button>
        </div>
      </form>
    </section>
  );
}
