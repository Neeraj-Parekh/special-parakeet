"use client";

// MerchantPane — the seller's face. Money language only: what happened,
// what it cost/saved, what to do next. NO probabilities, NO SHAP, NO
// model names — those live in the engineering console (the other demo).
//
// Visual identity: the existing console theme (bg-card, theme tokens).

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Store,
  ChevronDown,
  Truck,
  KeyRound,
  CheckCheck,
  Layers,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  feedStatus,
  orderOneLiner,
  type Phase,
  type StoryOrder,
} from "@/components/story/story-state";
import { CountUp, StatusPill } from "@/components/story/story-bits";
import {
  AMBIENT_MERCHANT_ORDERS,
  MERCHANT_SAVED_WEEK,
  plainReasons,
} from "@/lib/story/scenes";
import { cn } from "@/lib/utils";

const inr = (v: number) => `₹${v.toLocaleString("en-IN")}`;

export function MerchantPane({
  phase,
  orders,
  consent,
  onToggleConsent,
}: {
  phase: Phase;
  orders: StoryOrder[];
  consent: boolean;
  onToggleConsent: () => void;
}) {
  const [whyOpen, setWhyOpen] = React.useState<string | null>(null);
  const [shipped, setShipped] = React.useState<string[]>([]);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-border bg-card">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <Store className="size-4 text-muted-foreground" aria-hidden />
          <span className="text-sm font-semibold tracking-tight">UrbanKart</span>
          <span className="text-[10px] font-medium uppercase tracking-widest text-muted-foreground">
            Seller
          </span>
        </div>
        <Badge
          variant="outline"
          className="border-mint-500/30 bg-mint-500/10 text-[9px] font-semibold text-mint-700 dark:text-mint-500"
        >
          <span className="mr-1 inline-block size-1.5 animate-pulse rounded-full bg-mint-500" aria-hidden />
          Live
        </Badge>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3 [&::-webkit-scrollbar]:w-1.5">
        {/* Savings hero + protections, merged into one visual block */}
        <div className="rounded-lg border border-mint-500/25 bg-mint-500/[0.07] p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Saved from returns this week
          </p>
          <p className="mt-0.5 text-2xl font-bold text-mint-700 dark:text-mint-500">
            <CountUp value={MERCHANT_SAVED_WEEK} format={inr} />
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            12 orders stopped · 7 customers verified · 3 paid online instead
          </p>
          <div className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-mint-500/15 pt-2">
            {[
              "COD above ₹50,000 blocked",
              "New customers verify by OTP",
              "Repeat returners blocked",
              "Incomplete addresses flagged",
            ].map((t) => (
              <p
                key={t}
                className="flex items-center gap-1.5 text-[10px] leading-snug text-muted-foreground"
              >
                <CheckCheck
                  className="size-3 shrink-0 text-mint-600 dark:text-mint-500"
                  aria-hidden
                />
                {t}
              </p>
            ))}
          </div>
        </div>

        {/* Orders feed */}
        <div className="mt-3">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Today&apos;s orders
          </p>
          <div className="space-y-2">
            <AnimatePresence initial={false}>
              {orders.map((o) => (
                <StoryOrderCard
                  key={o.key}
                  order={o}
                  phase={phase}
                  whyOpen={whyOpen === o.key}
                  shipped={shipped.includes(o.key)}
                  onWhy={() => setWhyOpen(whyOpen === o.key ? null : o.key)}
                  onShip={() => setShipped((s) => [...s, o.key])}
                />
              ))}
            </AnimatePresence>
            {AMBIENT_MERCHANT_ORDERS.map((a) => (
              <AmbientCard key={a.orderId} order={a} />
            ))}
          </div>
        </div>

        {/* Consent card — pinned to the pane bottom when feed is short */}
        <div className="mt-auto min-h-0 pt-3">
          <div className="rounded-lg border border-border bg-muted/30 p-3">
          <div className="flex items-center gap-2">
            <Layers className="size-4 text-muted-foreground" aria-hidden />
            <p className="text-xs font-semibold">PayGrid — your payment platform</p>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {consent
              ? "Read-only audit access shared — they can verify, never edit."
              : "Requests read-only access to your audit trail — verify only, never edit."}
          </p>
          <Button
            size="sm"
            variant={consent ? "outline" : "default"}
            onClick={onToggleConsent}
            className={cn(
              "mt-2 h-8 w-full rounded-md text-xs font-semibold",
              !consent && "bg-brand-500 text-white hover:bg-brand-600",
            )}
          >
            <KeyRound className="mr-1.5 size-3.5" aria-hidden />
            {consent ? "Revoke audit access" : "Share audit access"}
          </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Story order card
// ---------------------------------------------------------------------------

function StoryOrderCard({
  order,
  phase,
  whyOpen,
  shipped,
  onWhy,
  onShip,
}: {
  order: StoryOrder;
  phase: Phase;
  whyOpen: boolean;
  shipped: boolean;
  onWhy: () => void;
  onShip: () => void;
}) {
  const status = feedStatus(order);
  const isRing = order.result.decision === "REJECT" && order.amount <= 6500;
  const reasons = plainReasons(order.result.explanation, order.amount);
  const short = order.orderId.split("-").pop();

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 300, damping: 24 }}
      className="rounded-lg border border-border bg-background p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <StatusPill status={status} />
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            #{short}
          </span>
        </div>
        <span className="shrink-0 text-xs font-bold tabular-nums">
          {inr(order.amount)}
        </span>
      </div>

      <p className="mt-1.5 text-xs text-foreground/80">
        {isRing ? "Same device — linked accounts" : orderOneLiner(order)}
      </p>

      {/* Why? expander */}
      <button
        type="button"
        onClick={onWhy}
        className="mt-1.5 flex items-center gap-1 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
        aria-expanded={whyOpen}
      >
        <ChevronDown
          className={cn("size-3.5 transition-transform", whyOpen && "rotate-180")}
          aria-hidden
        />
        Why?
      </button>

      <AnimatePresence initial={false}>
        {whyOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <ul className="mt-1 space-y-1 border-t border-border pt-2">
              {reasons.map((r, i) => (
                <li key={i} className="flex items-center gap-1.5 text-[11px]">
                  <span
                    className={cn(
                      "inline-block size-1.5 rounded-full",
                      r.tone === "risk" ? "bg-red-500/70" : "bg-mint-500",
                    )}
                    aria-hidden
                  />
                  <span className={r.tone === "risk" ? "text-foreground/85" : "text-muted-foreground"}>
                    {r.text}
                  </span>
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Action row — synced with the shopper pane */}
      <div className="mt-2 border-t border-border pt-2">
        {shipped ? (
          <p className="flex items-center gap-1.5 text-[11px] font-medium text-mint-700 dark:text-mint-500">
            <CheckCheck className="size-3.5" aria-hidden /> Shipped
          </p>
        ) : (
          <ActionRow order={order} phase={phase} onShip={onShip} />
        )}
      </div>
    </motion.div>
  );
}

function ActionRow({
  order,
  phase,
  onShip,
}: {
  order: StoryOrder;
  phase: Phase;
  onShip: () => void;
}) {
  const d = order.result.decision;
  const res = order.resolution;

  if (d === "ACCEPT" || res === "otp" || res === "prepaid" || res === "prepaid_otp") {
    return (
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium text-mint-700 dark:text-mint-500">
          {d === "ACCEPT"
            ? "Auto-approved — safe to ship"
            : res === "prepaid"
              ? "Customer paid online — no COD risk"
              : res === "prepaid_otp"
                ? "Customer paid online & verified"
                : "Customer verified — safe to ship"}
        </p>
        <Button
          size="sm"
          onClick={onShip}
          className="h-7 rounded-md bg-mint-500 px-3 text-[11px] font-semibold text-white hover:bg-mint-600"
        >
          <Truck className="mr-1 size-3" aria-hidden /> Ship
        </Button>
      </div>
    );
  }

  if (d === "REVIEW") {
    return (
      <p className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
        <span className="inline-block size-1.5 animate-pulse rounded-full bg-amber-500" aria-hidden />
        {phase === "otp_checking"
          ? "Customer is verifying…"
          : "Waiting for customer to verify"}
      </p>
    );
  }

  // REJECT paths
  if (phase === "prepaid_checking") {
    return (
      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="inline-block size-1.5 animate-pulse rounded-full bg-brand-500" aria-hidden />
        Customer is paying online…
      </p>
    );
  }
  return (
    <p className="text-[11px] font-medium text-red-600 dark:text-red-400">
      {res === "prepaid_otp"
        ? "Customer paid online — verifying"
        : "COD blocked · customer offered online payment"}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Ambient (pre-existing) order card — flatter, quieter
// ---------------------------------------------------------------------------

function AmbientCard({ order }: { order: (typeof AMBIENT_MERCHANT_ORDERS)[number] }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-2.5 opacity-75">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <StatusPill status={order.status} />
          <span className="font-mono text-[11px] text-muted-foreground">
            #{order.orderId}
          </span>
        </div>
        <span className="shrink-0 text-[11px] font-semibold tabular-nums text-muted-foreground">
          {inr(order.amount)}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">{order.line}</p>
    </div>
  );
}
