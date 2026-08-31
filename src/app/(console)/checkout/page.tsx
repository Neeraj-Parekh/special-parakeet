"use client";

// CHECKOUT DEMO — the consumer face of the trust layer.
//
// Scenario picker → "Place order" → a 3-step inline scoring animation with a
// live latency badge → one of three endings:
//   ACCEPT  → order confirmed (test-checkout confirmation + mandate chips)
//   REVIEW  → merchant OTP verification modal (the grey-zone gate)
//   REJECT  → decline screen with risk drivers + audit receipt
// Every verdict also lands in the session store so the Dashboard picks it up.

import * as React from "react";
import Link from "next/link";
import {
  CheckCircle2,
  ShieldCheck,
  XCircle,
  Loader2,
  Check,
  ArrowRight,
  RotateCcw,
  KeyRound,
  FileText,
  Clock,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import { DEMO_ORDERS, type ScoreResponse } from "@/lib/mock-data";
import { pushRecent } from "@/lib/session-decisions";
import { formatINR } from "@/lib/format";
import { cn } from "@/lib/utils";

// ----------------------------------------------------------------------------

const SCORING_STEPS = [
  { label: "Verifying customer history", detail: "prior orders · returns · device" },
  { label: "Scoring address & risk signals", detail: "address quality · city tier · value" },
  { label: "Applying cost-optimal policy", detail: "Bahnsen BMR · expected ₹ cost" },
];

type Phase = "idle" | "scoring" | "otp" | "done";
type Ending = "accept" | "review-ok" | "reject" | null;

export default function CheckoutDemoPage() {
  const keys = useApiKeys();
  const [scenarioIdx, setScenarioIdx] = React.useState(0);
  const scenario = DEMO_ORDERS[scenarioIdx];
  const order = scenario.order;

  const [phase, setPhase] = React.useState<Phase>("idle");
  const [stepDone, setStepDone] = React.useState(0); // 0..3 completed steps
  const [result, setResult] = React.useState<ScoreResponse | null>(null);
  const [mock, setMock] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [elapsed, setElapsed] = React.useState(0); // live ms ticker
  const [finalMs, setFinalMs] = React.useState<number | null>(null);
  const [otp, setOtp] = React.useState("");
  const [otpVerifying, setOtpVerifying] = React.useState(false);
  const [ending, setEnding] = React.useState<Ending>(null);

  const startRef = React.useRef(0);
  const timerRef = React.useRef<ReturnType<typeof setInterval> | null>(null);

  function reset() {
    if (timerRef.current) clearInterval(timerRef.current);
    setPhase("idle");
    setStepDone(0);
    setResult(null);
    setError(null);
    setElapsed(0);
    setFinalMs(null);
    setOtp("");
    setOtpVerifying(false);
    setEnding(null);
  }

  function pickScenario(i: number) {
    reset();
    setScenarioIdx(i);
  }

  async function placeOrder() {
    setError(null);
    setResult(null);
    setEnding(null);
    setPhase("scoring");
    setStepDone(0);
    setFinalMs(null);
    startRef.current = Date.now();

    // Live latency ticker (60ms cadence — reads like a stopwatch, not a spinner)
    timerRef.current = setInterval(() => {
      setElapsed(Date.now() - startRef.current);
    }, 60);

    // Step animation (~650ms per step) runs in parallel with the real fetch.
    const steps = (async () => {
      for (let i = 1; i <= SCORING_STEPS.length; i++) {
        await sleep(650);
        setStepDone(i);
      }
    })();

    const fetchP = (async () => {
      const r = await fetch("/api/risk/score", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeader(keys, "scorer"),
          "Idempotency-Key": `${order.order_id}:checkout:${Date.now()}`,
        },
        body: JSON.stringify(order),
      });
      const data = (await r.json().catch(() => null)) as ScoreResponse | null;
      if (!r.ok || !data) {
        const detail = (data as unknown as { detail?: string })?.detail || `HTTP ${r.status}`;
        throw new Error(detail);
      }
      return { data, isMock: r.headers.get("X-Mock-Mode") === "true" };
    })();

    try {
      const [, fetched] = await Promise.all([steps, fetchP]);
      if (timerRef.current) clearInterval(timerRef.current);
      setResult(fetched.data);
      setMock(fetched.isMock);
      setFinalMs(fetched.data.latency_ms ?? Date.now() - startRef.current);

      pushRecent({
        prediction_id: fetched.data.prediction_id || "—",
        order_id: order.order_id,
        amount_inr: order.amount_inr,
        payment_method: order.payment_method,
        decision: fetched.data.decision || "REVIEW",
        probability: fetched.data.probability,
        decision_source: fetched.data.decision_source,
        latency_ms: fetched.data.latency_ms ?? null,
        mock: fetched.isMock,
        ts: Date.now(),
      });

      if (fetched.data.decision === "REVIEW") {
        setPhase("otp");
      } else {
        setEnding(fetched.data.decision === "ACCEPT" ? "accept" : "reject");
        setPhase("done");
      }
    } catch (e) {
      if (timerRef.current) clearInterval(timerRef.current);
      setError(String((e as Error).message ?? e));
      setPhase("idle");
    }
  }

  async function verifyOtp() {
    setOtpVerifying(true);
    await sleep(900);
    setOtpVerifying(false);
    setEnding("review-ok");
    setPhase("done");
  }

  return (
    <div className="space-y-6">
      <PageHeader onReset={phase !== "idle" ? reset : undefined} />

      <div className="grid gap-6 lg:grid-cols-[400px_minmax(0,1fr)]">
        {/* LEFT — the consumer order card */}
        <div className="space-y-4">
          <OrderCard scenarioIdx={scenarioIdx} onPick={pickScenario} />
          {phase === "idle" && (
            <Button
              className="h-12 w-full rounded-lg text-base font-semibold"
              onClick={placeOrder}
              disabled={!order.order_id || !order.amount_inr}
            >
              Place order · {formatINR(order.amount_inr)}
            </Button>
          )}
          {error && (
            <div className="rounded-lg border border-signal-red/30 bg-signal-red/5 p-4 text-sm text-danger">
              <p className="font-semibold">Checkout failed</p>
              <p className="mt-1 text-xs">{error}</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={reset}>
                Try again
              </Button>
            </div>
          )}
        </div>

        {/* RIGHT — the gate panel */}
        <div className="space-y-6">
          {phase === "idle" && <IdlePanel />}
          {phase === "scoring" && (
            <ScoringPanel
              stepDone={stepDone}
              elapsed={elapsed}
              expected={scenario.expected}
            />
          )}
          {phase === "otp" && result && (
            <OtpPanel
              result={result}
              otp={otp}
              setOtp={setOtp}
              verifying={otpVerifying}
              onVerify={verifyOtp}
            />
          )}
          {phase === "done" && result && ending && (
            <VerdictPanel result={result} ending={ending} mock={mock} />
          )}

          {/* Mandate section — whenever the verdict carries mandate state */}
          {result?.mandate && phase === "done" && (
            <MandateCard mandate={result.mandate} />
          )}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Pieces
// ----------------------------------------------------------------------------

function PageHeader({ onReset }: { onReset?: () => void }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Checkout Demo</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          The consumer face of the trust layer — pick a scenario, place the order, and watch
          the 3-step gate run live. REVIEW orders stop at an OTP step instead of a blunt decline.
        </p>
      </div>
      {onReset && (
        <Button variant="outline" size="sm" onClick={onReset} className="shrink-0">
          <RotateCcw className="mr-1.5 size-3.5" aria-hidden />
          Run another order
        </Button>
      )}
    </div>
  );
}

