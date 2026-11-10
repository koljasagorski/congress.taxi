import { useCallback, useEffect, useState } from "react";

export type LoadState<T> =
  | { status: "loading"; data: T | null; error: null }
  | { status: "ready"; data: T; error: null }
  | { status: "error"; data: T | null; error: unknown };

/**
 * Lädt Daten beim Einhängen und bei reload(). `load` muss eine stabile Referenz sein
 * (Modulfunktion oder useCallback). Vorherige Daten bleiben beim Nachladen sichtbar.
 */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<LoadState<T>>({ status: "loading", data: null, error: null });
  const [round, setRound] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: "ready", data, error: null });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState((prev) => ({ status: "error", data: prev.data, error }));
      },
    );
    return () => controller.abort();
  }, [load, round]);

  const reload = useCallback(() => {
    setState((prev) => ({ status: "loading", data: prev.data, error: null }));
    setRound((r) => r + 1);
  }, []);

  return { ...state, reload };
}
