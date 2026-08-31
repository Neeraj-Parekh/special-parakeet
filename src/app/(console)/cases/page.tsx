"use client";

// Cases — the REVIEW queue (G5 case management).
//
// Every REVIEW verdict above the risk threshold opens a case here. The
// SLA policy is priority-based (high 4h / medium 24h / low 72h per
// Track D V3 §11) and the "SLA remaining" column is a LIVE countdown —
// it ticks every second client-side while the queue itself refetches
// every 30s from GET /api/v1/cases (+ GET /api/v1/cases/overdue for
// the summary strip). Mock mode (X-Mock-Mode) is badged next to the
// header so a judge always knows the data provenance.

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowRight, Briefcase, KeyRound, Timer } from "lucide-react";

import {
  Card,
  CardContent,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import { formatINR, formatNum } from "@/lib/format";
import { cn } from "@/lib/utils";

// ----------------------------------------------------------------------------
// Types — mirrors CaseRecord from src/lib/cases/service.ts (declared locally
// so no server module is ever pulled into the client bundle).
// ----------------------------------------------------------------------------

type CaseStatus = "open" | "in_progress" | "pending_qa" | "resolved" | "closed";
type CasePriority = "low" | "medium" | "high";

interface CaseRecord {
  id: string;
  predictionId: string;
  customerId: string;
  orderId: string;
  amountInr: number;
  riskScore: number;
  priority: CasePriority;
  status: CaseStatus;
  assignedTo: string | null;
  qaReviewer: string | null;
  dueAt: string;
  slaBreached: boolean;
  openedAt: string;
  resolvedAt: string | null;
  resolution: string | null;
  resolutionNote: string | null;
}

interface CasesPageData {
  ok: boolean;
  auth: boolean; // 401/403 → a scorer key is required
  cases: CaseRecord[];
  mock: boolean;
}

interface OverduePageData {
  ok: boolean;
  overdue: CaseRecord[];
  total: number;
}

/** Statuses that still count as "in the queue" (SLA still running). */
const ACTIVE_STATUSES: ReadonlySet<CaseStatus> = new Set<CaseStatus>([
  "open",
  "in_progress",
  "pending_qa",
]);

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const FOUR_HOURS_MS = 4 * HOUR_MS;

// ----------------------------------------------------------------------------
// Live clock — one interval for the whole page, ticks every second.
// ----------------------------------------------------------------------------

function useNow(): number {
  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

// ----------------------------------------------------------------------------
// SLA helpers
// ----------------------------------------------------------------------------

/** "3d 4h" past 24h, else "H:MM:SS" (zero-padded); overdue gets a "-" prefix. */
function formatRemaining(remainingMs: number): string {
  const overdue = remainingMs < 0;
  const abs = Math.abs(remainingMs);
  if (abs >= DAY_MS) {
    const days = Math.floor(abs / DAY_MS);
    const hours = Math.floor((abs % DAY_MS) / HOUR_MS);
    return `${overdue ? "-" : ""}${days}d ${hours}h`;
  }
  const hours = Math.floor(abs / HOUR_MS);
  const minutes = Math.floor((abs % HOUR_MS) / 60_000);
  const seconds = Math.floor((abs % 60_000) / 1_000);
  return `${overdue ? "-" : ""}${hours}:${String(minutes).padStart(2, "0")}:${String(
    seconds,
  ).padStart(2, "0")}`;
}

function SlaCell({ record, now }: { record: CaseRecord; now: number }) {
  // Terminal cases have no clock left.
  if (record.resolvedAt !== null || record.status === "resolved" || record.status === "closed") {
    return <span className="font-mono text-xs text-muted-foreground/60">—</span>;
  }
  const due = Date.parse(record.dueAt);
  if (Number.isNaN(due)) {
    return <span className="font-mono text-xs text-muted-foreground/60">—</span>;
  }
  const remaining = due - now;
  const breached = remaining < 0 || record.slaBreached;
  const tone = breached
    ? "text-danger"
    : remaining <= FOUR_HOURS_MS
      ? "text-warning"
      : "text-mint-700";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("font-mono text-xs tabular-nums", tone)}>
        {formatRemaining(remaining)}
      </span>
      {breached && (
        <span className="inline-flex items-center rounded-md bg-signal-red/10 px-1.5 py-0.5 text-[9px] font-semibold tracking-wider text-danger">
          BREACHED
        </span>
      )}
    </span>
  );
}

// ----------------------------------------------------------------------------
// Pills
// ----------------------------------------------------------------------------

