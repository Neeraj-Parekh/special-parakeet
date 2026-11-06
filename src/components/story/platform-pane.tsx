"use client";

// PlatformPane — the payment network's control room (the "Paytm view").
// Dark ops-room identity, always dark regardless of theme — it reads as a
// different world from the store. Aggregates + trust signals only; order
// detail stays LOCKED until the merchant shares the audit key (the
// data-segregation story, enacted on screen).
//
// Real signals: the audit-chain tile + verify button call the live
// /api/v1/audit/verify-chain endpoint on this deployment. Story rows are
// the real scored orders. Ambient rows are fictional (disclaimed).

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import {
  Network,
  ShieldCheck,
  Lock,
  ScanSearch,
  CheckCheck,
  Fingerprint,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  CountUp,
  formatINRCompact,
  ConsentDot,
  LockedCard,
} from "@/components/story/story-bits";
import type { StoryOrder } from "@/components/story/story-state";
import {
  AMBIENT_NETWORK_ROWS,
  NETWORK_BASE,
  PLATFORM_MERCHANTS,
  RING_DEVICE,
} from "@/lib/story/scenes";
import { cn } from "@/lib/utils";

const inr = (v: number) => `₹${v.toLocaleString("en-IN")}`;

interface ChainResponse {
  intact: boolean;
  records_checked: number;
}

