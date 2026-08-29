"use client";

// CostCurveSlider — demo moment #6.
//
// "Slide the false-negative cost up; watch the REJECT threshold drop;
// the decision flips from REVIEW to REJECT." — docs/FOLLOWUP.md §0.
//
// This component renders the Bahnsen Bayes Minimum Risk (BMR) cost-curve
// surface as a live Recharts plot. The judge dials two sliders:
//
//   • C_fn  — false-negative cost in ₹ (the RTO loss if we ship + it
//             returns). Range ₹100–₹5000, default ₹600 (the deployed
//             constant; pass amount_inr to /risk/score to use the
//             per-transaction amount per Bahnsen Eq.(5)).
//   • p     — model P(RTO). Range 0–1, default 0.64 (the last scored
//             order's probability, passed in as a prop).
//
// The chart shows the three expected-cost lines as functions of p:
//   ACCEPT  = p · c_fn                                           (rising)
//   REVIEW  = c_otp + (1 − p)·c_fp + p·(1 − otp_eff)·c_fn        (gentle rise)
//   REJECT  = (1 − p) · c_block                                  (falling)
// The argmin at the current p is the BMR decision — highlighted in
// the callout. As C_fn climbs, the ACCEPT line pivots up sharply,
// REJECT's crossover drops left, and the cost-optimal action flips
// REVIEW→REJECT at lower probabilities.
//
// Reference:
//   Bahnsen, Stojanovic, Aouada, Ottersten — "Cost Sensitive Credit
//   Card Fraud Detection using Bayes Minimum Risk", ICMLA 2013,
//   DOI 10.1109/ICMLA.2013.68, Eq.(5). Drummond & Holte 2006 §3.6
//   for the cost-curve visualization idiom.
//
// Implementation notes:
//   • The math helpers (sampleCostCurve, bmrDecisionAt,
//     findDecisionCrossovers) live in src/lib/mock-data.ts and
//     mirror src/business/cost_optimizer.py::optimal_decision 1:1 —
//     so the slider's chart + the live CostBreakdownTable shown in
//     the Verdict card agree at every (p, C_fn) combination.
//   • shadcn Card + Badge + Slider + the chart.tsx ChartContainer wrap
//     Recharts in the same GitHub-dark palette as the SHAP waterfall.
//   • No indigo/blue — accent colours are success (ACCEPT=emerald),
//     warning (REVIEW=amber), danger (REJECT=red).

import * as React from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";
import { Sliders, BookOpen } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import {
  sampleCostCurve,
  findDecisionCrossovers,
  bmrDecisionAt,
  type Decision,
} from "@/lib/mock-data";
import { cn } from "@/lib/utils";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";

interface CostCurveSliderProps {
  /** Last scored order's probability — slider default. Falls back to 0.64. */
  probability?: number | null;
  /** Show a mock badge (mock-mode — no Python backend). */
  mock?: boolean;
}

/** Backend sweep data fetched from /api/v1/policy/cost-curves on mount. */
interface BackendSweep {
  optimalThreshold: number | null;
  nSamples: number | null;
  nPos: number | null;
  dataSource: string | null;
  mock: boolean;
}

// BMR weight defaults match the deployed cost_optimizer.py constants.
const C_FP_DEFAULT = 50; // false-positive cost (review friction)
const C_OTP_DEFAULT = 5; // OTP send + verification
const C_BLOCK_DEFAULT = 1000; // block cost (lost sale + churn)
const OTP_EFF_DEFAULT = 0.82; // OTP effectiveness

const C_FN_MIN = 100;
const C_FN_MAX = 5000;
const C_FN_DEFAULT = 600;
const P_DEFAULT = 0.64;

const chartConfig: ChartConfig = {
  ACCEPT: { label: "ACCEPT (ship)", color: "var(--success)" },
  REVIEW: { label: "REVIEW (OTP gate)", color: "var(--warning)" },
  REJECT: { label: "REJECT (block)", color: "var(--danger)" },
};

