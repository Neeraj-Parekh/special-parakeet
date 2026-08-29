"use client";

// NarrativePivotCard — the "turn your weakest number into proof of insight"
// card the advice explicitly demands.
//
// Quote: "Stop defending the Amazon model. Use it as a teaching moment."
//   "We trained on Amazon India data — 128K real orders. Without
//   per-customer history, we got PR-AUC 0.10. That's honest — address-level
//   features alone aren't enough. Then we validated on Olist Brazilian data
//   — 99K orders WITH real customer IDs. Same model. Same hyperparameters.
//   PR-AUC jumped to 0.40 — a 32× baseline lift. This proves our thesis:
//   the real signal in RTO prediction is per-merchant and per-customer
//   behaviour history, not pincode averages. Razorpay has this data. We
//   built the platform to use it."
//
// This card renders that narrative visually — a before/after split showing
// the 0.10 → 0.40 jump, the 32× baseline lift, and the "Razorpay has this
// data" call-to-action. It's the single most important slide on the
// dashboard for a judge who scans for "is this honest + does it work?"

import * as React from "react";
import { ArrowRight, TrendingUp, Database, Users } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";

interface ModelStat {
  label: string;
  dataset: string;
  orders: string;
  prAuc: number;
  brier: number;
  hasCustomerIds: boolean;
  blurb: string;
  accent: "muted" | "success";
}

const STATS: ModelStat[] = [
  {
    label: "Amazon India",
    dataset: "amazon_histgb_20260827",
    orders: "128K",
    prAuc: 0.1027,
    brier: 0.0179,
    hasCustomerIds: false,
    blurb:
      "Trained on 128K real orders. No per-customer history available — address-level features alone hit a ~0.12 ceiling.",
    accent: "muted",
  },
  {
    label: "Olist Brazil",
    dataset: "rto_olist_histgb_20260828",
    orders: "99K",
    prAuc: 0.395,
    brier: 0.0439,
    hasCustomerIds: true,
    blurb:
      "Same model. Same hyperparameters. Real customer IDs unlocked a 3.8× PR-AUC lift — 32× the baseline.",
    accent: "success",
  },
];

export function NarrativePivotCard() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <TrendingUp className="size-4 text-success" aria-hidden />
            The honest pivot
          </CardTitle>
          <Badge variant="outline" className="text-[10px]">
            32× baseline lift
          </Badge>
        </div>
        <CardDescription>
          We don&apos;t hide the 0.10 — we use it as proof. The signal in RTO
          prediction is <span className="font-semibold">per-customer history</span>,
          not pincode averages. Razorpay has this data; we built the platform
          to use it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* The before → after split */}
        <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-2">
          {STATS.map((s, i) => (
            <React.Fragment key={s.label}>
              <div
                className={`rounded-lg border p-3 ${
                  s.accent === "success"
                    ? "border-success/40 bg-success/10"
                    : "border-border/70 bg-muted/30"
                }`}
              >
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-semibold">{s.label}</span>
                  {s.hasCustomerIds ? (
                    <Badge variant="outline" className="border-success/40 text-success text-[9px]">
                      <Users className="mr-1 size-2.5" aria-hidden />
                      customer IDs
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-[9px] text-muted-foreground">
                      <Database className="mr-1 size-2.5" aria-hidden />
                      no customer IDs
                    </Badge>
                  )}
                </div>
                <div className="mb-2">
                  <div className="text-2xl font-bold tabular-nums">
                    {s.prAuc.toFixed(4)}
                  </div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                    PR-AUC
                  </div>
                </div>
                <Progress
                  value={s.prAuc * 100}
                  className={`h-1.5 ${s.accent === "success" ? "[&>div]:bg-success" : ""}`}
                />
                <div className="mt-2 grid grid-cols-2 gap-1 text-[10px] text-muted-foreground">
                  <span>Brier: <span className="font-mono text-foreground">{s.brier.toFixed(4)}</span></span>
                  <span>Orders: <span className="font-mono text-foreground">{s.orders}</span></span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                  {s.blurb}
                </p>
                <div className="mt-2 truncate font-mono text-[9px] text-muted-foreground/80" title={s.dataset}>
                  {s.dataset}
                </div>
              </div>
              {i === 0 && (
                <div className="flex flex-col items-center justify-center px-1">
                  <ArrowRight className="size-5 text-success" aria-hidden />
                  <span className="mt-1 rounded-full bg-success/20 px-1.5 py-0.5 text-[9px] font-bold text-success">
                    3.8×
                  </span>
                </div>
              )}
            </React.Fragment>
          ))}
        </div>

        {/* The pitch one-liner a judge can read aloud */}
        <div className="rounded-md border border-success/30 bg-success/5 p-3">
          <p className="text-sm leading-relaxed text-foreground/90">
            <span className="font-semibold">The thesis:</span> address-level
            features cap out at PR-AUC{" "}
            <span className="font-mono font-semibold">0.10</span>. Add real
            per-customer history and the{" "}
            <span className="font-semibold">same model</span> hits{" "}
            <span className="font-mono font-semibold text-success">0.40</span>{" "}
            — <span className="font-semibold">32× the baseline lift</span>.
            Razorpay&apos;s merchant graph has this signal natively. Our
            inference path already consumes it via{" "}
            <code className="rounded bg-muted px-1 font-mono text-[11px]">
              ?dataset=olist
            </code>
            .
          </p>
        </div>

        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Numbers are held-out test-slice PR-AUC (not training). Amazon has
          no <code className="font-mono">customer_id</code> column (the
          ceiling is ~0.12 — verified). Olist exposes real{" "}
          <code className="font-mono">customer_unique_id</code>, unlocking
          the <code className="font-mono">user_rto_rate</code> feature that
          alone carries the lift. Full provenance in{" "}
          <code className="font-mono">data/olist/metrics.json</code> +{" "}
          <code className="font-mono">reports/kaggle/MODEL_CARD.md</code>.
        </p>
      </CardContent>
    </Card>
  );
}
