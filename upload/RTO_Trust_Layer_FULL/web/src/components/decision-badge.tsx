"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type { Decision } from "@/lib/mock-data";

interface DecisionBadgeProps {
  decision: Decision | string | null | undefined;
  className?: string;
  size?: "sm" | "md" | "lg";
}

const SIZE_CLASS: Record<NonNullable<DecisionBadgeProps["size"]>, string> = {
  sm: "px-2 py-0.5 text-xs",
  md: "px-3 py-1 text-sm",
  lg: "px-4 py-1.5 text-base",
};

/** Decision pill — color-coded per the master plan: ACCEPT=emerald,
 * REVIEW=amber, REJECT=red. Used by Risk Console + Audit Explorer +
 * recent decisions table. */
export function DecisionBadge({
  decision,
  className,
  size = "md",
}: DecisionBadgeProps) {
  if (!decision) {
    return (
      <span
        className={cn(
          "inline-flex items-center justify-center rounded-full border border-border bg-muted/40 text-muted-foreground",
          SIZE_CLASS[size],
          className,
        )}
      >
        —
      </span>
    );
  }
  const d = String(decision).toUpperCase();
  let cls = "border-border bg-muted/40 text-muted-foreground";
  if (d === "ACCEPT") cls = "border-success/50 bg-success/15 text-success";
  else if (d === "REVIEW") cls = "border-warning/50 bg-warning/15 text-warning";
  else if (d === "REJECT") cls = "border-danger/50 bg-danger/15 text-danger";
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-full border font-semibold tracking-wide",
        SIZE_CLASS[size],
        cls,
        className,
      )}
    >
      {d}
    </span>
  );
}

interface ScorePillProps {
  probability: number | null | undefined;
  className?: string;
}

/** "P(RTO) = 0.73" large display. */
export function ScorePill({ probability, className }: ScorePillProps) {
  const v =
    probability === null || probability === undefined
      ? null
      : (probability * 100).toFixed(0);
  return (
    <div className={cn("flex items-baseline gap-2", className)}>
      <span className="font-mono text-3xl font-bold tabular-nums">
        {v === null ? "—" : v}
      </span>
      <span className="text-sm text-muted-foreground">% RTO risk</span>
    </div>
  );
}

interface DecisionSourcePillProps {
  source: string | null | undefined;
}

const SOURCE_LABELS: Record<string, string> = {
  rules_engine_block: "Rules-engine BLOCK",
  mandate_breach: "Mandate breach",
  mandate_invalid: "Mandate invalid",
  mandate_review_required: "Mandate REVIEW (cooling)",
  degraded_review: "Degraded REVIEW",
  cost_optimal_bmr: "Cost-optimal BMR",
  cost_optimal_bmr_review_rule: "Cost-optimal BMR + REVIEW rule",
};

/** Small badge showing which Track-C decision layer actually chose. */
export function DecisionSourcePill({ source }: DecisionSourcePillProps) {
  if (!source) return null;
  const label = SOURCE_LABELS[source] ?? source;
  return (
    <span
      title={`Decision source: ${source}`}
      className="inline-flex items-center gap-1 rounded-md border border-border/70 bg-muted/30 px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
    >
      <span className="inline-block size-1.5 rounded-full bg-foreground/40" aria-hidden />
      {label}
    </span>
  );
}
