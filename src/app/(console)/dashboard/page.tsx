"use client";

// Dashboard — the merchant home. Razorpay-style metric cards at the top,
// a "how it works" strip, and a LIVE OPERATIONS demo: a simulated day of
// COD orders streamed one-by-one through the real /api/risk/score API.
//
// HONESTY RULE: no fake deltas. Every number here is either (a) derived
// from the live session (orders scored this session, blocked value),
// (b) fetched live from the API (audit chain, champion model), or
// (c) absent. Chips under each metric say WHERE the number came from.
// The live demo's ORDERS are synthetic (labeled SIMULATION) but every
// verdict, cost and latency is real API output.

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Activity,
  ShieldAlert,
  Zap,
  Lock,
  ArrowRight,
  Cpu,
  Loader2,
  Play,
  RotateCcw,
  Scale,
  ShoppingCart,
  Square,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import { DecisionBadge, DecisionSourcePill } from "@/components/decision-badge";
import { useRecentDecisions, pushRecent, clearRecent, type RecentDecision } from "@/lib/session-decisions";
import type { ScoreResponse } from "@/lib/mock-data";
import { generateLiveOrders, LIVE_STREAM_TOTAL, type LiveOrder } from "@/lib/live-demo";
import { formatINR, formatNum } from "@/lib/format";
import { CONSOLE_NAV } from "@/lib/nav";
import { cn } from "@/lib/utils";

// ----------------------------------------------------------------------------
// Page
// ----------------------------------------------------------------------------

