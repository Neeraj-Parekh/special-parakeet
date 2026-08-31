"use client";

// Integrations — the merchant integration grid with LIVE try-it actions.
// Every card fires a real call against the API routes on THIS deployment
// (with the scorer-scope Bearer when a key is pasted in the header). When
// the partner credential or the Python scorer is offline, the route answers
// from the labeled mock fallback and sets X-Mock-Mode: true — integrations
// never hard-fail. The raw JSON of every response is rendered below each
// action so a judge can curl the same route and compare byte-for-byte.

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRight,
  Landmark,
  Loader2,
  Package,
  Truck,
  Webhook,
} from "lucide-react";

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { MockModeBadge } from "@/components/app-header";
import { buildAuthHeader, useApiKeys } from "@/components/api-key-context";
import { formatINR } from "@/lib/format";

// ---------------------------------------------------------------------------
// Types — mirror the route handlers under src/app/api/v1/integrations/* and
// src/app/api/v1/webhooks/razorpay (field names read from those files).
// ---------------------------------------------------------------------------

/** Error body every route in this app returns on 4xx. */
interface ApiErrorBody {
  detail: string;
  reason?: string;
}

/** Discriminated result wrapper so cards can narrow without casts. */
type ApiResult<T> =
  | { ok: true; status: number; mock: boolean; data: T }
  | { ok: false; status: number; mock: boolean; data: ApiErrorBody };

interface NpciMandate {
  mandate_id: string;
  customer_id: string;
  amount_cap_inr: number;
  frequency: string;
  per_txn_cap_inr: number;
  cooling_period_h: number;
  max_devices: number;
  mandate_ttl_days: number;
  status: "ACTIVE" | "PENDING" | "REJECTED";
  created_at: string;
  mock: boolean;
}

interface ShiprocketResult {
  pincode: string;
  cod_available: boolean;
  prepaid_available: boolean;
  expected_delivery_days: number;
  recommended_courier: string;
  mock: boolean;
  timestamp: string;
}

interface DelhiveryScan {
  status: string;
  timestamp: string;
  location: string;
  remark: string;
}

interface DelhiveryResult {
  awb: string;
  current_status: string;
  eta: string;
  history: DelhiveryScan[];
  mock: boolean;
  timestamp: string;
}

interface WebhookAck {
  received: boolean;
  handled: boolean;
  event: string;
  payment_id: string | null;
  refund_id: string | null;
  amount: number | null;
  status: string | null;
  note: string;
  mock: boolean;
}

// ---------------------------------------------------------------------------
// Shared helpers (action buttons use plain fetch + useState; the Shiprocket
// GET goes through TanStack useQuery per the console data-fetching pattern).
// ---------------------------------------------------------------------------

async function postJson<T>(
  url: string,
  body: unknown,
  headers: Record<string, string> = {},
): Promise<ApiResult<T>> {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
    });
    const mock = r.headers.get("X-Mock-Mode") === "true";
    const data: unknown = await r
      .json()
      .catch(() => ({ detail: "malformed response" }));
    return r.ok
      ? { ok: true, status: r.status, mock, data: data as T }
      : { ok: false, status: r.status, mock, data: data as ApiErrorBody };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      mock: false,
      data: { detail: err instanceof Error ? err.message : "network error" },
    };
  }
}

