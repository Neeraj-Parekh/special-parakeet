"use client";

// Story bits — small shared pieces for the three-pane story demo.

import * as React from "react";
import { animate } from "framer-motion";
import { CheckCircle2, AlertTriangle, ShieldX, Lock } from "lucide-react";

import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// CountUp — animated number for stat tiles (platform/merchant panes)
// ---------------------------------------------------------------------------

export function CountUp({
  value,
  format,
  className,
}: {
  value: number;
  format?: (n: number) => string;
  className?: string;
}) {
  const [display, setDisplay] = React.useState(value);
  const prev = React.useRef(value);

  React.useEffect(() => {
    const from = prev.current;
    prev.current = value;
    if (from === value) return;
    const controls = animate(from, value, {
      duration: 0.9,
      ease: [0.2, 0.8, 0.2, 1],
      onUpdate: (v) => setDisplay(Math.round(v)),
    });
    return () => controls.stop();
  }, [value]);

  const fmt = format ?? ((n: number) => n.toLocaleString("en-IN"));
  return <span className={cn("tabular-nums", className)}>{fmt(display)}</span>;
}

// ---------------------------------------------------------------------------
// StatusPill — the merchant order status: Safe / Verify / Blocked
// ---------------------------------------------------------------------------

export type FeedStatus = "ok" | "check" | "blocked";

const STATUS_META: Record<FeedStatus, { label: string; cls: string }> = {
  ok: { label: "Safe", cls: "bg-mint-500/15 text-mint-700 dark:text-mint-500 border-mint-500/30" },
  check: { label: "Verify", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30" },
  blocked: { label: "Blocked", cls: "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/30" },
};

export function StatusPill({ status, className }: { status: FeedStatus; className?: string }) {
  const m = STATUS_META[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold",
        m.cls,
        className,
      )}
    >
      {status === "ok" && <CheckCircle2 className="size-3" aria-hidden />}
      {status === "check" && <AlertTriangle className="size-3" aria-hidden />}
      {status === "blocked" && <ShieldX className="size-3" aria-hidden />}
      {m.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ConsentDot — shared/locked indicator (platform pane)
// ---------------------------------------------------------------------------

export function ConsentDot({ shared }: { shared: boolean }) {
  return (
    <span
      className={cn(
        "inline-block size-1.5 rounded-full",
        shared ? "bg-mint-500" : "bg-red-400/70",
      )}
      aria-hidden
    />
  );
}

// ---------------------------------------------------------------------------
// LockedCard — the platform's gated view when consent isn't granted
// ---------------------------------------------------------------------------

export function LockedCard({ merchant, compact }: { merchant: string; compact?: boolean }) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] text-center",
        compact ? "p-4" : "p-8",
      )}
    >
      <Lock className="size-5 text-slate-400" aria-hidden />
      <p className="text-xs font-medium text-slate-300">
        Order detail locked
      </p>
      <p className="max-w-[220px] text-[11px] leading-relaxed text-slate-500">
        {merchant} hasn&apos;t shared their audit key. Aggregates only — no
        customer-level data.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// formatINR compact for stat tiles (₹4.2L style)
// ---------------------------------------------------------------------------

export function formatINRCompact(v: number): string {
  if (v >= 100000) {
    const lakhs = v / 100000;
    return `₹${lakhs >= 10 ? Math.round(lakhs) : Math.round(lakhs * 10) / 10}L`;
  }
  return `₹${v.toLocaleString("en-IN")}`;
}