function sleep(ms: number) {
  return new Promise((res) => setTimeout(res, ms));
}

/** The consumer order summary — looks like a checkout, not a form. */
function OrderCard({
  scenarioIdx,
  onPick,
}: {
  scenarioIdx: number;
  onPick: (i: number) => void;
}) {
  const scenario = DEMO_ORDERS[scenarioIdx];
  const order = scenario.order;
  return (
    <Card className="shadow-card">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">Your order</CardTitle>
          <Badge variant="outline" className="text-[10px] font-medium text-muted-foreground">
            Demo Store · checkout
          </Badge>
        </div>
        <CardDescription>Pick one of the 3 demo scenarios.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Scenario picker */}
        <div className="grid gap-2">
          {DEMO_ORDERS.map((d, i) => (
            <button
              key={d.order.order_id}
              type="button"
              onClick={() => onPick(i)}
              className={cn(
                "flex items-center justify-between gap-3 rounded-lg border p-3 text-left transition-all duration-200 ease-brand focus-visible:outline-2 focus-visible:outline-ring",
                i === scenarioIdx
                  ? "border-brand-500/40 bg-brand-500/5"
                  : "border-border bg-card hover:border-brand-500/30 hover:bg-muted/50",
              )}
              aria-pressed={i === scenarioIdx}
            >
              <div>
                <div className="text-sm font-semibold text-foreground">{d.label}</div>
                <div className="text-xs text-muted-foreground">{d.description}</div>
              </div>
              {i === scenarioIdx && (
                <span
                  className="flex size-5 shrink-0 items-center justify-center rounded-full bg-brand-500 text-white"
                  aria-hidden
                >
                  <Check className="size-3" />
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="border-t border-border pt-4">
          <dl className="space-y-2 text-sm">
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted-foreground">Order</dt>
              <dd className="font-mono text-xs">{order.order_id}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted-foreground">Category · items</dt>
              <dd className="text-xs">
                {order.category} · {order.items}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted-foreground">Payment</dt>
              <dd className="text-xs">{order.payment_method}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted-foreground">Deliver to</dt>
              <dd className="text-xs">
                {order.city_tier.replace("_", " ")} · {order.address_quality} address
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-t border-border pt-2">
              <dt className="font-semibold text-foreground">Total</dt>
              <dd className="font-mono text-base font-bold tabular-nums text-foreground">
                {formatINR(order.amount_inr)}
              </dd>
            </div>
          </dl>
        </div>
      </CardContent>
    </Card>
  );
}

function IdlePanel() {
  return (
    <Card className="shadow-card">
      <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <div className="flex size-12 items-center justify-center rounded-full bg-brand-500/10 text-brand-500">
          <ShieldCheck className="size-6" aria-hidden />
        </div>
        <p className="text-sm font-medium text-foreground">
          RTO Trust Layer will score this order at placement
        </p>
        <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
          The gate runs in-line at checkout — the shopper sees a verification step, never a
          blank decline. Merchants see the verdict, the cost math, and the audit receipt.
        </p>
      </CardContent>
    </Card>
  );
}

function ScoringPanel({
  stepDone,
  elapsed,
  expected,
}: {
  stepDone: number;
  elapsed: number;
  expected: string;
}) {
  return (
    <Card className="shadow-card">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Loader2 className="size-4 animate-spin text-brand-500" aria-hidden />
            Scoring your order…
          </CardTitle>
          {/* Latency badge — live ticker */}
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-500/30 bg-brand-500/10 px-2.5 py-1 font-mono text-xs tabular-nums text-brand-600">
            <Clock className="size-3" aria-hidden />
            {elapsed} ms
          </span>
        </div>
        <CardDescription>
          Running the pre-dispatch gate — expected outcome for this scenario:{" "}
          <span className="font-semibold">{expected}</span>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {SCORING_STEPS.map((s, i) => {
          const done = stepDone > i;
          const active = stepDone === i;
          return (
            <div
              key={s.label}
              className={cn(
                "flex items-center gap-3 rounded-lg border p-3 transition-colors duration-200 ease-brand",
                done
                  ? "border-mint-500/30 bg-mint-500/5"
                  : active
                    ? "border-brand-500/30 bg-brand-500/5"
                    : "border-border bg-muted/30",
              )}
            >
              <span
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-full",
                  done
                    ? "bg-mint-500 text-white"
                    : active
                      ? "bg-brand-500 text-white"
                      : "bg-muted text-muted-foreground",
                )}
                aria-hidden
              >
                {done ? (
                  <Check className="size-3.5" />
                ) : active ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <span className="text-[10px] font-bold">{i + 1}</span>
                )}
              </span>
              <div className="min-w-0">
                <p
                  className={cn(
                    "text-sm font-medium",
                    done || active ? "text-foreground" : "text-muted-foreground",
                  )}
                >
                  {s.label}
                </p>
                <p className="font-mono text-[11px] text-muted-foreground">{s.detail}</p>
              </div>
            </div>
          );
        })}
        <div className="pt-2">
          <Skeleton className="h-2 w-full" />
        </div>
      </CardContent>
    </Card>
  );
}

/** REVIEW → the grey-zone OTP gate (merchant verification step). */
function OtpPanel({
  result,
  otp,
  setOtp,
  verifying,
  onVerify,
}: {
  result: ScoreResponse;
  otp: string;
  setOtp: (v: string) => void;
  verifying: boolean;
  onVerify: () => void;
}) {
  return (
    <Card className="shadow-card">
      <div className="h-1.5 rounded-t-xl bar-review" aria-hidden />
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="size-4 text-warning" aria-hidden />
          One more step — verify it&rsquo;s you
        </CardTitle>
        <CardDescription>
          This order scored <span className="font-semibold">{pct(result.probability)}% RTO risk</span>{" "}
          — high enough to gate, not high enough to decline. Enter the OTP sent to the
          shopper&rsquo;s number to proceed.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col items-center gap-3 rounded-lg border border-gold-500/30 bg-gold-500/5 p-6">
          <Input
            value={otp}
            onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric"
            placeholder="••••••"
            aria-label="6-digit OTP"
            className="h-12 w-40 text-center font-mono text-xl tracking-[0.5em]"
            autoFocus
          />
          <p className="text-xs text-muted-foreground">
            Demo OTP — any 6 digits verify. A case {result.case_id ? (
              <span className="font-mono">{result.case_id}</span>
            ) : (
              "is opened"
            )}{" "}
            for the merchant queue.
          </p>
          <Button
            className="h-11 rounded-lg px-8 font-semibold"
            onClick={onVerify}
            disabled={otp.length !== 6 || verifying}
          >
            {verifying ? (
              <>
                <Loader2 className="mr-2 size-4 animate-spin" aria-hidden /> Verifying…
              </>
            ) : (
              "Verify OTP"
            )}
          </Button>
        </div>
        <p className="text-center text-xs text-muted-foreground">
          OTP conversion ≈ 82% (c_otp=₹5) — gating costs less than a false decline.
        </p>
      </CardContent>
    </Card>
  );
}

function VerdictPanel({
  result,
  ending,
  mock,
}: {
  result: ScoreResponse;
  ending: Exclude<Ending, null>;
  mock: boolean;
}) {
  if (ending === "accept") {
    return (
      <Card className="shadow-card">
        <div className="h-1.5 rounded-t-xl bar-accept" aria-hidden />
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <CheckCircle2 className="size-5 text-mint-500" aria-hidden />
              Order confirmed
            </CardTitle>
            {mock && <MockModeBadge mock={mock} />}
          </div>
          <CardDescription>
            Low return risk — dispatch approved. The courier leaves with confidence.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <ReceiptChip label="Order" value={result.prediction_id || "—"} />
            <ReceiptChip label="Risk score" value={`${pct(result.probability)}%`} />
            <ReceiptChip label="Decision in" value={`${result.latency_ms ?? "—"} ms`} />
          </div>
          {result.mandate && result.mandate.verdict === "VALID" && (
            <div className="rounded-lg border border-mint-500/30 bg-mint-500/5 p-3 text-xs text-mint-700">
              UPI Circle mandate ACTIVE — return value fenced if the parcel bounces.
            </div>
          )}
          <AuditLink result={result} />
        </CardContent>
      </Card>
    );
  }

  if (ending === "review-ok") {
    return (
      <Card className="shadow-card">
        <div className="h-1.5 rounded-t-xl bar-accept" aria-hidden />
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <CheckCircle2 className="size-5 text-mint-500" aria-hidden />
            Verified — order proceeding
          </CardTitle>
          <CardDescription>
            The OTP gate converted a would-be decline into a verified dispatch.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <ReceiptChip label="Order" value={result.prediction_id || "—"} />
            <ReceiptChip label="Risk score" value={`${pct(result.probability)}%`} />
            <ReceiptChip label="Case" value={result.case_id || "opened"} />
          </div>
          <AuditLink result={result} />
        </CardContent>
      </Card>
    );
  }

  // reject
  const drivers = (result.explanation || []).slice(0, 3);
  return (
    <Card className="shadow-card">
      <div className="h-1.5 rounded-t-xl bar-reject" aria-hidden />
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <XCircle className="size-5 text-signal-red" aria-hidden />
            Order declined — high return risk
          </CardTitle>
          {mock && <MockModeBadge mock={mock} />}
        </div>
        <CardDescription>
          Blocking this COD order is cheaper than shipping it: expected cost of shipping is
          higher than the cost of the block. The shopper keeps full pre-paid options.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Top risk drivers
          </p>
          {drivers.map((d, i) => (
            <div
              key={`${d.feature}-${i}`}
              className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2"
            >
              <span className="font-mono text-xs text-muted-foreground">{d.feature}</span>
              <span className="font-mono text-xs text-foreground">{String(d.value)}</span>
              <span className="font-mono text-xs tabular-nums text-danger">
                {d.delta_prob >= 0 ? "+" : ""}
                {d.delta_prob.toFixed(3)}
              </span>
            </div>
          ))}
        </div>
        <AuditLink result={result} />
      </CardContent>
    </Card>
  );
}

function ReceiptChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-center">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-0.5 truncate font-mono text-sm font-semibold tabular-nums text-foreground">
        {value}
      </p>
    </div>
  );
}

