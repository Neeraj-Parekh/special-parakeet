"use client";

import * as React from "react";

interface ApiKeyState {
  scorerKey: string;
  adminKey: string;
  setScorerKey: (v: string) => void;
  setAdminKey: (v: string) => void;
}

const ApiKeyContext = React.createContext<ApiKeyState | null>(null);

const STORAGE_KEY = "rto-trust-api-keys";

interface StoredShape {
  scorer?: string;
  admin?: string;
}

export function ApiKeyProvider({ children }: { children: React.ReactNode }) {
  const [scorerKey, setScorerKeyState] = React.useState("");
  const [adminKey, setAdminKeyState] = React.useState("");

  // Load once from localStorage on mount (client-only).
  React.useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as StoredShape;
        if (parsed.scorer) setScorerKeyState(parsed.scorer);
        if (parsed.admin) setAdminKeyState(parsed.admin);
      }
    } catch {
      // ignore — preview without localStorage
    }
  }, []);

  const persist = React.useCallback((next: StoredShape) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // ignore
    }
  }, []);

  const setScorerKey = React.useCallback(
    (v: string) => {
      setScorerKeyState(v);
      persist({ scorer: v, admin: adminKey });
    },
    [adminKey, persist],
  );
  const setAdminKey = React.useCallback(
    (v: string) => {
      setAdminKeyState(v);
      persist({ scorer: scorerKey, admin: v });
    },
    [scorerKey, persist],
  );

  const value = React.useMemo(
    () => ({ scorerKey, adminKey, setScorerKey, setAdminKey }),
    [scorerKey, adminKey, setScorerKey, setAdminKey],
  );

  return <ApiKeyContext.Provider value={value}>{children}</ApiKeyContext.Provider>;
}

export function useApiKeys(): ApiKeyState {
  const ctx = React.useContext(ApiKeyContext);
  if (!ctx) {
    throw new Error("useApiKeys must be used within <ApiKeyProvider>");
  }
  return ctx;
}

/** Helper: build the Authorization header from the requested scope. */
export function buildAuthHeader(
  keys: ApiKeyState,
  scope: "scorer" | "admin",
): Record<string, string> {
  const k = scope === "scorer" ? keys.scorerKey : keys.adminKey;
  if (!k) return {};
  return { Authorization: `Bearer ${k}` };
}
