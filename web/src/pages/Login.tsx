import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { Notice, TextField, useFocusFirstInvalid } from "../components/ui";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../i18n";
import { errorMessage } from "../lib/errors";
import { safeNext } from "../lib/format";
import { usePageTitle } from "../lib/usePageTitle";

export function LoginPage() {
  const { t } = useI18n();
  usePageTitle(t("login.title"));
  const { login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next");
  const formRef = useRef<HTMLFormElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  useFocusFirstInvalid(formRef, attempt);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setServerError(null);
    const found: { email?: string; password?: string } = {};
    if (!email.trim()) found.email = t("valid.emailRequired");
    if (!password) found.password = t("valid.passwordRequired");
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setAttempt((a) => a + 1);
      return;
    }
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      void navigate(safeNext(next), { replace: true });
    } catch (err) {
      setPassword("");
      setServerError(errorMessage(err, t));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section aria-labelledby="login-title">
      <h1 id="login-title">{t("login.title")}</h1>
      {next && <p className="hint">{t("login.needed")}</p>}
      <form ref={formRef} onSubmit={(e) => void onSubmit(e)} noValidate className="form">
        <TextField
          label={t("field.email")}
          type="email"
          name="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
          required
        />
        <TextField
          label={t("field.password")}
          type="password"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={errors.password}
          required
        />
        {serverError && <Notice kind="error">{serverError}</Notice>}
        <div className="actions">
          <button type="submit" className="btn" disabled={submitting}>
            {submitting ? t("login.submitting") : t("login.submit")}
          </button>
        </div>
      </form>
      <p>
        {t("login.noAccount")} <Link to="/registrieren">{t("nav.register")}</Link>
      </p>
    </section>
  );
}
