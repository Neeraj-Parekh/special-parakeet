"use client";

// ShapWaterfall — the visual explainability centerpiece the advice demands.
//
// "Your API returns SHAP values. The judge must see them visually."
//
// The Python backend returns `reason_codes` (top-5 by |delta_prob|) on every
// /risk/score response — see src/api/routes.py::reason_codes_batch and the
// mock mirror in src/lib/mock-data.ts::buildReasonCodes. Each ReasonCode is
//   { feature, value, delta_prob, direction: "up"|"down" }
// where `direction: "up"` means the feature pushes the RTO probability UP
// (towards REJECT) and "down" pushes it towards ACCEPT.
//
// This component renders a horizontal **diverging contribution chart** — the
// per-sample analogue of shap.plots.bar / shap.plots.waterfall:
//
//   • a centre zero-axis
//   • each reason = a horizontal bar; red (up) extends RIGHT, green (down)
//     extends LEFT, width ∝ |delta_prob|
//   • a one-line cumulative summary "base → ±deltas → final P(RTO)"
//   • a threshold ladder (ACCEPT_t / REJECT_t) drawn as vertical rules so
//     the judge sees WHY the decision landed where it did
//
// No Recharts waterfall fiddliness — pure divs + flexbox + Tailwind. This
// is more reliable than a stacked-bar-with-invisible-spacer and renders
// identically in light/dark.

import * as React from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ReasonCode } from "@/lib/mock-data";

interface ShapWaterfallProps {
  /** Top-N reason codes from /risk/score `explanation`. */
  reasons: ReasonCode[];
  /** Final model P(RTO) in [0,1]. */
  probability: number | null;
  /** Decision thresholds (legacy_accept_t / legacy_reject_t). Optional. */
  acceptT?: number;
  rejectT?: number;
  /** When the probability is mocked (no Python backend), show a caveat. */
  mock?: boolean;
}

/**
 * Back-calculate the base (prior) probability: base = final - ΣΔ.
 * If reasons is empty, fall back to a sensible global prior (0.20 = the
 * Amazon-test RTO base rate, honest per the report).
 */
function computeBase(reasons: ReasonCode[], probability: number | null): number {
  if (probability === null) return 0.2;
  const sum = reasons.reduce((acc, r) => acc + r.delta_prob, 0);
  const base = probability - sum;
  // Clamp to [0.02, 0.98] so the chart never shows negative or >1 base.
  return Math.max(0.02, Math.min(0.98, base));
}

/** Sort by |delta_prob| descending so the biggest drivers are on top. */
function sortByMagnitude(reasons: ReasonCode[]): ReasonCode[] {
  return [...reasons].sort((a, b) => Math.abs(b.delta_prob) - Math.abs(a.delta_prob));
}

/** Human-readable feature label (strip the raw column-name noise). */
function prettifyFeature(f: string): string {
  return f
    .replace(/^cat__/, "")
    .replace(/^num__/, "")
    .replace(/_/g, " ");
}

/** A bar row: feature name (left), the diverging bar (centre), delta (right). */
function ReasonBar({ reason, maxAbs }: { reason: ReasonCode; maxAbs: number }) {
  const isUp = reason.direction === "up" || reason.delta_prob >= 0;
  const widthPct = maxAbs > 0 ? Math.min(100, (Math.abs(reason.delta_prob) / maxAbs) * 100) : 0;
  const Icon = isUp ? TrendingUp : TrendingDown;
  const sign = reason.delta_prob >= 0 ? "+" : "−";
  return (
    <div className="grid grid-cols-[140px_1fr_64px] items-center gap-2 text-xs">
      <div className="flex items-center gap-1.5 truncate">
        <Icon
          className={`size-3 shrink-0 ${isUp ? "text-danger" : "text-success"}`}
          aria-hidden
        />
        <span className="truncate font-mono text-muted-foreground" title={reason.feature}>
          {prettifyFeature(reason.feature)}
        </span>
      </div>
      {/* Diverging track — centre line at 50% */}
      <div className="relative h-5 rounded-sm bg-muted/40">
        {/* centre axis */}
        <div className="absolute left-1/2 top-0 h-full w-px bg-border" aria-hidden />
        {/* the bar — anchored to centre, grows left (down) or right (up) */}
        <div
          className={`absolute top-0 h-full ${
            isUp
              ? "left-1/2 bg-danger/70"
              : "right-1/2 bg-success/70"
          } rounded-sm`}
          style={{ width: `${widthPct / 2}%` }}
          aria-label={`${reason.feature} ${isUp ? "increases" : "decreases"} RTO risk by ${Math.abs(reason.delta_prob).toFixed(3)}`}
        />
      </div>
      <div
        className={`text-right font-mono ${
          isUp ? "text-danger" : "text-success"
        }`}
      >
        {sign}
        {Math.abs(reason.delta_prob).toFixed(3)}
      </div>
    </div>
  );
}

