"use client";

// Dashboard — the merchant home. Razorpay-style metric cards at the top.
//
// HONESTY RULE: no fake deltas. Every number here is either (a) derived
// from the live session (orders scored this session, blocked value),
// (b) fetched live from the API (audit chain, champion model), or
// (c) absent. Chips under each metric say WHERE the number came from.

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Activity,
  ShieldAlert,
  Zap,
  Lock,
  ArrowRight,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import { useRecentDecisions, type RecentDecision } from "@/lib/session-decisions";
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

      <MetricsRow recent={recent} chain={chainQuery.data} chainLoading={chainQuery.isLoading} />

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
        Return-risk operations at a glance — scoring volume, blocked COD value, and the
        state of the sealed audit chain.
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
            No decisions yet —{" "}
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