/** Format an INR amount — ₹ with thousands separators. */
function formatInr(n: number): string {
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

/** Decision → tailwind class for the callout border + text. */
function decisionClass(d: Decision): string {
  if (d === "ACCEPT") return "border-success/50 bg-success/10 text-success";
  if (d === "REVIEW") return "border-warning/50 bg-warning/10 text-warning";
  return "border-danger/50 bg-danger/10 text-danger";
}

export function CostCurveSlider({
  probability,
  mock = false,
}: CostCurveSliderProps) {
  const keys = useApiKeys();

  // Slider state.
  const [cFn, setCFn] = React.useState<number>(C_FN_DEFAULT);
  const [p, setP] = React.useState<number>(
    probability === null || probability === undefined ? P_DEFAULT : probability,
  );

  // Backend sweep — fetched once on mount from the real /v1/policy/cost-curves
  // endpoint (proxied via the Next.js API route). Proves the slider is wired
  // to the live Python backend; falls back to mock-mode when the backend is
  // unreachable (X-Mock-Mode header).
  const [sweep, setSweep] = React.useState<BackendSweep>({
    optimalThreshold: null,
    nSamples: null,
    nPos: null,
    dataSource: null,
    mock: true,
  });
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/v1/policy/cost-curves?n_resamples=100", {
          headers: buildAuthHeader(keys, "scorer"),
        });
        if (!r.ok || cancelled) return;
        const data = await r.json();
        if (data && Array.isArray(data.curves)) {
          setSweep({
            optimalThreshold:
              typeof data.optimal_threshold === "number"
                ? data.optimal_threshold
                : null,
            nSamples: typeof data.n_samples === "number" ? data.n_samples : null,
            nPos: typeof data.n_pos === "number" ? data.n_pos : null,
            dataSource: typeof data.data_source === "string" ? data.data_source : null,
            mock: r.headers.get("X-Mock-Mode") === "true",
          });
        }
      } catch {
        /* keep mock defaults */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [keys]);

  // When the parent passes a new scored-order probability, adopt it
  // (one-shot — the user can still override via the slider afterwards
  // because we only sync on `probability` prop changes).
  const lastSyncedProb = React.useRef<number | null>(null);
  React.useEffect(() => {
    if (
      probability !== null &&
      probability !== undefined &&
      probability !== lastSyncedProb.current
    ) {
      setP(probability);
      lastSyncedProb.current = probability;
    }
  }, [probability]);

  // Recompute the curve + the current decision when the slider moves.
  // The curve has 81 samples for smooth rendering without jank.
  const curve = React.useMemo(
    () =>
      sampleCostCurve(
        {
          c_fp: C_FP_DEFAULT,
          c_fn: cFn,
          c_otp: C_OTP_DEFAULT,
          c_block: C_BLOCK_DEFAULT,
          otp_effectiveness: OTP_EFF_DEFAULT,
        },
        81,
      ),
    [cFn],
  );
  const crossovers = React.useMemo(
    () =>
      findDecisionCrossovers({
        c_fp: C_FP_DEFAULT,
        c_fn: cFn,
        c_otp: C_OTP_DEFAULT,
        c_block: C_BLOCK_DEFAULT,
        otp_effectiveness: OTP_EFF_DEFAULT,
      }),
    [cFn],
  );
  const { decision, costs } = bmrDecisionAt(p, {
    c_fp: C_FP_DEFAULT,
    c_fn: cFn,
    c_otp: C_OTP_DEFAULT,
    c_block: C_BLOCK_DEFAULT,
    otp_effectiveness: OTP_EFF_DEFAULT,
  });

  // Y-domain — let the chart auto-scale but cap at a sane upper bound so
  // the c_fn = 5000 extreme doesn't squash the readable part.
  const yMax = Math.max(cFn, C_BLOCK_DEFAULT) * 1.05;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Sliders className="size-4 text-muted-foreground" aria-hidden />
            Cost-curve slider
            <Badge variant="outline" className="text-[10px]">
              demo #6
            </Badge>
          </CardTitle>
          <div className="flex items-center gap-2">
            {/* Live/mock badge — reflects the real /api/v1/policy/cost-curves fetch */}
            {sweep.mock ? (
              <MockModeBadge mock={true} />
            ) : (
              <Badge variant="outline" className="border-success/40 text-success text-[10px]">
                live backend sweep
              </Badge>
            )}
            <a
              href="https://doi.org/10.1109/ICMLA.2013.68"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-muted/30 px-2 py-0.5 text-[10px] text-muted-foreground transition-colors hover:text-foreground"
              title="Bahnsen, Stojanovic, Aouada, Ottersten — Cost Sensitive Credit Card Fraud Detection using Bayes Minimum Risk, ICMLA 2013, Eq.(5)."
            >
              <BookOpen className="size-2.5" aria-hidden />
              Bahnsen ICMLA 2013 · Eq.5
            </a>
          </div>
        </div>
        <CardDescription>
          Dial the false-negative cost and watch the BMR decision flip live.
          The chart shows the three expected-cost curves (₹) as functions of
          P(RTO); the vertical line marks the model&apos;s current probability.
          <span className="text-foreground/80">
            {" "}
            Slide <span className="font-mono">C_fn</span> up and the REJECT
            threshold drops — the cost-optimal decision flips REVIEW→REJECT at
            lower probabilities.
          </span>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Sliders row */}
        <div className="grid gap-5 md:grid-cols-2">
          {/* C_fn slider */}
          <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 p-3">
            <div className="flex items-baseline justify-between">
              <label
                htmlFor="ccs-cfn"
                className="text-xs font-medium text-muted-foreground"
              >
                C_fn — false-negative cost
              </label>
              <span className="font-mono text-sm font-semibold text-foreground">
                {formatInr(cFn)}
              </span>
            </div>
            <Slider
              id="ccs-cfn"
              min={C_FN_MIN}
              max={C_FN_MAX}
              step={50}
              value={[cFn]}
              onValueChange={(v) => setCFn(v[0] ?? C_FN_DEFAULT)}
              aria-label="False-negative cost in rupees"
              className="[&_[data-slot=slider-range]]:bg-danger [&_[data-slot=slider-thumb]]:border-danger"
            />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>{formatInr(C_FN_MIN)} (low-stakes)</span>
              <span>{formatInr(C_FN_MAX)} (high-value COD)</span>
            </div>
          </div>

          {/* P(RTO) slider */}
          <div className="space-y-2 rounded-md border border-border/70 bg-muted/20 p-3">
            <div className="flex items-baseline justify-between">
              <label
                htmlFor="ccs-prob"
                className="text-xs font-medium text-muted-foreground"
              >
                P(RTO) — model probability
              </label>
              <span className="font-mono text-sm font-semibold text-foreground">
                {p.toFixed(3)}
              </span>
            </div>
            <Slider
              id="ccs-prob"
              min={0}
              max={1000}
              step={5}
              value={[Math.round(p * 1000)]}
              onValueChange={(v) => setP((v[0] ?? 640) / 1000)}
              aria-label="Model probability of return-to-origin"
              className="[&_[data-slot=slider-range]]:bg-warning [&_[data-slot=slider-thumb]]:border-warning"
            />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>0.000 (safe)</span>
              <span>1.000 (certain RTO)</span>
            </div>
          </div>
        </div>

        {/* Live chart */}
        <ChartContainer
          config={chartConfig}
          className="aspect-[16/9] h-[260px] w-full"
        >
          <LineChart
            data={curve}
            margin={{ top: 4, right: 12, bottom: 4, left: 4 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="probability"
              domain={[0, 1]}
              tickCount={6}
              tickFormatter={(v: number) => v.toFixed(2)}
              tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              label={{
                value: "P(RTO)",
                position: "insideBottomRight",
                offset: -4,
                fill: "var(--muted-foreground)",
                fontSize: 10,
              }}
              stroke="var(--border)"
            />
            <YAxis
              domain={[0, yMax]}
              tickFormatter={(v: number) => `₹${Math.round(v).toLocaleString("en-IN")}`}
              tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              width={64}
              stroke="var(--border)"
            />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  formatter={(value, name) => (
                    <div className="flex w-full items-center justify-between gap-3">
                      <span className="text-muted-foreground">
                        {name}
                      </span>
                      <span className="font-mono text-foreground">
                        {formatInr(Number(value))}
                      </span>
                    </div>
                  )}
                  labelFormatter={(_, payload) => {
                    const pt = payload?.[0]?.payload as
                      | { probability: number; decision: Decision }
                      | undefined;
                    return pt
                      ? `p = ${pt.probability.toFixed(3)} · ${pt.decision}`
                      : "";
                  }}
                />
              }
            />
            {/* The three expected-cost lines */}
            <Line
              type="monotone"
              dataKey="ACCEPT"
              stroke="var(--success)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="REVIEW"
              stroke="var(--warning)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="REJECT"
              stroke="var(--danger)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            {/* Current-probability vertical marker */}
            <ReferenceLine
              x={p}
              stroke="var(--foreground)"
              strokeDasharray="4 4"
              strokeWidth={1.5}
              label={{
                value: `p=${p.toFixed(2)}`,
                position: "top",
                fill: "var(--foreground)",
                fontSize: 10,
              }}
            />
            {/* Crossover flip-point markers — vertical dashed rules */}
            {crossovers.map((c) => (
              <ReferenceLine
                key={`cross-${c}`}
                x={c}
                stroke="var(--muted-foreground)"
                strokeDasharray="2 2"
                strokeWidth={1}
                label={{
                  value: `flip`,
                  position: "insideTopLeft",
                  fill: "var(--muted-foreground)",
                  fontSize: 9,
                }}
              />
            ))}
            {/* Backend optimal-threshold marker — from the real /v1/policy/cost-curves sweep */}
            {sweep.optimalThreshold !== null && (
              <ReferenceLine
                x={sweep.optimalThreshold}
                stroke="var(--chart-4)"
                strokeWidth={2}
                label={{
                  value: `backend optimal t=${sweep.optimalThreshold.toFixed(2)}`,
                  position: "insideTopRight",
                  fill: "var(--chart-4)",
                  fontSize: 9,
                }}
              />
            )}
          </LineChart>
        </ChartContainer>

        {/* Legend */}
        <div className="flex flex-wrap items-center gap-3 text-[11px]">
          {(["ACCEPT", "REVIEW", "REJECT"] as const).map((d) => (
            <div
              key={d}
              className="flex items-center gap-1.5 text-muted-foreground"
            >
              <span
                className="inline-block h-2.5 w-4 rounded-[2px]"
                style={{
                  backgroundColor:
                    d === "ACCEPT"
                      ? "var(--success)"
                      : d === "REVIEW"
                        ? "var(--warning)"
                        : "var(--danger)",
                }}
                aria-hidden
              />
              <span className="font-mono">{d}</span>
            </div>
          ))}
          <span className="ml-auto text-muted-foreground">
            C_fp = {formatInr(C_FP_DEFAULT)} · C_otp = {formatInr(C_OTP_DEFAULT)} · C_block = {formatInr(C_BLOCK_DEFAULT)} · otp_eff = {OTP_EFF_DEFAULT}
          </span>
        </div>

        {/* Backend sweep meta — proves the fetch is wired (real n_samples + data_source) */}
        {sweep.nSamples !== null && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border/60 bg-muted/20 p-2 text-[10px] text-muted-foreground">
            <span className="font-mono">
              backend sweep: n_samples={sweep.nSamples.toLocaleString("en-IN")}
              {sweep.nPos !== null && ` · n_pos(RTO)=${sweep.nPos.toLocaleString("en-IN")}`}
            </span>
            {sweep.dataSource && (
              <span className="font-mono">data_source: {sweep.dataSource}</span>
            )}
            <span className={`font-mono ${sweep.mock ? "text-warning" : "text-success"}`}>
              {sweep.mock ? "(mock fallback — Python backend unreachable)" : "(live Drummond-Holte bootstrap sweep)"}
            </span>
          </div>
        )}

        {/* Current-decision callout */}
        <div
          className={cn(
            "rounded-md border p-4",
            decisionClass(decision),
          )}
        >
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide">
              BMR decision at p = {p.toFixed(3)}
            </span>
            {mock && (
              <Badge
                variant="outline"
                className="border-warning/40 text-warning text-[10px]"
              >
                mock weights
              </Badge>
            )}
          </div>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-bold tracking-tight">
                {decision}
              </span>
              <span className="text-xs text-muted-foreground">
                argmin expected cost
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px]">
              {(["ACCEPT", "REVIEW", "REJECT"] as const).map((d) => {
                const v = costs[d];
                return (
                  <span
                    key={d}
                    className={cn(
                      "font-mono",
                      d === decision ? "font-semibold" : "text-muted-foreground",
                    )}
                  >
                    {d}: {formatInr(v)}
                    {d === decision && " ←"}
                  </span>
                );
              })}
            </div>
          </div>
          {crossovers.length > 0 && (
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              Decision flips at{" "}
              <span className="font-mono text-foreground">
                {crossovers.map((c) => `p=${c.toFixed(3)}`).join(" · ")}
              </span>
              . {decision === "REJECT"
                ? "C_fn is high enough that blocking dominates — even a moderate RTO probability costs more than a false-decline."
                : decision === "REVIEW"
                  ? "The OTP gate is the cheapest hedge — its small fixed cost undercuts both ship (rising RTO loss) and block (constant ₹1K decline hit) at this probability."
                  : "The order amount at risk is low enough that shipping — even if it returns — beats the gate cost and the block hit."}
            </p>
          )}
        </div>

        {/* The demo-moment caption */}
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          The math mirrors{" "}
          <code className="font-mono text-[10px]">
            src/business/cost_optimizer.py::optimal_decision
          </code>{" "}
          1:1 — the same BMR rule that fires on every live{" "}
          <code className="font-mono text-[10px]">POST /risk/score</code>{" "}
          response. The Verdict card&apos;s{" "}
          <em>Cost breakdown</em> table above is computed from these exact
          weights. Drummond &amp; Holte 2006 §3.6 cost-curve visualization.
        </p>
      </CardContent>
    </Card>
  );
}
