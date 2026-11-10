import { createContext, useContext, useMemo, type ReactNode } from "react";
import { endpoints } from "../api/endpoints";
import type { PublicConfig } from "../api/types";
import { useLoad } from "../lib/useLoad";

interface ConfigValue {
  config: PublicConfig | null;
  status: "loading" | "ready" | "error";
  error: unknown;
  reload: () => void;
}

const ConfigContext = createContext<ConfigValue | null>(null);

const loadConfig = (signal: AbortSignal) => endpoints.config(signal);

/** Lädt GET /api/config einmalig (Tile-URL, Eventziel, Limits kommen zur Laufzeit vom Server). */
export function ConfigProvider({ children }: { children: ReactNode }) {
  const { data, status, error, reload } = useLoad(loadConfig);
  const value = useMemo<ConfigValue>(
    () => ({ config: data, status, error, reload }),
    [data, status, error, reload],
  );
  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useConfig(): ConfigValue {
  const value = useContext(ConfigContext);
  if (!value) throw new Error("ConfigProvider fehlt");
  return value;
}
