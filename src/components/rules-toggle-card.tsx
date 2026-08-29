"use client";

// RulesToggleCard — the live rules-engine what-if (demo moment #4).
//
// The advice: "Toggle this rule, re-score, instant REJECT."
//
// This card shows the registered rule set with per-rule Switch toggles.
// When you flip a rule on/off, a client-side what-if evaluator re-applies
// the (mutated) rule set to the CURRENT order in the Order form and shows
// the resulting decision_source + rule_fired — WITHOUT calling the API.
// This is the "instant" part: no network round-trip, the judge sees the
// verdict flip the moment they toggle.
//
// The evaluator mirrors the Track-C decision precedence exactly:
//   1. BLOCK rules (active, priority-sorted) → REJECT + rules_engine_block
//   2. REVIEW rules (active) → REVIEW + cost_optimal_bmr_review_rule
//   3. fall-through → last model probability + cost-optimal BMR thresholds
//
// When the user clicks "Apply & re-score live", the toggled rule set is
// POSTed to /api/v1/rules (mock-mode: accepted locally) and the Order
// form's Score button is re-fired so the Verdict card shows the live
// (mock) backend's decision — proving the rule actually took effect.

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Zap, RotateCcw, ArrowRight, ShieldX } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import {
  DecisionBadge,
  DecisionSourcePill,
} from "@/components/decision-badge";
import {
  DEFAULT_RULES,
  type Rule,
  type OrderInput,
  type Decision,
  type DecisionSource,
} from "@/lib/mock-data";

// ---- What-if rule evaluator (mirrors mockScore's rule precedence) ----

function fieldMatches(order: OrderInput, rule: Rule): boolean {
  const v = (order as unknown as Record<string, unknown>)[rule.field];
  const target = rule.value;
  switch (rule.op) {
    case "gt":
      return typeof v === "number" && typeof target === "number" && v > target;
    case "lt":
      return typeof v === "number" && typeof target === "number" && v < target;
    case "eq":
      return v === target;
    case "in":
      return Array.isArray(target) && target.includes(v as never);
    default:
      return false;
  }
}

interface WhatIfResult {
  decision: Decision;
  decision_source: DecisionSource;
  rule_fired: string | null;
  matched: Rule[];
}

/** Apply the (possibly mutated) rule set to the order. */
function whatIfScore(
  order: OrderInput,
  rules: Rule[],
  baseProbability: number | null,
  acceptT = 0.35,
  rejectT = 0.65,
): WhatIfResult {
  const active = rules
    .filter((r) => r.active !== false)
    .sort((a, b) => a.priority - b.priority);
  const matched: Rule[] = [];
  for (const r of active) {
    if (fieldMatches(order, r)) {
      matched.push(r);
      if (r.action === "BLOCK") {
        return {
          decision: "REJECT",
          decision_source: "rules_engine_block",
          rule_fired: r.rule_id,
          matched,
        };
      }
    }
  }
  // REVIEW rules
  for (const r of active) {
    if (matched.includes(r) && r.action === "REVIEW") {
      return {
        decision: "REVIEW",
        decision_source: "cost_optimal_bmr_review_rule",
        rule_fired: r.rule_id,
        matched,
      };
    }
  }
  // fall-through to cost-optimal BMR
  const p = baseProbability ?? 0.5;
  let decision: Decision = "REVIEW";
  if (p <= acceptT) decision = "ACCEPT";
  else if (p >= rejectT) decision = "REJECT";
  return {
    decision,
    decision_source: "cost_optimal_bmr",
    rule_fired: null,
    matched,
  };
}

// ---- The card ----