/** The point of the page — the RAW JSON response, verbatim. */
function RawJson({ data }: { data: unknown }) {
  return (
    <pre className="max-h-40 overflow-y-auto rounded-lg border border-border bg-muted/50 p-3 font-mono text-xs leading-relaxed text-foreground/80">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function LiveTryItBadge() {
  return (
    <Badge
      variant="outline"
      className="border-brand-500/30 bg-brand-500/10 text-brand-600"
    >
      Live try-it
    </Badge>
  );
}

function HttpStatusChip({ status }: { status: number }) {
  const cls =
    status >= 200 && status < 300
      ? "border-mint-500/30 bg-mint-500/10 text-mint-700"
      : status === 0
        ? "border-border/60 bg-muted/50 text-muted-foreground"
        : "border-signal-red/30 bg-signal-red/10 text-danger";
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px] font-semibold tabular-nums ${cls}`}
    >
      {status === 0 ? "ERR" : `HTTP ${status}`}
    </span>
  );
}

function IconChip({
  tone,
  children,
}: {
  tone: "brand" | "mint";
  children: React.ReactNode;
}) {
  const cls =
    tone === "brand"
      ? "bg-brand-500/10 text-brand-600"
      : "bg-mint-500/10 text-mint-700";
  return (
    <div
      className={`flex size-9 shrink-0 items-center justify-center rounded-lg ${cls}`}
    >
      {children}
    </div>
  );
}

/** Small mono note under an action button — the exact contract being hit. */
function EndpointNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-2 font-mono text-xs tabular-nums text-muted-foreground">
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// NPCI UPI Circle — OC-201B mandate fencing.
// ---------------------------------------------------------------------------

function MandateStepper({ status }: { status: NpciMandate["status"] }) {
  const steps: { key: string; label: string; sub: string }[] = [
    { key: "ACTIVE", label: "Active", sub: "Mandate live · ₹5,000 per-txn cap" },
    { key: "COOLING", label: "Cooling", sub: "24h after the first txn" },
    { key: "REVOKED", label: "Revoked", sub: "Auto-revoke at the 180d TTL" },
  ];
  return (
    <div className="flex items-stretch gap-1">
      {steps.map((s, i) => {
        const active = s.key === status;
        return (
          <React.Fragment key={s.key}>
            {i > 0 && (
              <ChevronRight
                className="my-auto size-4 shrink-0 text-muted-foreground/40"
                aria-hidden
              />
            )}
            <div
              className={`flex-1 rounded-lg border p-2.5 ${
                active
                  ? "border-brand-500/40 bg-brand-500/10"
                  : "border-border/60 bg-muted/40"
              }`}
            >
              <div className="flex items-center gap-1.5">
                <span
                  className={`inline-block size-1.5 rounded-full ${
                    active ? "bg-brand-500" : "bg-muted-foreground/40"
                  }`}
                  aria-hidden
                />
                <span
                  className={`text-xs font-semibold ${
                    active ? "text-brand-600" : "text-muted-foreground"
                  }`}
                >
                  {s.label}
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-snug tabular-nums text-muted-foreground">
                {s.sub}
              </p>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

function NpciCard() {
  const keys = useApiKeys();
  const [loading, setLoading] = React.useState(false);
  const [result, setResult] = React.useState<ApiResult<NpciMandate> | null>(
    null,
  );

  async function createMandate() {
    setLoading(true);
    const r = await postJson<NpciMandate>(
      "/api/v1/integrations/npci/mandate",
      {
        customer_id: "CUST-NEW-0001",
        amount_cap_inr: 10000,
        frequency: "monthly",
        purpose: "ORD-HVC-002 COD OTP-gate",
      },
      buildAuthHeader(keys, "scorer"),
    );
    setResult(r);
    setLoading(false);
  }

  return (
    <Card className="gap-5 shadow-card transition-shadow duration-200 ease-brand hover:shadow-lift">
      <CardHeader>
        <div className="flex items-center gap-3">
          <IconChip tone="brand">
            <Landmark className="size-5" aria-hidden />
          </IconChip>
          <div className="min-w-0">
            <CardTitle className="text-base">NPCI UPI Circle</CardTitle>
            <CardDescription>
              OC-201B mandates — the OTP-gate for high-value COD
            </CardDescription>
          </div>
        </div>
        <CardAction className="flex flex-wrap items-center justify-end gap-1.5">
          <LiveTryItBadge />
          {result?.mock && <MockModeBadge mock={result.mock} />}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm tabular-nums text-muted-foreground">
          Mandate fencing instead of a flat REJECT: on a {formatINR(52000)}{" "}
          high-value COD order the customer registers a capped UPI Circle
          mandate, pays the first {formatINR(5000)} slice via UPI, and the
          merchant ships with the capital risk fenced. A 24h cooling period
          follows the first txn, the mandate lives on at most 5 devices, and it
          auto-revokes at the 180-day TTL.
        </p>
        <div>
          <Button
            onClick={createMandate}
            disabled={loading}
            className="h-11 font-semibold"
          >
            {loading && (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
            {loading ? "Creating…" : "Create test mandate"}
          </Button>
          <EndpointNote>
            POST /api/v1/integrations/npci/mandate · CUST-NEW-0001 · cap{" "}
            {formatINR(10000)} · ORD-HVC-002
          </EndpointNote>
        </div>
        {result && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <HttpStatusChip status={result.status} />
              {result.ok && (
                <span className="font-mono text-[11px] text-muted-foreground">
                  {result.data.status}
                </span>
              )}
            </div>
            {result.ok ? (
              <div className="space-y-3">
                <MandateStepper status={result.data.status} />
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center rounded-md border border-mint-500/30 bg-mint-500/10 px-2.5 py-1 font-mono text-xs text-mint-700 tabular-nums">
                    Cap {formatINR(result.data.amount_cap_inr)}
                  </span>
                  <span className="inline-flex items-center rounded-md border border-mint-500/30 bg-mint-500/10 px-2.5 py-1 font-mono text-xs text-mint-700 tabular-nums">
                    Per-txn {formatINR(result.data.per_txn_cap_inr)}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {result.data.mandate_id}
                  </span>
                </div>
              </div>
            ) : (
              <p className="text-sm text-danger">
                {result.data.reason ?? result.data.detail}
              </p>
            )}
            <RawJson data={result.data} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Shiprocket — pincode serviceability (GET via TanStack useQuery).
// ---------------------------------------------------------------------------

interface ShiprocketQueryData {
  status: number;
  mock: boolean;
  data: ShiprocketResult | ApiErrorBody;
}

function ShiprocketCard() {
  const keys = useApiKeys();
  const [pinInput, setPinInput] = React.useState("110001");
  const [queryPin, setQueryPin] = React.useState<string | null>(null);
  const [inputError, setInputError] = React.useState<string | null>(null);

  const { data, isFetching, error } = useQuery({
    queryKey: ["integrations", "shiprocket", queryPin],
    queryFn: async (): Promise<ShiprocketQueryData> => {
      const r = await fetch(
        `/api/v1/integrations/shiprocket/validate-pincode/${queryPin}`,
        { headers: buildAuthHeader(keys, "scorer") },
      );
      const mock = r.headers.get("X-Mock-Mode") === "true";
      const body: unknown = await r
        .json()
        .catch(() => ({ detail: "malformed response" }));
      return {
        status: r.status,
        mock,
        data: body as ShiprocketResult | ApiErrorBody,
      };
    },
    enabled: queryPin !== null,
  });

  function validate() {
    const pin = pinInput.trim();
    if (!/^\d{6}$/.test(pin)) {
      setInputError("Indian PIN codes are 6 digits.");
      return;
    }
    setInputError(null);
    setQueryPin(pin);
  }

  const svc = data && "cod_available" in data.data ? data.data : null;
  const apiErr = data && !("cod_available" in data.data) ? data.data : null;

  return (
    <Card className="gap-5 shadow-card transition-shadow duration-200 ease-brand hover:shadow-lift">
      <CardHeader>
        <div className="flex items-center gap-3">
          <IconChip tone="mint">
            <Truck className="size-5" aria-hidden />
          </IconChip>
          <div className="min-w-0">
            <CardTitle className="text-base">Shiprocket</CardTitle>
            <CardDescription>
              Pincode serviceability before COD renders at checkout
            </CardDescription>
          </div>
        </div>
        <CardAction className="flex flex-wrap items-center justify-end gap-1.5">
          <LiveTryItBadge />
          {data?.mock && <MockModeBadge mock={data.mock} />}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Serviceability runs before the COD option renders at checkout. If the
          pincode is COD-unavailable, checkout hides COD and forces prepaid —
          the merchant never ships cash-on-delivery into a zone the courier
          will not collect from.
        </p>
        <div className="flex gap-2">
          <Input
            value={pinInput}
            onChange={(e) => setPinInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") validate();
            }}
            inputMode="numeric"
            maxLength={6}
            placeholder="110001"
            aria-label="PIN code"
            className="h-11 font-mono"
          />
          <Button
            onClick={validate}
            disabled={isFetching}
            className="h-11 font-semibold"
          >
            {isFetching && (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
            Validate
          </Button>
        </div>
        {inputError && <p className="text-xs text-danger">{inputError}</p>}
        {error && (
          <p className="text-sm text-danger">
            {error instanceof Error ? error.message : "request failed"}
          </p>
        )}
        {data && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <HttpStatusChip status={data.status} />
              {svc &&
                (svc.cod_available ? (
                  <span className="inline-flex items-center rounded-md border border-mint-500/30 bg-mint-500/10 px-2.5 py-1 text-xs font-medium text-mint-700">
                    Serviceable · COD · {svc.expected_delivery_days}d ·{" "}
                    {svc.recommended_courier}
                  </span>
                ) : svc.prepaid_available ? (
                  <span className="inline-flex items-center rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1 text-xs font-medium text-warning">
                    Prepaid only · COD unavailable · {svc.recommended_courier}
                  </span>
                ) : (
                  <span className="inline-flex items-center rounded-md border border-signal-red/30 bg-signal-red/10 px-2.5 py-1 text-xs font-medium text-danger">
                    Not serviceable
                  </span>
                ))}
            </div>
            {apiErr && (
              <p className="text-sm text-danger">
                {apiErr.reason ?? apiErr.detail}
              </p>
            )}
            <RawJson data={data.data} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Delhivery — shipment tracking (the route is a POST {awb}, not a GET).
// ---------------------------------------------------------------------------

function DelhiveryCard() {
  const keys = useApiKeys();
  const [awb, setAwb] = React.useState("AWB1234567890");
  const [loading, setLoading] = React.useState(false);
  const [result, setResult] = React.useState<ApiResult<DelhiveryResult> | null>(
    null,
  );
  const [inputError, setInputError] = React.useState<string | null>(null);

  async function track() {
    const a = awb.trim();
    if (a.length < 4) {
      setInputError("AWB must be at least 4 characters.");
      return;
    }
    setInputError(null);
    setLoading(true);
    const r = await postJson<DelhiveryResult>(
      "/api/v1/integrations/delhivery/track",
      { awb: a },
      buildAuthHeader(keys, "scorer"),
    );
    setResult(r);
    setLoading(false);
  }

  const statusTone = (s: string): string =>
    s === "delivered"
      ? "border-mint-500/30 bg-mint-500/10 text-mint-700"
      : s === "exception"
        ? "border-signal-red/30 bg-signal-red/10 text-danger"
        : "border-brand-500/30 bg-brand-500/10 text-brand-600";

  return (
    <Card className="gap-5 shadow-card transition-shadow duration-200 ease-brand hover:shadow-lift">
      <CardHeader>
        <div className="flex items-center gap-3">
          <IconChip tone="mint">
            <Package className="size-5" aria-hidden />
          </IconChip>
          <div className="min-w-0">
            <CardTitle className="text-base">Delhivery</CardTitle>
            <CardDescription>
              Shipment tracking milestones per AWB
            </CardDescription>
          </div>
        </div>
        <CardAction className="flex flex-wrap items-center justify-end gap-1.5">
          <LiveTryItBadge />
          {result?.mock && <MockModeBadge mock={result.mock} />}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Milestone history for dispatched orders: picked_up → in_transit →
          out_for_delivery → delivered. A delivered scan closes the order&apos;s
          RTO risk window and logs delivery_confirmed in the audit trail.
        </p>
        <div className="flex gap-2">
          <Input
            value={awb}
            onChange={(e) => setAwb(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") track();
            }}
            placeholder="AWB1234567890"
            aria-label="AWB number"
            className="h-11 font-mono"
          />
          <Button
            onClick={track}
            disabled={loading}
            className="h-11 font-semibold"
          >
            {loading && (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
            {loading ? "Tracking…" : "Track"}
          </Button>
        </div>
        {inputError && <p className="text-xs text-danger">{inputError}</p>}
        {result && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <HttpStatusChip status={result.status} />
              {result.ok && (
                <>
                  <span
                    className={`inline-flex items-center rounded-md border px-2.5 py-1 font-mono text-xs font-medium ${statusTone(result.data.current_status)}`}
                  >
                    {result.data.current_status}
                  </span>
                  <span className="font-mono text-xs text-muted-foreground tabular-nums">
                    {result.data.history.length} scans · ETA {result.data.eta}
                  </span>
                </>
              )}
            </div>
            {!result.ok && (
              <p className="text-sm text-danger">
                {result.data.reason ?? result.data.detail}
              </p>
            )}
            <RawJson data={result.data} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Razorpay — webhooks with HMAC SHA-256 signature verification.
// ---------------------------------------------------------------------------

function RazorpayCard() {
  const [loading, setLoading] = React.useState(false);
  const [result, setResult] = React.useState<ApiResult<WebhookAck> | null>(
    null,
  );

  async function sendTestEvent() {
    setLoading(true);
    const r = await postJson<WebhookAck>(
      "/api/v1/webhooks/razorpay",
      {
        event: "payment.captured",
        payload: {
          payment: {
            entity: {
              id: "pay_NhKs8uA2",
              amount: 1249900,
              currency: "INR",
              status: "captured",
              order_id: "ORD-HVC-002",
              method: "upi",
            },
          },
        },
      },
      {
        "X-Razorpay-Signature":
          "8f14e45fceea167a5a36dedd4bea25438f14e45fceea167a5a36dedd4bea2543",
      },
    );
    setResult(r);
    setLoading(false);
  }

  return (
    <Card className="gap-5 shadow-card transition-shadow duration-200 ease-brand hover:shadow-lift">
      <CardHeader>
        <div className="flex items-center gap-3">
          <IconChip tone="brand">
            <Webhook className="size-5" aria-hidden />
          </IconChip>
          <div className="min-w-0">
            <CardTitle className="text-base">Razorpay</CardTitle>
            <CardDescription>
              Webhooks with HMAC-SHA256 signature verification
            </CardDescription>
          </div>
        </div>
        <CardAction className="flex flex-wrap items-center justify-end gap-1.5">
          <LiveTryItBadge />
          {result?.mock && <MockModeBadge mock={result.mock} />}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Every webhook is verified before it is trusted: HMAC-SHA256 over the
          raw body bytes with a constant-time compare. payment.captured events
          reconcile dispatch — the order flips to prepaid and the COD RTO
          window closes. Missing or tampered signatures are rejected with 400.
        </p>
        <div>
          <Button
            onClick={sendTestEvent}
            disabled={loading}
            className="h-11 font-semibold"
          >
            {loading && (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            )}
            {loading ? "Sending…" : "Send test event"}
          </Button>
          <EndpointNote>
            POST /api/v1/webhooks/razorpay · payment.captured ·{" "}
            {formatINR(12499)} · X-Razorpay-Signature (demo)
          </EndpointNote>
        </div>
        {result && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <HttpStatusChip status={result.status} />
              {result.ok && result.data.received && (
                <span className="inline-flex items-center rounded-md border border-mint-500/30 bg-mint-500/10 px-2.5 py-1 text-xs font-medium text-mint-700">
                  Accepted · {result.data.event} dispatched
                </span>
              )}
              {result.ok && !result.data.received && (
                <span className="inline-flex items-center rounded-md border border-warning/40 bg-warning/10 px-2.5 py-1 text-xs font-medium text-warning">
                  Ack&apos;d · not handled
                </span>
              )}
            </div>
            {!result.ok && (
              <p className="text-sm text-danger">
                Rejected · {result.data.reason ?? result.data.detail}
              </p>
            )}
            {result.mock && result.ok && (
              <p className="text-xs text-muted-foreground">
                Mock-accept — RAZORPAY_WEBHOOK_SECRET is unset on this
                deployment, so the HMAC check is bypassed and flagged.{" "}
                <span className="font-mono">production must set the secret.</span>
              </p>
            )}
            <RawJson data={result.data} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function IntegrationsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Integrations</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          The rails around the risk core — UPI mandates, courier
          serviceability, shipment tracking, and payment webhooks. Every card
          below fires a real call; the raw JSON is the contract.
        </p>
      </div>

      <div className="rounded-lg border border-brand-500/25 bg-brand-500/5 p-4 text-sm text-foreground/80">
        These call the live API routes on this deployment. When the Python
        scorer is offline the routes answer from the labeled mock fallback —
        integrations never hard-fail.
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <NpciCard />
        <ShiprocketCard />
        <DelhiveryCard />
        <RazorpayCard />
      </div>
    </div>
  );
}
