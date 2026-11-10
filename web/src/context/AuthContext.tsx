import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { setUnauthorizedHandler } from "../api/client";
import { endpoints } from "../api/endpoints";
import type { Me } from "../api/types";

interface AuthValue {
  status: "loading" | "authed" | "anon";
  user: Me | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  deleteAccount: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

interface AuthState {
  status: AuthValue["status"];
  user: Me | null;
}

/** Auth-Zustand über GET /api/me (Cookie HttpOnly, daher kein Token im JavaScript). */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading", user: null });

  useEffect(() => {
    const controller = new AbortController();
    setUnauthorizedHandler(() => setState({ status: "anon", user: null }));
    endpoints.me(controller.signal).then(
      (user) => {
        if (!controller.signal.aborted) setState({ status: "authed", user });
      },
      () => {
        if (!controller.signal.aborted) setState({ status: "anon", user: null });
      },
    );
    return () => {
      controller.abort();
      setUnauthorizedHandler(null);
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    await endpoints.login(email, password);
    const user = await endpoints.me();
    setState({ status: "authed", user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await endpoints.logout();
    } finally {
      setState({ status: "anon", user: null });
    }
  }, []);

  const deleteAccount = useCallback(async () => {
    await endpoints.deleteMe();
    setState({ status: "anon", user: null });
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ ...state, login, logout, deleteAccount }),
    [state, login, logout, deleteAccount],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("AuthProvider fehlt");
  return value;
}