function AuditLink({ result }: { result: ScoreResponse }) {
  if (!result.audit_trail_url) return null;
  const id = result.audit_trail_url.replace("/audit/", "");
  return (
    <Link
      href={`/audit?id=${encodeURIComponent(id)}`}
      className="inline-flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:underline"
    >
      <FileText className="size-3.5" aria-hidden />
      View audit receipt
      <ArrowRight className="size-3" aria-hidden />
    </Link>
  );
}

/** Mandate state stepper: ACTIVE → COOLING → REVOKED. */
function MandateCard({
  mandate,
}: {
  mandate: NonNullable<ScoreResponse["mandate"]>;
}) {
  const v = String(mandate.verdict || "").toUpperCase();
  const state: "ACTIVE" | "COOLING" | "REVOKED" =
    v === "VALID" || v === "ACTIVE"
      ? "ACTIVE"
      : v.includes("COOLING") || v.includes("REVIEW")
        ? "COOLING"
        : "REVOKED";

  const steps: { key: string; label: string; hint: string }[] = [
    { key: "ACTIVE", label: "Active", hint: "mandate live · value fenced" },
    { key: "COOLING", label: "Cooling-off", hint: "OC-201B 24h window" },
    { key: "REVOKED", label: "Revoked", hint: "fence lifted" },
  ];
  const activeIdx = steps.findIndex((s) => s.key === state);

  return (
    <Card className="shadow-card">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">UPI Circle mandate</CardTitle>
          <div className="flex gap-2">
            {mandate.mandate_type && (
              <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">
                {mandate.mandate_type}
              </Badge>
            )}
            {mandate.bh_purpose_code && (
              <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground">
                BH {mandate.bh_purpose_code}
              </Badge>
            )}
          </div>
        </div>
        <CardDescription>
          OC-201B mandate state for this order — the fence on return value.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3">
          {steps.map((s, i) => {
            const isCurrent = i === activeIdx;
            const isPast = activeIdx > i;
            return (
              <li key={s.key} className="flex items-center gap-3">
                <span
                  className={cn(
                    "flex size-7 shrink-0 items-center justify-center rounded-full border font-mono text-[10px] font-bold",
                    isCurrent
                      ? s.key === "ACTIVE"
                        ? "border-mint-500 bg-mint-500 text-white"
                        : s.key === "COOLING"
                          ? "border-gold-500 bg-gold-500 text-white"
                          : "border-signal-red bg-signal-red text-white"
                      : isPast
                        ? "border-border bg-muted text-muted-foreground"
                        : "border-border-dashed bg-card text-muted-foreground/50",
                  )}
                  aria-hidden
                >
                  {i + 1}
                </span>
                <div
                  className={cn(
                    "flex-1 rounded-lg border p-2.5",
                    isCurrent ? "border-border bg-muted/50" : "border-transparent",
                  )}
                >
                  <p
                    className={cn(
                      "text-sm font-medium",
                      isCurrent ? "text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {s.label}
                    {isCurrent && (
                      <Badge variant="outline" className="ml-2 px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wider">
                        current
                      </Badge>
                    )}
                  </p>
                  <p className="text-[11px] text-muted-foreground">{s.hint}</p>
                </div>
              </li>
            );
          })}
        </ol>
        {(mandate.note || mandate.verdict_reason) && (
          <p className="mt-3 border-l-2 border-warning pl-3 text-xs text-muted-foreground">
            {mandate.note || mandate.verdict_reason}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function pct(p: number | null | undefined): string {
  if (p === null || p === undefined) return "—";
  return String(Math.round(p * 100));
}