export function ShapWaterfall({
  reasons,
  probability,
  acceptT = 0.35,
  rejectT = 0.65,
  mock = false,
}: ShapWaterfallProps) {
  const sorted = sortByMagnitude(reasons).slice(0, 8);
  const maxAbs = sorted.length
    ? Math.max(...sorted.map((r) => Math.abs(r.delta_prob)))
    : 0;
  const base = computeBase(reasons, probability);
  const final = probability ?? null;
  const totalDelta = reasons.reduce((acc, r) => acc + r.delta_prob, 0);
  const ups = reasons.filter((r) => r.direction === "up" || r.delta_prob >= 0);
  const downs = reasons.filter((r) => r.direction === "down" && r.delta_prob < 0);
  const upSum = ups.reduce((acc, r) => acc + r.delta_prob, 0);
  const downSum = downs.reduce((acc, r) => acc + r.delta_prob, 0);

  if (!reasons.length) {
    return (
      <div className="rounded-md border border-border/60 bg-muted/20 p-4 text-sm text-muted-foreground">
        <div className="flex items-center gap-2">
          <Minus className="size-4" aria-hidden />
          <span>No reason codes returned for this order.</span>
        </div>
        <p className="mt-1 text-xs">
          The model produced a probability but no top-K SHAP contributions
          (this happens when the order sits near the prior — no feature moved
          it enough to clear the |Δ| ≥ 0.005 reporting floor).
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border/70 bg-muted/20 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">SHAP contribution waterfall</h3>
          <Badge variant="outline" className="text-[10px]">
            {reasons.length} reasons
          </Badge>
        </div>
        {mock && (
          <Badge variant="outline" className="border-warning/40 text-warning text-[10px]">
            mock SHAP
          </Badge>
        )}
      </div>

      {/* Cumulative summary line — the "base → ±deltas → final" narrative */}
      <div className="mb-4 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs font-mono">
        <span className="text-muted-foreground">base</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-foreground">
          {base.toFixed(3)}
        </span>
        {upSum > 0 && (
          <>
            <span className="text-muted-foreground">+</span>
            <span className="text-danger">{upSum.toFixed(3)} ↑</span>
          </>
        )}
        {downSum < 0 && (
          <>
            <span className="text-muted-foreground">−</span>
            <span className="text-success">{Math.abs(downSum).toFixed(3)} ↓</span>
          </>
        )}
        <span className="text-muted-foreground">=</span>
        <span
          className={`rounded px-1.5 py-0.5 font-semibold ${
            final === null
              ? "bg-muted text-muted-foreground"
              : final >= rejectT
                ? "bg-danger/20 text-danger"
                : final <= acceptT
                  ? "bg-success/20 text-success"
                  : "bg-warning/20 text-warning"
          }`}
        >
          {final === null ? "—" : `P(RTO) ${final.toFixed(3)}`}
        </span>
      </div>

      {/* Threshold ladder — shows WHERE the decision boundary sits */}
      <div className="mb-3 flex items-center gap-2 text-[10px] text-muted-foreground">
        <span className="font-mono">ACCEPT ≤ {acceptT.toFixed(2)}</span>
        <span className="text-muted-foreground/60">·</span>
        <span className="font-mono">REVIEW {acceptT.toFixed(2)}–{rejectT.toFixed(2)}</span>
        <span className="text-muted-foreground/60">·</span>
        <span className="font-mono">REJECT ≥ {rejectT.toFixed(2)}</span>
        {totalDelta > 0 && (
          <span className="ml-auto font-mono text-danger">
            net +{totalDelta.toFixed(3)}
          </span>
        )}
        {totalDelta < 0 && (
          <span className="ml-auto font-mono text-success">
            net {totalDelta.toFixed(3)}
          </span>
        )}
      </div>

      {/* The diverging bar chart */}
      <div className="space-y-1.5">
        {sorted.map((r, i) => (
          <ReasonBar key={`${r.feature}-${i}`} reason={r} maxAbs={maxAbs} />
        ))}
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
        Each bar = one feature&apos;s signed contribution to the model&apos;s
        RTO probability, relative to the base rate{" "}
        <span className="font-mono">{base.toFixed(3)}</span> (E[f(x)]).{" "}
        <span className="text-danger">Red ↑</span> pushes the order towards
        REJECT; <span className="text-success">green ↓</span> towards ACCEPT.
        Width ∝ |Δ|. The cumulative sum reconstructs the final{" "}
        <span className="font-mono">P(RTO) {final === null ? "—" : final.toFixed(3)}</span>{" "}
        via{" "}
        <code className="font-mono text-[10px]">
          f(x) = E[f(x)] + Σ SHAP
        </code>
        .
      </p>
    </div>
  );
}