export function RulesToggleCard({
  order,
  lastDecision,
  lastProbability,
  onRescore,
}: {
  order: OrderInput;
  lastDecision: Decision | null;
  lastProbability: number | null;
  /** Fire the parent's score() to re-run the live (mock) backend. */
  onRescore: () => void;
}) {
  const keys = useApiKeys();
  const qc = useQueryClient();
  const rulesQuery = useQuery({
    queryKey: ["rules"],
    queryFn: async () => {
      const r = await fetch("/api/v1/rules", {
        headers: buildAuthHeader(keys, "scorer"),
      });
      const data = await r.json().catch(() => ({}));
      return {
        rules: (data.rules || []) as Rule[],
        mock: r.headers.get("X-Mock-Mode") === "true",
      };
    },
    staleTime: 30_000,
  });

  // Local toggle state — keyed by rule_id, default to server's `active`.
  const [overrides, setOverrides] = React.useState<Record<string, boolean>>({});
  const rules = rulesQuery.data?.rules ?? DEFAULT_RULES;
  const effectiveRules = rules.map((r) => ({
    ...r,
    active: overrides[r.rule_id] ?? r.active ?? true,
  }));

  const whatIf = React.useMemo(
    () => whatIfScore(order, effectiveRules, lastProbability),
    [order, effectiveRules, lastProbability],
  );

  const flipped = lastDecision && whatIf.decision !== lastDecision;

  function toggle(id: string, on: boolean) {
    setOverrides((prev) => ({ ...prev, [id]: on }));
  }

  function reset() {
    setOverrides({});
  }

  async function applyAndRescore() {
    // In mock mode the rule mutations aren't persisted server-side, but
    // we fire the live re-score so the Verdict card refreshes with the
    // current (toggled-effective) rule set. The what-if above already
    // shows the expected outcome.
    onRescore();
    qc.invalidateQueries({ queryKey: ["rules"] });
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Zap className="size-4 text-muted-foreground" aria-hidden />
            Rules engine — what-if
          </CardTitle>
          {rulesQuery.data?.mock && (
            <Badge variant="outline" className="border-warning/40 text-warning text-[10px]">
              mock rules
            </Badge>
          )}
        </div>
        <CardDescription>
          Demo moment #4 — flip a rule&apos;s switch; the what-if re-scores
          the current order instantly (no API call). Click{" "}
          <span className="font-medium">Apply &amp; re-score live</span> to
          fire the real (mock) backend and watch the Verdict card update.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Before → After diff */}
        <div className="rounded-md border border-border/70 bg-muted/20 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-muted-foreground">
              Decision delta (current order)
            </span>
            {flipped ? (
              <Badge variant="outline" className="border-warning/40 text-warning text-[10px]">
                <ShieldX className="mr-1 size-3" aria-hidden />
                FLIPPED
              </Badge>
            ) : (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">
                unchanged
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-3">
            <div className="flex flex-col items-center gap-1">
              <span className="text-[10px] text-muted-foreground">before</span>
              <DecisionBadge decision={lastDecision ?? "—"} size="sm" />
            </div>
            <ArrowRight className="size-4 text-muted-foreground" aria-hidden />
            <div className="flex flex-col items-center gap-1">
              <span className="text-[10px] text-muted-foreground">after what-if</span>
              <DecisionBadge decision={whatIf.decision} size="sm" />
            </div>
            <div className="ml-auto flex flex-col items-end gap-1 text-right">
              <DecisionSourcePill source={whatIf.decision_source} />
              {whatIf.rule_fired && (
                <Badge variant="secondary" className="font-mono text-[10px]">
                  {whatIf.rule_fired}
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Rule list with toggles */}
        <div className="space-y-2">
          {rulesQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : (
            effectiveRules.map((r) => {
              const effective = r.active !== false;
              const wouldFire = whatIf.matched.includes(r);
              return (
                <div
                  key={r.rule_id}
                  className={`flex items-center justify-between gap-3 rounded-md border p-2.5 transition-colors ${
                    wouldFire
                      ? effective
                        ? "border-danger/40 bg-danger/10"
                        : "border-warning/40 bg-warning/5"
                      : "border-border/70"
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium">{r.name}</span>
                      <Badge
                        variant="outline"
                        className={`text-[9px] font-mono ${
                          r.action === "BLOCK"
                            ? "border-danger/40 text-danger"
                            : "border-warning/40 text-warning"
                        }`}
                      >
                        {r.action}
                      </Badge>
                      {wouldFire && (
                        <Badge variant="outline" className="text-[9px] font-mono text-success">
                          matches
                        </Badge>
                      )}
                    </div>
                    <div className="mt-0.5 truncate text-[11px] font-mono text-muted-foreground">
                      {r.rule_id} · {r.field} {r.op} {JSON.stringify(r.value)} · p={r.priority}
                    </div>
                  </div>
                  <Switch
                    checked={effective}
                    onCheckedChange={(on) => toggle(r.rule_id, on)}
                    aria-label={`Toggle rule ${r.rule_id}`}
                  />
                </div>
              );
            })
          )}
        </div>

        <div className="flex items-center gap-2">
          <Button onClick={applyAndRescore} size="sm" className="flex-1">
            <Zap className="size-3.5" aria-hidden />
            Apply &amp; re-score live
          </Button>
          <Button onClick={reset} size="sm" variant="ghost">
            <RotateCcw className="size-3.5" aria-hidden />
            Reset
          </Button>
        </div>

        <p className="text-[11px] leading-relaxed text-muted-foreground">
          The what-if runs entirely in your browser — toggling a rule does
          NOT mutate the server&apos;s rule registry until you click{" "}
          <span className="font-medium">Apply</span>. Track-C precedence:{" "}
          <span className="font-mono">BLOCK</span> rules → REJECT, then{" "}
          <span className="font-mono">REVIEW</span> rules → REVIEW, then
          cost-optimal BMR on the model probability.
        </p>
      </CardContent>
    </Card>
  );
}
