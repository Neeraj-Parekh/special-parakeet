"use client";

export const dynamic = "force-dynamic";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ShieldAlert,
  ListChecks,
  History,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { useApiKeys, buildAuthHeader } from "@/components/api-key-context";
import { MockModeBadge } from "@/components/app-header";
import {
  DecisionBadge,
  DecisionSourcePill,
  ScorePill,
} from "@/components/decision-badge";
import {
  DEMO_ORDERS,
  type CostBreakdown,
  type Decision,
  type OrderInput,
  type ReasonCode,
  type ScoreResponse,
} from "@/lib/mock-data";
import { formatINR } from "@/lib/format";
import { useRecentDecisions, type RecentDecision } from "@/lib/session-decisions";
import { ShapWaterfall } from "@/components/shap-waterfall";
import { RulesToggleCard } from "@/components/rules-toggle-card";
import { AgentConsole } from "@/components/agent-console";
import { NarrativePivotCard } from "@/components/narrative-pivot-card";
import { CostCurveSlider } from "@/components/cost-curve-slider";

// ----------------------------------------------------------------------------
// Recent-decision history lives in src/lib/session-decisions.ts — a
// sessionStorage-backed module store shared with the Dashboard metrics row
// and the Checkout demo, so a judge demo never loses its history.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
// Page
// ----------------------------------------------------------------------------

const EMPTY_ORDER: OrderInput = {
  order_id: "ORD-WEB-001",
  amount_inr: 12499,
  category: "Electronics",
  customer_id: "CUST-WEB-001",
  address_quality: "vague",
  city_tier: "tier_3",
  payment_method: "COD",
  prior_orders: 0,
  prior_returns: 0,
  items: 1,
  order_hour: 12,
  device: "Android App",
};

export default function RiskConsolePage() {
  const keys = useApiKeys();
  const [order, setOrder] = React.useState<OrderInput>(EMPTY_ORDER);
  const [result, setResult] = React.useState<ScoreResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [mock, setMock] = React.useState(false);
  const [recent, addRecent, clearRecent] = useRecentDecisions();

  // Fetch live rules on mount so the score button's "BLOCK rule fired"
  // branch reflects the user's current rule set (Track I demo moment #4).
  const rulesQuery = useQuery({
    queryKey: ["rules"],
    queryFn: async () => {
      const r = await fetch("/api/v1/rules", {
        headers: buildAuthHeader(keys, "scorer"),
      });
      const data = await r.json().catch(() => ({}));
      return { rules: data.rules || [], mock: r.headers.get("X-Mock-Mode") === "true" };
    },
    staleTime: 30_000,
  });

  async function score() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const idemKey = `${order.order_id}:${order.amount_inr}:${Date.now()}`;
      const r = await fetch("/api/risk/score", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...buildAuthHeader(keys, "scorer"),
          "Idempotency-Key": idemKey,
        },
        body: JSON.stringify(order),
      });
      const data = (await r.json().catch(() => null)) as ScoreResponse | null;
      if (!r.ok || !data) {
        const detail = (data as unknown as { detail?: string })?.detail || `HTTP ${r.status}`;
        setError(detail);
      } else {
        setResult(data);
        setMock(r.headers.get("X-Mock-Mode") === "true");
        addRecent({
          prediction_id: data.prediction_id || "—",
          order_id: order.order_id,
          amount_inr: order.amount_inr,
          payment_method: order.payment_method,
          decision: (data.decision || "REVIEW") as Decision,
          probability: data.probability,
          decision_source: data.decision_source,
          latency_ms: data.latency_ms ?? null,
          mock: r.headers.get("X-Mock-Mode") === "true",
          ts: Date.now(),
        });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  function loadDemo(d: (typeof DEMO_ORDERS)[number]) {
    setOrder({ ...d.order, order_id: d.order.order_id });
    setResult(null);
    setError(null);
  }

  return (
    <div className="space-y-6">
      <PageHeader />
      <div className="grid gap-6 lg:grid-cols-[420px_minmax(0,1fr)]">
        <div className="space-y-4">
          <OrderFormCard
            order={order}
            setOrder={setOrder}
            onScore={score}
            loading={loading}
          />
          <DemoOrdersCard onPick={loadDemo} />
        </div>
        <div className="space-y-6">
          <ResultCard
            result={result}
            loading={loading}
            error={error}
            mock={mock}
          />
          <NarrativePivotCard />
          <CostCurveSlider
            probability={result?.probability ?? null}
            mock={mock}
          />
          <RulesToggleCard
            order={order}
            lastDecision={result?.decision ?? null}
            lastProbability={result?.probability ?? null}
            onRescore={score}
          />
          <AgentConsole
            recentCount={recent.length}
            rulesCount={rulesQuery.data?.rules.length ?? 0}
          />
          <RecentDecisionsCard
            rows={recent}
            onClear={clearRecent}
          />
        </div>
      </div>
      {/* Hidden helper: keeps rulesQuery warm for the Rules Manager */}
      <span className="sr-only" aria-hidden>
        {rulesQuery.data?.rules.length ?? 0} rules active
      </span>
    </div>
  );
}

