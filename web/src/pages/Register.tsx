import { useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { endpoints } from "../api/endpoints";
import { Notice, TextField, useFocusFirstInvalid } from "../components/ui";
import { useI18n } from "../i18n";
import { errorMessage, invalidFields } from "../lib/errors";
import { usePageTitle } from "../lib/usePageTitle";

type Errors = Partial<Record<"email" | "password" | "display_name" | "consent", string>>;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function ConsentLabel({ text }: { text: string }) {
  const match = /^(.*)<privacy>(.*)<\/privacy>(.*)$/.exec(text);
  if (!match) return <>{text}</>;
  return (
    <>
      {match[1]}
      <Link to="/datenschutz" target="_blank">
        {match[2]}
      </Link>
      {match[3]}
    </>
  );
}

export function RegisterPage() {
  const { t } = useI18n();
  usePageTitle(t("register.title"));
  const formRef = useRef<HTMLFormElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [consent, setConsent] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  useFocusFirstInvalid(formRef, attempt);

  const validate = (): Errors => {
    const next: Errors = {};
    const mail = email.trim();
    if (!mail) next.email = t("valid.emailRequired");
    else if (!EMAIL_RE.test(mail) || mail.length > 320) next.email = t("valid.emailInvalid");
    if (password.length < 10 || password.length > 128) next.password = t("valid.passwordLength");
    const name = displayName.trim();
    if (!name) next.display_name = t("valid.displayNameRequired");
    else if (name.length > 80) next.display_name = t("valid.displayNameLength");
    if (!consent) next.consent = t("valid.consentRequired");
    return next;
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setServerError(null);
    const found = validate();
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setAttempt((a) => a + 1);
      return;
    }
    setSubmitting(true);
    try {
      await endpoints.register({
        email: email.trim(),
        password,
        display_name: displayName.trim(),
        consent: true,
      });
      // 202 ohne Auto-Login: Antwort ist bewusst immer gleich (kein Nutzer-Orakel).
      setPassword("");
      setDone(true);
    } catch (err) {
      const fields = invalidFields(err);
      if (fields.length > 0) {
        const mapped: Errors = {};
        for (const f of fields) {
          if (f === "email" || f === "password" || f === "display_name" || f === "consent") {
            mapped[f] = t("error.fieldInvalid");
          }
        }
        setErrors(mapped);
        setAttempt((a) => a + 1);
      }
      setServerError(errorMessage(err, t));
    } finally {
      setSubmitting(false);
    }
  };

  if (done) {
    return (
      <section aria-labelledby="register-done">
        <h1 id="register-done">{t("register.successTitle")}</h1>
        <Notice kind="success">{t("register.success")}</Notice>
        <p>
          <Link className="btn" to="/anmelden">
            {t("register.toLogin")}
          </Link>
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="register-title">
      <h1 id="register-title">{t("register.title")}</h1>
      <p className="hint">{t("register.emailUnverified")}</p>
      <form ref={formRef} onSubmit={(e) => void onSubmit(e)} noValidate className="form">
        <TextField
          label={t("field.email")}
          type="email"
          name="email"
          autoComplete="email"
          maxLength={320}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
          required
        />
        <TextField
          label={t("field.password")}
          type="password"
          name="password"
          autoComplete="new-password"
          hint={t("register.passwordHint")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={errors.password}
          required
        />
        <TextField
          label={t("field.displayName")}
          type="text"
          name="display_name"
          autoComplete="nickname"
          maxLength={80}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          error={errors.display_name}
          required
        />
        <div className="field field-check">
          <input
            id="consent"
            type="checkbox"
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            aria-invalid={errors.consent ? true : undefined}
            aria-describedby={errors.consent ? "consent-error" : undefined}
            required
          />
          <label htmlFor="consent">
            <ConsentLabel text={t("register.consent")} />
          </label>
          {errors.consent && (
            <p id="consent-error" className="field-error">
              {errors.consent}
            </p>
          )}
        </div>
        {serverError && <Notice kind="error">{serverError}</Notice>}
        <div className="actions">
          <button type="submit" className="btn" disabled={submitting}>
            {submitting ? t("register.submitting") : t("register.submit")}
          </button>
        </div>
      </form>
      <p>
        {t("register.hasAccount")} <Link to="/anmelden">{t("nav.login")}</Link>
      </p>
    </section>
  );
}