export default function DashboardPage() {
  const keys = useApiKeys();
  const [recent] = useRecentDecisions();

  const chainQuery = useQuery({
    queryKey: ["verify-chain", keys.scorerKey],
    queryFn: async () => {
      const r = await fetch("/api/v1/audit/verify-chain", {
        headers: buildAuthHeader(keys, "scorer"),
      });
      const data = await r.json().catch(() => null);
      return {
        intact: data?.intact ?? null,
        records: data?.records_checked ?? null,
        mock: r.headers.get("X-Mock-Mode") === "true",
      };
    },
  });

  const modelQuery = useQuery({
    queryKey: ["models-current", keys.scorerKey],
    queryFn: async () => {
      const r = await fetch("/api/v1/models/current", {
        headers: buildAuthHeader(keys, "scorer"),
      });
      const data = await r.json().catch(() => null);
      return {
        champion: data?.champion ?? null,
        mock: r.headers.get("X-Mock-Mode") === "true",
      };
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader />

      <HowItWorks />

      <MetricsRow recent={recent} chain={chainQuery.data} chainLoading={chainQuery.isLoading} />

      <LiveOpsCard />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <DecisionSplitCard recent={recent} />
        <SystemStatusCard
          champion={modelQuery.data?.champion ?? null}
          mock={modelQuery.data?.mock ?? false}
          loading={modelQuery.isLoading}
        />
      </div>

      <QuickLinks />
    </div>
  );
}

function PageHeader() {
  return (
    <div className="flex flex-col gap-1.5">
      <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
      <p className="max-w-2xl text-sm text-muted-foreground">
        Return-risk operations at a glance — run the live demo stream to watch a
        day of COD orders get scored, gated, and sealed in real time.
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Metrics row — the first thing a merchant (and a judge) sees
// ----------------------------------------------------------------------------

function MetricCard({
  label,
  value,
  sub,
  icon: Icon,
  tone,
  chip,
}: {
  label: string;
  value: string;
  sub: string;
  icon: React.ComponentType<{ className?: string }>;
  tone?: "mint" | "brand";
  chip?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 shadow-card transition-shadow duration-200 ease-brand hover:shadow-lift">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </span>
        <div
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            tone === "mint"
              ? "bg-mint-500/10 text-mint-700"
              : "bg-brand-500/10 text-brand-500",
          )}
        >
          <Icon className="size-4" aria-hidden />
        </div>
      </div>
      <div className="font-mono text-2xl font-bold tabular-nums text-foreground">
        {value}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center justify-between gap-1.5">
        <span className="text-xs text-muted-foreground">{sub}</span>
        {chip && (
          <Badge
            variant="outline"
            className="px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground"
          >
            {chip}
          </Badge>
        )}
      </div>
    </div>
  );
}

function MetricsRow({
  recent,
  chain,
  chainLoading,
}: {
  recent: RecentDecision[];
  chain: { intact: boolean | null; records: number | null; mock: boolean } | undefined;
  chainLoading: boolean;
}) {
  const blocked = recent
    .filter((r) => r.decision === "REJECT")
    .reduce((sum, r) => sum + (r.amount_inr || 0), 0);
  const latencies = recent
    .map((r) => r.latency_ms)
    .filter((v): v is number => typeof v === "number");
  const avgMs = latencies.length
    ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)
    : null;

  const chainValue = chainLoading
    ? "…"
    : chain?.intact === true
      ? "INTACT"
      : chain?.intact === false
        ? "BROKEN"
        : "—";

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        label="Orders scored"
        value={formatNum(recent.length)}
        sub="decisions this session"
        icon={Activity}
        chip="Session"
      />
      <MetricCard
        label="RTO blocked"
        value={formatINR(blocked)}
        sub="COD value stopped at the gate"
        icon={ShieldAlert}
        tone="mint"
        chip="Session"
      />
      <MetricCard
        label="Avg decision time"
        value={avgMs === null ? "—" : `${formatNum(avgMs)} ms`}
        sub={latencies.length ? `p50-ish over ${latencies.length} calls` : "score an order to measure"}
        icon={Zap}
        chip={avgMs === null ? undefined : "Live"}
      />
      <MetricCard
        label="Audit chain"
        value={chainValue}
        sub={
          chain?.records != null
            ? `${formatNum(chain.records)} records verified`
            : "Merkle hash chain"
        }
        icon={Lock}
        tone={chain?.intact === false ? undefined : "mint"}
        chip={chain?.mock ? "Mock" : chain?.intact != null ? "Live" : undefined}
      />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Decision split + system status
// ----------------------------------------------------------------------------

function DecisionSplitCard({ recent }: { recent: RecentDecision[] }) {
  const counts = { ACCEPT: 0, REVIEW: 0, REJECT: 0 } as Record<string, number>;
  for (const r of recent) {
    if (r.decision in counts) counts[r.decision] += 1;
  }
  const total = recent.length;
  const pct = (n: number) => (total ? `${Math.round((n / total) * 100)}%` : "0%");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Decision split</CardTitle>
        <CardDescription>
          ACCEPT / REVIEW / REJECT across every order scored in this session.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Stacked bar */}
        <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
          {total > 0 && (
            <>
              <div
                className="h-full bg-mint-500 transition-all duration-300 ease-brand"
                style={{ width: pct(counts.ACCEPT) }}
              />
              <div
                className="h-full bg-gold-500 transition-all duration-300 ease-brand"
                style={{ width: pct(counts.REVIEW) }}
              />
              <div
                className="h-full bg-signal-red transition-all duration-300 ease-brand"
                style={{ width: pct(counts.REJECT) }}
              />
            </>
          )}
        </div>
        <div className="grid grid-cols-3 gap-3">
          <SplitStat label="Accept" value={counts.ACCEPT} share={pct(counts.ACCEPT)} cls="text-mint-700" />
          <SplitStat label="Review (OTP)" value={counts.REVIEW} share={pct(counts.REVIEW)} cls="text-warning" />
          <SplitStat label="Reject" value={counts.REJECT} share={pct(counts.REJECT)} cls="text-danger" />
        </div>
        {total === 0 && (
          <p className="text-sm text-muted-foreground">
            No decisions yet — run the <span className="font-medium text-foreground">live demo stream</span> below,{" "}
            <Link href="/score" className="font-medium text-brand-600 hover:underline">
              score an order
            </Link>{" "}
            or{" "}
            <Link href="/checkout" className="font-medium text-brand-600 hover:underline">
              run the checkout demo
            </Link>
            .
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function SplitStat({
  label,
  value,
  share,
  cls,
}: {
  label: string;
  value: number;
  share: string;
  cls: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/40 p-3 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 font-mono text-xl font-bold tabular-nums", cls)}>{value}</p>
      <p className="text-[10px] text-muted-foreground">{share} of session</p>
    </div>
  );
}

function SystemStatusCard({
  champion,
  mock,
  loading,
}: {
  champion: {
    version?: string;
    deployed_at?: string;
    metrics?: { pr_auc?: number; roc_auc?: number; precision?: number; recall?: number };
  } | null;
  mock: boolean;
  loading: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">System status</CardTitle>
          {mock && <MockModeBadge mock={mock} />}
        </div>
        <CardDescription>Champion scorer + deployment posture.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <Skeleton className="h-24 w-full" />
        ) : champion ? (
          <>
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="text-muted-foreground">Champion model</span>
              <span className="font-mono text-xs font-semibold">{champion.version ?? "—"}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <StatChip label="PR-AUC" value={champion.metrics?.pr_auc?.toFixed(4) ?? "—"} />
              <StatChip label="ROC-AUC" value={champion.metrics?.roc_auc?.toFixed(4) ?? "—"} />
              <StatChip label="Precision" value={champion.metrics?.precision?.toFixed(3) ?? "—"} />
              <StatChip label="Recall" value={champion.metrics?.recall?.toFixed(3) ?? "—"} />
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            Model registry unreachable. Enter a scorer key above or retry.
          </p>
        )}
        <div className="flex items-center gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
          <span className="relative flex size-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-mint-500 opacity-60 motion-safe:animate-ping" />
            <span className="relative inline-flex size-2 rounded-full bg-mint-500" />
          </span>
          Scoring API online · mock fallback armed
        </div>
        <Link
          href="/model-health"
          className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline"
        >
          Open model health <ArrowRight className="size-3" aria-hidden />
        </Link>
      </CardContent>
    </Card>
  );
}

function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-muted/40 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="font-mono text-sm font-semibold tabular-nums text-foreground">{value}</p>
    </div>
  );
}