const PILL_BASE =
  "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide";

const PRIORITY_STYLES: Record<CasePriority, string> = {
  high: "border-signal-red/30 bg-signal-red/10 text-danger",
  medium: "border-gold-500/30 bg-gold-500/10 text-warning",
  low: "border-border bg-muted/40 text-muted-foreground",
};

const PRIORITY_LABELS: Record<CasePriority, string> = {
  high: "HIGH",
  medium: "MEDIUM",
  low: "LOW",
};

function PriorityPill({ priority }: { priority: CasePriority }) {
  return (
    <span className={cn(PILL_BASE, PRIORITY_STYLES[priority])}>
      {PRIORITY_LABELS[priority]}
    </span>
  );
}

const STATUS_STYLES: Record<CaseStatus, string> = {
  open: "border-border bg-muted/40 text-muted-foreground",
  in_progress: "border-brand-500/30 bg-brand-500/10 text-brand-600",
  pending_qa: "border-gold-500/30 bg-gold-500/10 text-warning",
  resolved: "border-mint-500/30 bg-mint-500/10 text-mint-700",
  closed: "border-border bg-muted/40 text-muted-foreground",
};

const STATUS_LABELS: Record<CaseStatus, string> = {
  open: "OPEN",
  in_progress: "IN PROGRESS",
  pending_qa: "PENDING QA",
  resolved: "RESOLVED",
  closed: "CLOSED",
};

function StatusPill({ status }: { status: CaseStatus }) {
  return (
    <span className={cn(PILL_BASE, STATUS_STYLES[status])}>{STATUS_LABELS[status]}</span>
  );
}

// ----------------------------------------------------------------------------
// Page
// ----------------------------------------------------------------------------

export default function CasesPage() {
  const keys = useApiKeys();
  const now = useNow();

  const casesQuery = useQuery({
    queryKey: ["cases", keys.scorerKey],
    queryFn: async (): Promise<CasesPageData> => {
      try {
        const r = await fetch("/api/v1/cases?limit=100", {
          headers: buildAuthHeader(keys, "scorer"),
        });
        const data = (await r.json().catch(() => null)) as
          | { cases?: CaseRecord[] }
          | null;
        const rows = data && Array.isArray(data.cases) ? data.cases : [];
        return {
          ok: r.ok,
          auth: r.status === 401 || r.status === 403,
          cases: rows,
          mock: r.headers.get("X-Mock-Mode") === "true",
        };
      } catch {
        return { ok: false, auth: false, cases: [], mock: false };
      }
    },
    refetchInterval: 30_000,
  });

  const overdueQuery = useQuery({
    queryKey: ["cases-overdue", keys.scorerKey],
    queryFn: async (): Promise<OverduePageData> => {
      try {
        const r = await fetch("/api/v1/cases/overdue?limit=100", {
          headers: buildAuthHeader(keys, "scorer"),
        });
        const data = (await r.json().catch(() => null)) as
          | { overdue?: CaseRecord[]; total?: number }
          | null;
        const rows = data && Array.isArray(data.overdue) ? data.overdue : [];
        return {
          ok: r.ok,
          overdue: rows,
          total: typeof data?.total === "number" ? data.total : 0,
        };
      } catch {
        return { ok: false, overdue: [], total: 0 };
      }
    },
    refetchInterval: 30_000,
  });

  const cases = casesQuery.data?.cases ?? [];
  const mock = casesQuery.data?.mock ?? false;
  const needsKey = casesQuery.data?.auth ?? false;
  const loading = casesQuery.isLoading || overdueQuery.isLoading;

  const active = cases.filter((c) => ACTIVE_STATUSES.has(c.status));
  const openCount = active.length;
  const breachedActive = active.filter((c) => c.slaBreached).length;
  // Overdue: prefer the dedicated endpoint; fall back to the slaBreached
  // flag on the fetched queue when the endpoint is unreachable.
  const overdueCount = overdueQuery.data?.ok
    ? overdueQuery.data.total
    : breachedActive;

  const highestPriority = highestAmong(active);
  const loadingQueue = casesQuery.isLoading;

  return (
    <div className="space-y-6">
      {/* Header + data provenance */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">Cases</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            The REVIEW queue — every REVIEW verdict opens a case with a
            priority-based SLA clock.
          </p>
        </div>
        {mock && <MockModeBadge mock />}
      </div>

      {/* Summary strip */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatChip
          label="Open cases"
          value={loading ? "…" : formatNum(openCount)}
          sub="open · in progress · pending QA"
        />
        <StatChip
          label="Overdue"
          value={loading ? "…" : formatNum(overdueCount)}
          sub="past SLA, unresolved"
          tone={overdueCount > 0 ? "danger" : undefined}
        />
        <StatChip
          label="Highest priority open"
          value={
            loading
              ? "…"
              : highestPriority === null
                ? "—"
                : PRIORITY_LABELS[highestPriority]
          }
          sub="most urgent open case"
          tone={
            highestPriority === "high"
              ? "danger"
              : highestPriority === "medium"
                ? "warning"
                : undefined
          }
        />
      </div>

      {/* 401/403 → key needed (mock mode still serves without auth) */}
      {needsKey && <KeyNotice />}

      {/* Queue */}
      {loadingQueue ? (
        <QueueSkeleton />
      ) : cases.length === 0 ? (
        <EmptyQueue />
      ) : (
        <div className="space-y-2">
          <CasesTable cases={cases} now={now} />
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Timer className="size-3" aria-hidden />
            SLA clocks tick every second; the queue refetches every 30 seconds.
          </p>
        </div>
      )}
    </div>
  );
}

/** Highest priority among the given (active) cases, or null when empty. */
function highestAmong(cases: CaseRecord[]): CasePriority | null {
  const order: CasePriority[] = ["high", "medium", "low"];
  for (const p of order) {
    if (cases.some((c) => c.priority === p)) return p;
  }
  return null;
}

// ----------------------------------------------------------------------------
// Subcomponents
// ----------------------------------------------------------------------------

function StatChip({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "danger" | "warning";
}) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3 shadow-card">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p
        className={cn(
          "mt-1 font-mono text-lg font-bold tabular-nums",
          tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-foreground",
        )}
      >
        {value}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>
    </div>
  );
}