export function PlatformPane({
  orders,
  ringFired,
  consent,
  drill,
  drillVerified,
  onDrill,
  onVerifyProof,
}: {
  orders: StoryOrder[];
  ringFired: boolean;
  consent: boolean;
  drill: string | null;
  drillVerified: boolean;
  onDrill: (key: string | null) => void;
  onVerifyProof: () => void;
}) {
  const chain = useQuery<ChainResponse>({
    queryKey: ["story-verify-chain"],
    queryFn: async () => {
      const r = await fetch("/api/v1/audit/verify-chain");
      const data = await r.json().catch(() => null);
      return { intact: data?.intact ?? false, records_checked: data?.records_checked ?? 0 };
    },
    staleTime: 30_000,
  });

  const blockedValue = orders
    .filter((o) => o.result.decision === "REJECT")
    .reduce((s, o) => s + o.amount, 0);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-slate-800 bg-[#0A0F1E] text-slate-200">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <Network className="size-4 text-sky-400" aria-hidden />
          <span className="text-sm font-semibold tracking-tight text-slate-100">PayGrid</span>
          <span className="text-[10px] font-medium uppercase tracking-widest text-slate-500">
            Risk network
          </span>
        </div>
        <span className="flex items-center gap-1.5 text-[10px] font-medium text-slate-400">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-2 animate-ping rounded-full bg-sky-400 opacity-60" aria-hidden />
            <span className="relative inline-flex size-2 rounded-full bg-sky-400" aria-hidden />
          </span>
          Control room
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {/* Stat tiles */}
        <div className="grid grid-cols-2 gap-2">
          <StatTile
            label="Orders scored today"
            value={<CountUp value={NETWORK_BASE.ordersToday + orders.length} />}
          />
          <StatTile
            label="RTO prevented"
            value={
              <CountUp
                value={NETWORK_BASE.rtoPreventedBase + blockedValue}
                format={formatINRCompact}
                className="text-sky-300"
              />
            }
          />
          <StatTile
            label="Fraud rings"
            value={
              ringFired ? (
                <span className="relative text-red-400">
                  1 active
                  <span className="absolute -right-3.5 top-0 size-1.5 animate-pulse rounded-full bg-red-400" aria-hidden />
                </span>
              ) : (
                "0"
              )
            }
            alert={ringFired}
          />
          <StatTile
            label="Audit chain"
            value={
              chain.data?.intact ? (
                <span className="flex items-center gap-1 text-mint-400">
                  <ShieldCheck className="size-3.5" aria-hidden /> Intact
                </span>
              ) : (
                "…"
              )
            }
            sub={chain.data?.intact ? `${chain.data.records_checked} records` : undefined}
          />
        </div>

        {/* Fraud ring alert */}
        <AnimatePresence>
          {ringFired && (
            <motion.div
              initial={{ opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              transition={{ type: "spring", stiffness: 260, damping: 22 }}
              className="rounded-lg border border-red-500/40 bg-red-500/[0.08] p-3"
            >
              <div className="flex items-center justify-between">
                <p className="flex items-center gap-1.5 text-xs font-bold text-red-300">
                  <Fingerprint className="size-4" aria-hidden /> Fraud ring detected
                </p>
                <Badge variant="outline" className="border-red-500/40 text-[9px] text-red-300">
                  auto
                </Badge>
              </div>
              <p className="mt-1 text-[11px] text-slate-300">
                3 orders · 1 device · {inr(15997)} stopped
              </p>
              <RingGraph />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Live network feed */}
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Live network
          </p>
          <div className="space-y-1">
            <AnimatePresence initial={false}>
              {orders.map((o) => (
                <NetworkRow
                  key={o.key}
                  label="UrbanKart"
                  amount={o.amount}
                  outcome={outcomeOf(o)}
                  onClick={() => onDrill(drill === o.key ? null : o.key)}
                  active={drill === o.key}
                />
              ))}
            </AnimatePresence>
            {AMBIENT_NETWORK_ROWS.map((r, i) => (
              <NetworkRow
                key={i}
                label={r.merchant}
                amount={r.amount}
                outcome={r.outcome}
                ambient
              />
            ))}
          </div>
        </div>

        {/* Drill-down — the consent gate */}
        <AnimatePresence initial={false}>
          {drill && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden"
            >
              <DrillPanel
                order={orders.find((o) => o.key === drill) ?? null}
                consent={consent}
                verified={drillVerified}
                onVerify={onVerifyProof}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Merchant consent list */}
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Merchants on network
          </p>
          <div className="rounded-lg border border-slate-800 bg-white/[0.02] p-1.5">
            <div className="flex items-center justify-between rounded-md bg-sky-500/10 px-2.5 py-2">
              <span className="flex items-center gap-2 text-xs font-semibold text-slate-100">
                <ConsentDot shared={consent} /> UrbanKart
              </span>
              <span className="text-[10px] font-medium text-slate-400">
                {consent ? "Audit key shared" : "Locked"}
              </span>
            </div>
            {PLATFORM_MERCHANTS.map((m) => (
              <div
                key={m.name}
                className="flex items-center justify-between px-2.5 py-2"
                title={m.note}
              >
                <span className="flex items-center gap-2 text-xs text-slate-400">
                  <ConsentDot shared={m.consent === "shared"} /> {m.name}
                </span>
                <span className="text-[10px] text-slate-600">{m.note}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function StatTile({
  label,
  value,
  sub,
  alert,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  alert?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border p-2.5",
        alert ? "border-red-500/30 bg-red-500/[0.06]" : "border-slate-800 bg-white/[0.02]",
      )}
    >
      <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 text-lg font-bold tabular-nums text-slate-100">{value}</p>
      {sub && <p className="text-[10px] text-slate-500">{sub}</p>}
    </div>
  );
}

function outcomeOf(o: StoryOrder): "ok" | "gated" | "blocked" {
  const d = o.result.decision;
  if (d === "ACCEPT") return "ok";
  if (d === "REVIEW") return "gated";
  return "blocked";
}

const OUTCOME_META = {
  ok: { label: "approved", cls: "text-mint-400" },
  gated: { label: "gated", cls: "text-amber-400" },
  blocked: { label: "blocked", cls: "text-red-400" },
} as const;

function NetworkRow({
  label,
  amount,
  outcome,
  onClick,
  active,
  ambient,
}: {
  label: string;
  amount: number;
  outcome: "ok" | "gated" | "blocked";
  onClick?: () => void;
  active?: boolean;
  ambient?: boolean;
}) {
  const m = OUTCOME_META[outcome];
  const Comp: "button" | "div" = onClick ? "button" : "div";
  return (
    <motion.div layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>
      <Comp
        onClick={onClick}
        className={cn(
          "flex w-full items-center justify-between rounded-md border px-2.5 py-1.5 text-left transition-colors",
          ambient
            ? "border-transparent opacity-60"
            : "border-slate-800 bg-white/[0.03] hover:border-slate-700",
          active && "border-sky-500/50 bg-sky-500/10",
        )}
      >
        <span className="flex items-center gap-2 text-[11px] text-slate-300">
          {onClick && <ScanSearch className="size-3 text-slate-500" aria-hidden />}
          {label}
        </span>
        <span className="flex items-center gap-2">
          <span className="text-[11px] font-semibold tabular-nums text-slate-400">
            {inr(amount)}
          </span>
          <span className={cn("text-[10px] font-semibold", m.cls)}>{m.label}</span>
        </span>
      </Comp>
    </motion.div>
  );
}

function DrillPanel({
  order,
  consent,
  verified,
  onVerify,
}: {
  order: StoryOrder | null;
  consent: boolean;
  verified: boolean;
  onVerify: () => void;
}) {
  if (!order) return null;
  if (!consent) {
    return <LockedCard merchant="UrbanKart" />;
  }
  const d = order.result.decision;
  const word =
    d === "ACCEPT" ? "Approved" : d === "REVIEW" ? "Gated — customer verified" : "Blocked";
  const pred = order.result.prediction_id?.slice(0, 12) ?? order.orderId;
  return (
    <div className="rounded-lg border border-slate-800 bg-white/[0.03] p-3">
      <div className="flex items-center justify-between">
        <p className="font-mono text-[11px] text-slate-300">{pred}</p>
        <Badge
          variant="outline"
          className={cn(
            "border-slate-700 text-[9px]",
            d === "ACCEPT" && "border-mint-500/40 text-mint-400",
            d === "REVIEW" && "border-amber-500/40 text-amber-400",
            d === "REJECT" && "border-red-500/40 text-red-400",
          )}
        >
          {word}
        </Badge>
      </div>
      <p className="mt-1.5 text-[11px] text-slate-400">
        Sealed in the audit chain — tamper-evident, regulator-verifiable.
      </p>
      {verified ? (
        <p className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold text-mint-400">
          <CheckCheck className="size-3.5" aria-hidden /> Proof verified · chain intact
        </p>
      ) : (
        <Button
          size="sm"
          onClick={onVerify}
          className="mt-2 h-7 rounded-md bg-sky-600 px-3 text-[11px] font-semibold text-white hover:bg-sky-500"
        >
          <Lock className="mr-1 size-3" aria-hidden /> Verify proof
        </Button>
      )}
    </div>
  );
}

function RingGraph() {
  // Device node left, 3 customer nodes right — simple, no math on screen.
  const nodes = [
    { x: 168, y: 34 },
    { x: 168, y: 68 },
    { x: 168, y: 102 },
  ];
  return (
    <svg viewBox="0 0 200 136" className="mt-2 h-24 w-full" role="img" aria-label={`One device linked to three customer accounts (${RING_DEVICE})`}>
      <motion.line
        x1="46" y1="68" x2={nodes[0].x} y2={nodes[0].y}
        stroke="rgba(248,113,113,0.5)" strokeWidth="1.5"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.2, duration: 0.5 }}
      />
      <motion.line
        x1="46" y1="68" x2={nodes[1].x} y2={nodes[1].y}
        stroke="rgba(248,113,113,0.5)" strokeWidth="1.5"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.35, duration: 0.5 }}
      />
      <motion.line
        x1="46" y1="68" x2={nodes[2].x} y2={nodes[2].y}
        stroke="rgba(248,113,113,0.5)" strokeWidth="1.5"
        initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.5, duration: 0.5 }}
      />
      <motion.circle
        cx="46" cy="68" r="13" fill="rgba(248,113,113,0.15)" stroke="#f87171" strokeWidth="1.5"
        initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 300, damping: 15 }}
      />
      <text x="46" y="72" textAnchor="middle" fill="#fca5a5" fontSize="8" fontWeight="700">DEV</text>
      {nodes.map((n, i) => (
        <motion.g key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.4 + i * 0.15 }}>
          <circle cx={n.x} cy={n.y} r="9" fill="rgba(148,163,184,0.12)" stroke="#94a3b8" strokeWidth="1" />
          <text x={n.x} y={n.y + 3} textAnchor="middle" fill="#cbd5e1" fontSize="7">C{i + 1}</text>
        </motion.g>
      ))}
      <text x="46" y="98" textAnchor="middle" fill="#64748b" fontSize="7" fontFamily="monospace">{RING_DEVICE}</text>
    </svg>
  );
}