function PageHeader() {
  return (
    <div className="flex flex-col gap-1.5">
      <h1 className="text-2xl font-semibold tracking-tight">Risk Scoring</h1>
      <p className="max-w-2xl text-sm text-muted-foreground">
        Paste an order, click Score, and see the cost-optimal Bahnsen BMR verdict with a full
        explainability trail. The 3 demo orders cover all four decision layers — cost-optimal
        BMR, rules-engine BLOCK, REVIEW rule gate, and mandate breach.
      </p>
    </div>
  );
}

function OrderFormCard({
  order,
  setOrder,
  onScore,
  loading,
}: {
  order: OrderInput;
  setOrder: (next: OrderInput) => void;
  onScore: () => void;
  loading: boolean;
}) {
  const update = <K extends keyof OrderInput>(k: K, v: OrderInput[K]) =>
    setOrder({ ...order, [k]: v });
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <ListChecks className="size-4 text-brand-500" aria-hidden />
            Score a COD order
          </CardTitle>
          <Badge
            variant="outline"
            className="border-brand-500/25 bg-brand-500/10 text-[10px] font-semibold text-brand-600"
          >
            LIVE API
          </Badge>
        </div>
        <CardDescription>
          Every field maps 1:1 to <code className="font-mono text-xs">OrderIn</code> on{" "}
          <code className="font-mono text-xs">POST /risk/score</code>.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field label="Order ID">
          <Input
            value={order.order_id}
            onChange={(e) => update("order_id", e.target.value)}
            placeholder="ORD-WEB-001"
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Amount (INR)">
            <Input
              type="number"
              value={order.amount_inr}
              onChange={(e) => update("amount_inr", Number(e.target.value))}
            />
          </Field>
          <Field label="Category">
            <Select
              value={order.category}
              onValueChange={(v) => update("category", v)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Category" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Electronics">Electronics</SelectItem>
                <SelectItem value="Fashion">Fashion</SelectItem>
                <SelectItem value="Health">Health</SelectItem>
                <SelectItem value="Accessories">Accessories</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Payment method">
            <Select
              value={order.payment_method}
              onValueChange={(v) =>
                update("payment_method", v as OrderInput["payment_method"])
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Payment" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="COD">COD</SelectItem>
                <SelectItem value="Prepaid">Prepaid</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="Address quality">
            <Select
              value={order.address_quality}
              onValueChange={(v) =>
                update("address_quality", v as OrderInput["address_quality"])
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Address" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="complete">complete</SelectItem>
                <SelectItem value="partial">partial</SelectItem>
                <SelectItem value="vague">vague</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="City tier">
            <Select
              value={order.city_tier}
              onValueChange={(v) =>
                update("city_tier", v as OrderInput["city_tier"])
              }
            >
              <SelectTrigger>
                <SelectValue placeholder="Tier" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tier_1">tier_1 (metro)</SelectItem>
                <SelectItem value="tier_2">tier_2</SelectItem>
                <SelectItem value="tier_3">tier_3 (rural)</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="Customer ID">
            <Input
              value={order.customer_id}
              onChange={(e) => update("customer_id", e.target.value)}
              placeholder="CUST-XXX"
            />
          </Field>
        </div>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Prior orders">
            <Input
              type="number"
              value={order.prior_orders}
              onChange={(e) => update("prior_orders", Number(e.target.value))}
            />
          </Field>
          <Field label="Prior returns">
            <Input
              type="number"
              value={order.prior_returns}
              onChange={(e) => update("prior_returns", Number(e.target.value))}
            />
          </Field>
          <Field label="Items">
            <Input
              type="number"
              value={order.items}
              onChange={(e) => update("items", Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Order hour (0-23)">
            <Input
              type="number"
              min={0}
              max={23}
              value={order.order_hour}
              onChange={(e) => update("order_hour", Number(e.target.value))}
            />
          </Field>
          <Field label="Device">
            <Select
              value={order.device}
              onValueChange={(v) => update("device", v)}
            >
              <SelectTrigger>
                <SelectValue placeholder="Device" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="Android App">Android App</SelectItem>
                <SelectItem value="iOS App">iOS App</SelectItem>
                <SelectItem value="Web">Web</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <Button
          className="h-11 w-full font-semibold"
          onClick={onScore}
          disabled={loading || !order.order_id || !order.amount_inr}
        >
          {loading ? "Scoring…" : "Score order"}
        </Button>
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

function DemoOrdersCard({
  onPick,
}: {
  onPick: (d: (typeof DEMO_ORDERS)[number]) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">3 demo orders</CardTitle>
        <CardDescription>
          One-click auto-fill. The expected decision is shown for each — score
          to verify the live verdict matches.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {DEMO_ORDERS.map((d) => (
          <div
            key={d.order.order_id}
            className="flex items-center justify-between gap-3 rounded-md border border-border/70 bg-card/40 p-3"
          >
            <div className="space-y-0.5">
              <div className="text-sm font-medium">{d.label}</div>
              <div className="text-xs text-muted-foreground">{d.description}</div>
            </div>
            <div className="flex items-center gap-2">
              <DecisionBadge decision={d.expected} size="sm" />
              <Button size="sm" variant="secondary" onClick={() => onPick(d)}>
                Load
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function ResultCard({
  result,
  loading,
  error,
  mock,
}: {
  result: ScoreResponse | null;
  loading: boolean;
  error: string | null;
  mock: boolean;
}) {
  if (loading) {
    return (
      <Card>
        <CardContent className="space-y-3 p-6">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="rounded-md border border-danger/50 bg-danger/15 p-4 text-sm text-danger">
            <p className="font-semibold">Scoring failed</p>
            <p className="mt-1 text-danger/80">{error}</p>
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!result) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-muted-foreground">
            <ShieldAlert className="size-8 opacity-40" aria-hidden />
            <p className="text-sm">Score an order to see the verdict.</p>
          </div>
        </CardContent>
      </Card>
    );
  }
  const barClass =
    result.decision === "ACCEPT"
      ? "bar-accept"
      : result.decision === "REVIEW"
        ? "bar-review"
        : "bar-reject";
  return (
    <Card>
      <div className={`-mt-6 h-1.5 rounded-t-xl ${barClass}`} aria-hidden />
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="text-base">Verdict</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            {mock && <MockModeBadge mock={mock} />}
            <DecisionSourcePill source={result.decision_source} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-[auto_minmax(0,1fr)]">
          <DecisionSurface decision={result.decision} />
          <div className="flex flex-col gap-3">
            <ScorePill probability={result.probability} />
            <div className="text-xs text-muted-foreground">
              model: <span className="font-mono">{result.model_version}</span>{" "}
              · latency: <span className="font-mono">{result.latency_ms}ms</span>
              {result.rule_fired && (
                <>
                  {" "}
                  · rule fired:{" "}
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {result.rule_fired}
                  </Badge>
                </>
              )}
            </div>
            {result.case_id && (
              <div className="text-xs text-muted-foreground">
                Case opened:{" "}
                <code className="font-mono">{result.case_id}</code>
              </div>
            )}
            {result.audit_trail_url && (
              <div className="text-xs text-muted-foreground">
                Audit trail:{" "}
                <a
                  className="font-mono text-success hover:underline"
                  href={`/audit?id=${encodeURIComponent(result.audit_trail_url.replace("/audit/", ""))}`}
                >
                  {result.audit_trail_url}
                </a>
              </div>
            )}
          </div>
        </div>
        <ExplainabilityPanel
          decision={result.decision as Decision}
          probability={result.probability ?? undefined}
          reasons={result.explanation}
          orderSummary={summarizeOrder(result)}
          mock={mock}
          acceptT={result.gate_thresholds?.legacy_accept_t}
          rejectT={result.gate_thresholds?.legacy_reject_t}
        />
        {result.cost_breakdown && (
          <CostBreakdownTable breakdown={result.cost_breakdown} />
        )}
        {result.mandate && result.mandate.verdict !== "VALID" && (
          <MandateBreachBanner
            verdict={result.mandate.verdict}
            note={result.mandate.note}
            reason={result.mandate.verdict_reason}
            type={result.mandate.mandate_type}
            bh={result.mandate.bh_purpose_code}
          />
        )}
      </CardContent>
    </Card>
  );
}

function summarizeOrder(result: ScoreResponse): string {
  // Compose the "why" summary line the judge reads aloud — mirrors
  // demo moment #2: "73% risk because: COD + ₹12,400, new customer,
  // vague address in Tier-3 city".
  const flags: string[] = [];
  // Read off the reasons array's most-upvoted features + flag the
  // classic Indian e-commerce RTO drivers. We use the actual value
  // (not the feature name) so the summary reflects the order, not a
  // hardcoded "COD" label.
  const reasons = result.explanation || [];
  const reasonValue = (f: string): string | undefined =>
    reasons.find((r) => r.feature === f)?.value as string | undefined;
  const payment = reasonValue("payment_method");
  if (payment === "COD") flags.push("COD");
  else if (payment === "Prepaid") flags.push("Prepaid payment");
  const addr = reasonValue("address_quality");
  if (addr === "vague") flags.push("vague address");
  else if (addr === "partial") flags.push("partial address");
  const tier = reasonValue("city_tier");
  if (tier === "tier_3") flags.push("Tier-3 city");
  else if (tier === "tier_2") flags.push("Tier-2 city");
  const priorOrders = reasonValue("prior_orders");
  if (priorOrders === 0) flags.push("new customer");
  else if (Number(priorOrders) >= 10) flags.push("loyal customer");
  if (reasonValue("prior_returns") !== undefined && Number(reasonValue("prior_returns")) > 0) {
    flags.push("repeat returner");
  }
  if (reasonValue("amount_inr") !== undefined && Number(reasonValue("amount_inr")) > 10000) {
    flags.push("high-value order");
  }
  return flags.length ? flags.join(" + ") : "no dominant risk factor";
}

function DecisionSurface({ decision }: { decision: Decision | null }) {
  let cls = "border-border bg-muted/40 text-muted-foreground";
  if (decision === "ACCEPT") cls = "decision-accept";
  else if (decision === "REVIEW") cls = "decision-review";
  else if (decision === "REJECT") cls = "decision-reject";
  return (
    <div
      className={`flex h-11 min-w-36 shrink-0 items-center justify-center rounded-full border px-5 text-sm font-bold tracking-wide ${cls}`}
      aria-label={`Decision: ${decision || "—"}`}
    >
      {decision || "—"}
    </div>
  );
}

function ExplainabilityPanel({
  decision,
  probability,
  reasons,
  orderSummary,
  mock,
  acceptT,
  rejectT,
}: {
  decision: Decision;
  probability?: number;
  reasons: ReasonCode[];
  orderSummary: string;
  mock?: boolean;
  acceptT?: number;
  rejectT?: number;
}) {
  const pct = probability === undefined ? null : Math.round(probability * 100);
  return (
    <div className="rounded-md border border-border/70 bg-muted/30 p-4">
      <div className="mb-1 flex items-center gap-2">
        <h3 className="text-sm font-semibold">Explainability</h3>
        <Badge variant="outline" className="text-[10px]">
          reason-codes
        </Badge>
      </div>
      <p className="text-sm text-foreground/90">
        This order scored{" "}
        <span className="font-semibold">{pct === null ? "—" : `${pct}%`}</span>{" "}
        risk because: {orderSummary || "(no dominant factor)"}. Decision:{" "}
        <span className="font-semibold">{decision}</span>{" "}
        via Bahnsen Bayes Minimum Risk (ICMLA 2013).
      </p>
      {reasons.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {reasons.slice(0, 5).map((r, i) => (
            <li
              key={`${r.feature}-${i}`}
              className="flex items-center justify-between gap-3 text-xs"
            >
              <span className="font-mono text-muted-foreground">{r.feature}</span>
              <span className="font-mono text-foreground/80">
                {String(r.value)}
              </span>
              <span
                className={
                  r.direction === "up" || r.delta_prob >= 0
                    ? "font-mono text-danger"
                    : "font-mono text-success"
                }
              >
                {r.delta_prob >= 0 ? "+" : ""}
                {r.delta_prob.toFixed(3)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3">
        <ShapWaterfall
          reasons={reasons}
          probability={probability ?? null}
          acceptT={acceptT}
          rejectT={rejectT}
          mock={mock}
        />
      </div>
    </div>
  );
}

function CostBreakdownTable({ breakdown }: { breakdown: CostBreakdown }) {
  const rows = [
    { label: "ACCEPT (ship)", cost: breakdown.ACCEPT },
    { label: "REVIEW (OTP gate)", cost: breakdown.REVIEW },
    { label: "REJECT (block)", cost: breakdown.REJECT },
  ];
  const min = Math.min(...rows.map((r) => r.cost));
  return (
    <div className="rounded-lg border border-border/70 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Cost breakdown — Bahnsen Eq.1</h3>
        <span className="text-[10px] text-muted-foreground">
          c_fp=₹50 · c_fn=₹600 · c_otp=₹5 · c_block=₹1000
        </span>
      </div>
      <div className="grid grid-cols-3 gap-3">
        {rows.map((r) => {
          const isChosen = r.cost === min;
          return (
            <div
              key={r.label}
              className={`rounded-lg border p-3 text-center transition-colors duration-200 ease-brand ${
                isChosen
                  ? "border-brand-500/40 bg-brand-500/5"
                  : "border-border bg-muted/40"
              }`}
            >
              <p className="mb-1 text-xs text-muted-foreground">{r.label}</p>
              <p
                className={`font-mono text-base font-bold tabular-nums ${
                  isChosen ? "text-brand-600" : "text-foreground"
                }`}
              >
                {formatINR(r.cost)}
              </p>
              {isChosen && (
                <p className="mt-1 text-[10px] font-semibold uppercase tracking-wider text-brand-600">
                  cost-optimal
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MandateBreachBanner({
  verdict,
  note,
  reason,
  type,
  bh,
}: {
  verdict: string;
  note: string | null;
  reason: string | null;
  type: string | null;
  bh: string | null;
}) {
  return (
    <div className="rounded-md border border-danger/50 bg-danger/15 p-4 text-sm">
      <div className="mb-1 flex items-center gap-2">
        <span className="font-semibold text-danger">Mandate {verdict}</span>
        {type && (
          <Badge variant="outline" className="border-danger/40 text-danger">
            {type}
          </Badge>
        )}
        {bh && (
          <Badge variant="outline" className="border-danger/40 text-danger">
            BH {bh}
          </Badge>
        )}
      </div>
      <p className="text-xs text-danger/80">
        {note || reason || "Mandate header failed verification — short-circuit REJECT per Track D V3 §13."}
      </p>
    </div>
  );
}

function RecentDecisionsCard({
  rows,
  onClear,
}: {
  rows: RecentDecision[];
  onClear: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <History className="size-4 text-muted-foreground" aria-hidden />
            Recent decisions this session
          </CardTitle>
          {rows.length > 0 && (
            <Button size="sm" variant="ghost" onClick={onClear}>
              Clear
            </Button>
          )}
        </div>
        <CardDescription>
          Persisted to sessionStorage — survives tab reload, wiped when the
          tab closes. Mock-mode rows are flagged.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            No decisions yet — click <span className="font-medium">Score order</span> above.
          </div>
        ) : (
          <div className="max-h-72 overflow-y-auto rounded-md border border-border/60">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Order</TableHead>
                  <TableHead>Amount</TableHead>
                  <TableHead>P(RTO)</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead>Source</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.prediction_id + r.ts}>
                    <TableCell className="font-mono text-xs">
                      {r.order_id}
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums">
                      {formatINR(r.amount_inr)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {r.probability === null ? "—" : r.probability.toFixed(3)}
                    </TableCell>
                    <TableCell>
                      <DecisionBadge decision={r.decision} size="sm" />
                    </TableCell>
                    <TableCell className="text-[11px] text-muted-foreground">
                      {r.decision_source}
                      {r.mock && (
                        <span className="ml-1 text-warning">(mock)</span>
                      )}
                    </TableCell>
                    <TableCell className="text-[11px] text-muted-foreground">
                      {new Date(r.ts).toLocaleTimeString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