function KeyNotice() {
  return (
    <Card className="gap-0 border-warning/40 bg-warning/5 py-0 shadow-card">
      <CardContent className="flex items-start gap-3 p-4">
        <KeyRound className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <p className="text-sm text-foreground">
          Enter a scorer key in the top bar to read the case queue — or rely on
          the mock fallback below.
        </p>
      </CardContent>
    </Card>
  );
}

const HEAD_CLS =
  "text-[11px] font-medium uppercase tracking-wider text-muted-foreground";

function CasesTable({ cases, now }: { cases: CaseRecord[]; now: number }) {
  return (
    <div className="max-h-[28rem] overflow-y-auto rounded-xl border border-border bg-card shadow-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className={HEAD_CLS}>Case</TableHead>
            <TableHead className={HEAD_CLS}>Order</TableHead>
            <TableHead className={HEAD_CLS}>Priority</TableHead>
            <TableHead className={HEAD_CLS}>Status</TableHead>
            <TableHead className={HEAD_CLS}>SLA remaining</TableHead>
            <TableHead className={cn(HEAD_CLS, "text-right")}>Amount</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cases.map((c) => (
            <TableRow key={c.id}>
              <TableCell>
                <div className="font-mono text-xs">{c.id}</div>
                <div className="font-mono text-[10px] text-muted-foreground">
                  pred {c.predictionId}
                </div>
              </TableCell>
              <TableCell>
                <div className="font-mono text-xs">{c.orderId}</div>
                <div className="font-mono text-[10px] text-muted-foreground">
                  {c.customerId}
                </div>
              </TableCell>
              <TableCell>
                <PriorityPill priority={c.priority} />
              </TableCell>
              <TableCell>
                <StatusPill status={c.status} />
              </TableCell>
              <TableCell>
                <SlaCell record={c} now={now} />
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">
                {formatINR(c.amountInr)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function QueueSkeleton() {
  return (
    <div className="max-h-[28rem] overflow-y-auto rounded-xl border border-border bg-card p-4 shadow-card">
      <div className="space-y-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    </div>
  );
}

function EmptyQueue() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card px-6 py-14 text-center shadow-card">
      <Briefcase className="size-8 text-muted-foreground/40" aria-hidden />
      <p className="max-w-md text-sm text-muted-foreground">
        No open cases. Cases open automatically when an order lands in REVIEW —
        score one now.
      </p>
      <Button asChild>
        <Link href="/score">
          Score a REVIEW order
          <ArrowRight className="size-4" aria-hidden />
        </Link>
      </Button>
      <p className="text-xs text-muted-foreground/70">
        The "Prior returns" demo order on the Risk Scoring page produces a
        REVIEW verdict.
      </p>
    </div>
  );
}
