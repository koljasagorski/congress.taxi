import {
  useEffect,
  useId,
  useState,
  type InputHTMLAttributes,
  type ReactNode,
  type RefObject,
} from "react";
import { useI18n } from "../i18n";

/** Statusmeldung. Fehler: role="alert" (sofort vorgelesen), sonst role="status" (aria-live polite). */
export function Notice({
  kind,
  children,
  id,
}: {
  kind: "error" | "success" | "info";
  children: ReactNode;
  id?: string;
}) {
  return (
    <div id={id} className={`notice notice-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  error?: string | undefined;
  hint?: string;
}

/** Eingabefeld mit sichtbarem Label, Hinweis und Fehlertext (aria-invalid, aria-describedby). */
export function TextField({ label, error, hint, ...inputProps }: TextFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {hint && (
        <p id={hintId} className="hint">
          {hint}
        </p>
      )}
      <input
        id={id}
        {...inputProps}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      {error && (
        <p id={errorId} className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

/** Nach einem fehlgeschlagenen Absenden den Fokus auf das erste fehlerhafte Feld setzen. */
export function useFocusFirstInvalid(formRef: RefObject<HTMLElement | null>, attempt: number) {
  useEffect(() => {
    if (attempt === 0) return;
    formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [attempt, formRef]);
}

/** Zweistufige Bestätigung für destruktive Aktionen (ohne window.confirm, daher auch testbar und barrierearm). */
export function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
  describedBy,
  disabled,
  ariaLabel,
}: {
  label: string;
  confirmLabel: string;
  onConfirm: () => void;
  describedBy?: string;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const { t } = useI18n();
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button
        type="button"
        className="btn btn-danger-outline"
        onClick={() => setAsking(true)}
        disabled={disabled}
        aria-describedby={describedBy}
        aria-label={ariaLabel}
      >
        {label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={t("mine.confirmCancel")}>
      <span>{t("mine.confirmCancel")}</span>{" "}
      <button
        type="button"
        className="btn btn-danger"
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>{" "}
      <button type="button" className="btn btn-secondary" onClick={() => setAsking(false)}>
        {t("mine.confirmNo")}
      </button>
    </span>
  );
}
