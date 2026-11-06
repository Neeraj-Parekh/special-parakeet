// Session-scoped recent-decision store — shared by the Risk Scoring page,
// the Checkout demo, and the Dashboard metrics row.
//
// Persists to sessionStorage (survives tab reload, wiped when the tab
// closes) so a judge demo mid-flight never loses its history.

import * as React from "react";
import type { Decision } from "@/lib/mock-data";

export interface RecentDecision {
  prediction_id: string;
  order_id: string;
  amount_inr: number;
  payment_method: string;
  decision: Decision | string;
  probability: number | null;
  decision_source: string;
  latency_ms: number | null;
  mock: boolean;
  ts: number;
}

const RECENT_KEY = "rto-recent-decisions";
let recentListeners: Array<() => void> = [];
let recentCache: RecentDecision[] = [];

export function loadRecent(): RecentDecision[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(RECENT_KEY);
    if (raw) return JSON.parse(raw) as RecentDecision[];
  } catch {
    /* ignore */
  }
  return [];
}

export function pushRecent(d: RecentDecision): void {
  const next = [d, ...loadRecent()].slice(0, 50);
  try {
    window.sessionStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
  recentCache = next;
  recentListeners.forEach((fn) => fn());
}

export function clearRecent(): void {
  try {
    window.sessionStorage.removeItem(RECENT_KEY);
  } catch {
    /* ignore */
  }
  recentCache = [];
  recentListeners.forEach((fn) => fn());
}

/** React binding — re-renders on every push/clear. */
export function useRecentDecisions(): [RecentDecision[], (d: RecentDecision) => void, () => void] {
  const [, force] = React.useReducer((x) => x + 1, 0);
  React.useEffect(() => {
    recentCache = loadRecent();
    const fn = () => force();
    recentListeners.push(fn);
    return () => {
      recentListeners = recentListeners.filter((f) => f !== fn);
    };
  }, []);
  return [recentCache, pushRecent, clearRecent];
}