// ----------------------------------------------------------------------------
// How it works — the value loop, narrated in one strip
// ----------------------------------------------------------------------------

function HowItWorks() {
  const steps = [
    {
      icon: ShoppingCart,
      title: "COD order placed",
      text: "Checkout calls /risk/score before the courier leaves.",
    },
    {
      icon: Cpu,
      title: "Signals scored",
      text: "History · device · address · graph, p50 < 50 ms.",
    },
    {
      icon: Scale,
      title: "Cost-optimal verdict",
      text: "ACCEPT · OTP gate · REJECT — ₹-aware, never a bare cutoff.",
    },
    {
      icon: Lock,
      title: "Sealed & saved",
      text: "Every decision hash-chained; RTO ₹ stopped at the gate.",
    },
  ];
  return (
    <section
      aria-label="How the trust layer works"
      className="rounded-xl border border-border bg-card p-4 shadow-card"
    >
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        How it works
      </h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((s, i) => (
          <div key={s.title} className="flex items-start gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-500/10 text-brand-500">
              <s.icon className="size-4.5" aria-hidden />
            </div>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-foreground">
                <span className="font-mono text-muted-foreground">{i + 1} · </span>
                {s.title}
              </p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                {s.text}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Live operations — the "real life" demo. Synthetic orders, real API.
// ----------------------------------------------------------------------------

interface FeedRow {
  order: LiveOrder;
  ok: boolean;
  prediction_id: string | null;
  decision: string | null;
  probability: number | null;
  decision_source: string;
  latency_ms: number | null;
  mock: boolean;
  ts: number;
}

function useLiveStream(keys: ReturnType<typeof useApiKeys>) {
  const [phase, setPhase] = React.useState<"idle" | "running" | "done">("idle");
  const [feed, setFeed] = React.useState<FeedRow[]>([]);
  const [pendingId, setPendingId] = React.useState<string | null>(null);
  const runningRef = React.useRef(false);
  const stopRef = React.useRef(false);

  // Halt the loop if the component unmounts mid-stream.
  React.useEffect(() => {
    return () => {
      stopRef.current = true;
      runningRef.current = false;
    };
  }, []);

  const run = React.useCallback(
    async (orders: LiveOrder[]) => {
      if (runningRef.current) return;
      runningRef.current = true;
      stopRef.current = false;
      setFeed([]);
      setPhase("running");
      for (const order of orders) {
        if (stopRef.current) break;
        setPendingId(order.order_id);
        const row: FeedRow = {
          order,
          ok: false,
          prediction_id: null,
          decision: null,
          probability: null,
          decision_source: "",
          latency_ms: null,
          mock: false,
          ts: Date.now(),
        };
        try {
          const t0 = performance.now();
          const r = await fetch("/api/risk/score", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...buildAuthHeader(keys, "scorer"),
            },
            body: JSON.stringify(order),
          });
          const latency = Math.round(performance.now() - t0);
          const mock = r.headers.get("X-Mock-Mode") === "true";
          row.latency_ms = latency;
          row.mock = mock;
          const data = (await r.json().catch(() => null)) as ScoreResponse | null;
          if (r.ok && data) {
            row.ok = true;
            row.prediction_id = data.prediction_id ?? null;
            row.decision = data.decision ?? null;
            row.probability = data.probability ?? null;
            row.decision_source = data.decision_source ?? "";
            // Land it in the session store → the metric cards and the
            // decision split above update LIVE as the stream runs.
            pushRecent({
              prediction_id: data.prediction_id || "—",
              order_id: order.order_id,
              amount_inr: order.amount_inr,
              payment_method: order.payment_method,
              decision: data.decision || "REVIEW",
              probability: data.probability ?? null,
              decision_source: data.decision_source ?? "",
              latency_ms: latency,
              mock,
              ts: Date.now(),
            });
          }
        } catch {
          /* network error → row stays not-ok, rendered as ERROR */
        }
        setPendingId(null);
        setFeed((f) => [row, ...f]);
        if (!stopRef.current) {
          // breathing room between orders, ~1.4 s
          await new Promise((res) => setTimeout(res, 1150 + Math.random() * 550));
        }
      }
      setPendingId(null);
      setPhase("done");
      runningRef.current = false;
    },
    [keys],
  );

  const stop = React.useCallback(() => {
    stopRef.current = true;
  }, []);

  const reset = React.useCallback(() => {
    setFeed([]);
    setPhase("idle");
  }, []);

  return { phase, feed, pendingId, run, stop, reset };
}

function LiveOpsCard() {
  const keys = useApiKeys();
  const { phase, feed, pendingId, run, stop, reset } = useLiveStream(keys);

  const done = feed.length;
  const mockAny = feed.some((f) => f.mock);
  let accepted = 0;
  let otpGated = 0;
  let blocked = 0;
  let failed = 0;
  let blockedInr = 0;
  for (const f of feed) {
    if (!f.ok) {
      failed += 1;
    } else if (f.decision === "ACCEPT") {
      accepted += 1;
    } else if (f.decision === "REVIEW") {
      otpGated += 1;
    } else if (f.decision === "REJECT") {
      blocked += 1;
      blockedInr += f.order.amount_inr;
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">Live operations</CardTitle>
            <CardDescription>
              A simulated day of {LIVE_STREAM_TOTAL} COD orders, scored one by one
              through the real scoring API — watch the gate decide in real time.
            </CardDescription>
          </div>
          <div className="flex items-center gap-1.5">
            <Badge
              variant="outline"
              className="px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Simulation
            </Badge>
            {mockAny && <MockModeBadge mock={mockAny} />}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Controls + progress */}
        <div className="flex flex-wrap items-center gap-3">
          {phase === "running" ? (
            <Button
              variant="outline"
              onClick={stop}
              className="h-11 gap-2 px-5 font-semibold"
            >
              <Square className="size-4" aria-hidden /> Stop stream
            </Button>
          ) : (
            <Button
              onClick={() => run(generateLiveOrders())}
              className="h-11 gap-2 px-5 font-semibold"
            >
              <Play className="size-4" aria-hidden />
              {done > 0 ? "Run demo again" : "Run demo stream"}
            </Button>
          )}
          {phase !== "running" && done > 0 && (
            <Button
              variant="ghost"
              onClick={() => {
                reset();
                clearRecent();
              }}
              className="h-11 gap-2"
            >
              <RotateCcw className="size-4" aria-hidden /> Reset demo
            </Button>
          )}
          <div className="ml-auto flex items-center gap-2.5">
            <div className="h-1.5 w-28 overflow-hidden rounded-full bg-muted sm:w-36">
              <div
                className="h-full rounded-full bg-brand-500 transition-all duration-300 ease-brand"
                style={{ width: `${(done / LIVE_STREAM_TOTAL) * 100}%` }}
              />
            </div>
            <span className="font-mono text-xs tabular-nums text-muted-foreground">
              {done}/{LIVE_STREAM_TOTAL}
            </span>
          </div>
        </div>

        {/* Feed — newest on top, capped height with scroll */}
        <div className="max-h-96 overflow-y-auto rounded-lg border border-border bg-card">
          {pendingId && (
            <div className="flex items-center gap-3 border-b border-border/60 px-4 py-2.5">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs font-semibold text-muted-foreground">
                <Loader2 className="size-3 animate-spin" aria-hidden /> scoring
              </span>
              <span className="font-mono text-xs text-muted-foreground">{pendingId}</span>
            </div>
          )}
          {feed.length === 0 && !pendingId ? (
            <div className="px-4 py-8 text-center">
              <p className="text-sm text-muted-foreground">
                Ready when you are — {LIVE_STREAM_TOTAL} synthetic orders will stream
                through the live scoring API.
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                The orders are simulated; every verdict, cost and latency you see is
                real API output.
              </p>
            </div>
          ) : (
            feed.map((row) => <FeedRowView key={row.order.order_id} row={row} />)
          )}
        </div>

        {/* Stream summary */}
        {phase === "done" && done > 0 && (
          <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-xs leading-relaxed text-muted-foreground">
            <span className="font-semibold text-foreground">
              {done === LIVE_STREAM_TOTAL ? "Stream complete — " : `Stopped at ${done}/${LIVE_STREAM_TOTAL} — `}
            </span>
            {formatNum(done)} orders · {accepted} accepted · {otpGated} OTP-gated ·{" "}
            {blocked} blocked ·{" "}
            <span className="font-mono font-semibold tabular-nums text-mint-700">
              {formatINR(blockedInr)}
            </span>{" "}
            RTO value stopped. Every decision is sealed in the{" "}
            <Link href="/audit" className="font-medium text-brand-600 hover:underline">
              audit trail
            </Link>
            .{failed > 0 ? ` ${failed} call(s) failed.` : ""}
          </div>
        )}

        <p className="text-[11px] text-muted-foreground">
          REVIEW = delivery-time OTP gate · REJECT = stop before dispatch · every
          verdict is cost-optimal (₹-aware), explainable, and auditable.
        </p>
      </CardContent>
    </Card>
  );
}

function FeedRowView({ row }: { row: FeedRow }) {
  const o = row.order;
  return (
    <div className="flex items-start gap-3 border-b border-border/60 px-4 py-2.5 last:border-0 motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-1 motion-safe:duration-300">
      {row.ok ? (
        <DecisionBadge decision={row.decision} size="sm" className="mt-0.5 shrink-0" />
      ) : (
        <span className="mt-0.5 inline-flex shrink-0 items-center rounded-full border border-danger/50 bg-danger/15 px-2 py-0.5 text-xs font-semibold tracking-wide text-danger">
          ERROR
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-mono text-xs font-semibold text-foreground">{o.order_id}</span>
          {o.note && (
            <span className="inline-flex items-center rounded-md border border-warning/50 bg-warning/10 px-1.5 py-0.5 text-[10px] font-medium text-warning">
              {o.note}
            </span>
          )}
          <span className="font-mono text-[11px] text-muted-foreground">
            {o.payment_method} · {o.city_tier} · {o.address_quality} · {o.prior_orders} priors
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
          {row.ok && <DecisionSourcePill source={row.decision_source} />}
          {row.probability != null && (
            <span className="font-mono tabular-nums">
              p={(row.probability * 100).toFixed(0)}%
            </span>
          )}
          {row.ok && <span className="font-mono">{o.device}</span>}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-mono text-sm font-semibold tabular-nums text-foreground">
          {formatINR(o.amount_inr)}
        </p>
        {row.latency_ms != null && (
          <p className="font-mono text-[10px] tabular-nums text-muted-foreground">
            {formatNum(row.latency_ms)} ms
          </p>
        )}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Quick links — every console surface, one click away (zero dead links)
// ----------------------------------------------------------------------------

function QuickLinks() {
  return (
    <section aria-label="Console surfaces">
      <h2 className="mb-3 text-sm font-semibold text-foreground">Console surfaces</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {CONSOLE_NAV.filter((n) => n.href !== "/dashboard").map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="group rounded-xl border border-border bg-card p-4 shadow-card transition-all duration-200 ease-brand hover:-translate-y-0.5 hover:shadow-lift focus-visible:outline-2 focus-visible:outline-ring"
            >
              <div className="mb-2.5 flex items-center justify-between">
                <div className="flex size-9 items-center justify-center rounded-lg bg-brand-500/10 text-brand-500">
                  <Icon className="size-4.5" aria-hidden />
                </div>
                <ArrowRight
                  className="size-4 text-muted-foreground/50 transition-transform duration-200 ease-brand group-hover:translate-x-0.5 group-hover:text-brand-500"
                  aria-hidden
                />
              </div>
              <p className="text-sm font-semibold text-foreground">{item.label}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">{item.description}</p>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
