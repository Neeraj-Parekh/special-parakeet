"use client";

// API reference — Stripe-style docs page for the RTO Trust Layer console.
// Light endpoint cards; the ONLY dark surfaces on this page are the shared
// CodeBlock components (navy-950 with a copy button). Every route below is
// live on this deployment — when the Python scorer is offline the routes
// answer from the labeled mock fallback and set `X-Mock-Mode: true`.

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
} from "@/components/ui/card";
import { CodeBlock } from "@/components/marketing/code-block";

/** Live deployment base URL. */
const BASE_URL = "https://rto-trust-layer.vercel.app";

type HttpMethod = "GET" | "POST" | "DELETE";

interface EndpointDoc {
  method: HttpMethod;
  path: string;
  /** Right-aligned auth badge, e.g. "Bearer JWT", "HMAC sig". */
  auth: string;
  description: string;
  requestCurl: string;
  responseBody: string;
}

const ENDPOINTS: EndpointDoc[] = [
  {
    method: "POST",
    path: "/api/risk/score",
    auth: "Public (mock-fallback)",
    description:
      "Score a COD order end-to-end (rules → mandate → cost-optimal BMR). Returns the decision, per-decision expected cost, and reason codes. Accepts an optional scorer-scope Bearer.",
    requestCurl: `curl -s -X POST ${BASE_URL}/api/risk/score \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $SCORER_KEY" \\
  -d '{
    "order_id": "ORD-DEMO-001",
    "amount_inr": 12499,
    "category": "Electronics",
    "customer_id": "CUST-DEMO",
    "address_quality": "vague",
    "city_tier": "tier_3",
    "payment_method": "COD",
    "prior_orders": 0,
    "prior_returns": 0,
    "items": 1,
    "order_hour": 22,
    "device": "Android App"
  }'`,
    responseBody: `{
  "prediction_id": "pred_7f3a91c2",
  "decision": "REJECT",
  "probability": 0.73,
  "decision_source": "cost_optimal_bmr",
  "cost_breakdown": {
    "ACCEPT": 22148,
    "REVIEW": 11093,
    "REJECT": 1000
  },
  "explanation": [
    { "feature": "payment_method", "value": "COD", "delta_prob": 0.115 }
  ],
  "audit_trail_url": "/audit/pred_7f3a91c2"
}`,
  },
  {
    method: "GET",
    path: "/api/v1/audit/verify-chain",
    auth: "Bearer JWT",
    description:
      "Verify the tamper-evident audit chain — SHA-256 raw_hash + prev_hash per record, Merkle-sealed per interval.",
    requestCurl: `curl -s ${BASE_URL}/api/v1/audit/verify-chain \\
  -H "Authorization: Bearer $JWT"`,
    responseBody: `{
  "intact": true,
  "records_checked": 1846,
  "first_bad_audit_id": null
}`,
  },
  {
    method: "GET",
    path: "/api/v1/cases",
    auth: "Bearer JWT",
    description:
      "List the REVIEW case queue with live SLA clocks. Filters: status, priority, assigned_to, customer_id, limit.",
    requestCurl: `curl -s "${BASE_URL}/api/v1/cases?status=open&priority=medium" \\
  -H "Authorization: Bearer $JWT"`,
    responseBody: `{
  "cases": [
    {
      "id": "case_9f21",
      "predictionId": "pred_4d2a91",
      "customerId": "CUST-RET-003",
      "orderId": "ORD-RET-003",
      "amountInr": 12499,
      "riskScore": 0.61,
      "priority": "medium",
      "status": "open",
      "assignedTo": "analyst.priya",
      "dueAt": "2025-01-04T18:30:00Z",
      "slaBreached": false
    }
  ],
  "total": 1
}`,
  },
  {
    method: "POST",
    path: "/api/v1/integrations/npci/mandate",
    auth: "Bearer JWT",
    description:
      "Create an OC-201B UPI Circle mandate — the OTP-gate alternative for high-value COD. Enforces the ₹50,000 cap, ₹5,000 per-txn, 24h cooling, and monthly frequency before the call.",
    requestCurl: `curl -s -X POST ${BASE_URL}/api/v1/integrations/npci/mandate \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $SCORER_KEY" \\
  -d '{
    "customer_id": "CUST-NEW-0001",
    "amount_cap_inr": 10000,
    "frequency": "monthly",
    "purpose": "ORD-HVC-002 COD OTP-gate"
  }'`,
    responseBody: `{
  "mandate_id": "NPCI-MND-lx4q8f2c",
  "customer_id": "CUST-NEW-0001",
  "amount_cap_inr": 10000,
  "frequency": "monthly",
  "per_txn_cap_inr": 5000,
  "cooling_period_h": 24,
  "max_devices": 5,
  "mandate_ttl_days": 180,
  "status": "ACTIVE",
  "created_at": "2025-01-04T09:12:33Z",
  "mock": true
}`,
  },
  {
    method: "GET",
    path: "/api/v1/integrations/shiprocket/validate-pincode/110001",
    auth: "Bearer JWT",
    description:
      "Pincode serviceability before the COD option renders at checkout — cod_available:false hides COD and forces prepaid, saving the merchant an RTO loss.",
    requestCurl: `curl -s ${BASE_URL}/api/v1/integrations/shiprocket/validate-pincode/110001 \\
  -H "Authorization: Bearer $SCORER_KEY"`,
    responseBody: `{
  "pincode": "110001",
  "cod_available": false,
  "prepaid_available": true,
  "expected_delivery_days": 2,
  "recommended_courier": "Bluedart",
  "mock": true,
  "timestamp": "2025-01-04T09:12:34Z"
}`,
  },
  {
    method: "GET",
    path: "/api/v1/models/drift",
    auth: "Bearer JWT",
    description:
      "PSI per feature vs the training window + worst PSI. Live DDM/ADWIN detector states stream from /api/metrics (Prometheus text format).",
    requestCurl: `curl -s ${BASE_URL}/api/v1/models/drift \\
  -H "Authorization: Bearer $JWT"`,
    responseBody: `{
  "status": "OK",
  "n_observed": 412,
  "psi": {
    "amount_inr": 0.018,
    "prior_returns": 0.024,
    "prior_orders": 0.013,
    "items": 0.009,
    "order_hour": 0.022
  },
  "worst_psi": 0.024
}`,
  },
  {
    method: "POST",
    path: "/api/copilot",
    auth: "Bearer JWT",
    description:
      "Policy-bounded operator console. A deterministic intent classifier runs first — refusals are code-enforced; the LLM only writes the prose.",
    requestCurl: `curl -s -X POST ${BASE_URL}/api/copilot \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer $JWT" \\
  -d '{ "question": "What is the drift state?" }'`,
    responseBody: `{
  "answer": "Drift status: OK. Worst PSI feature = 0.024 (CRITICAL threshold > 0.25). DDM state = STABLE (Gama 2014 §3.2). ADWIN state = STABLE (variable-length sliding window).",
  "verdict": "read",
  "sources": ["/v1/models/drift", "/metrics"],
  "mock": false
}`,
  },
  {
    method: "POST",
    path: "/api/v1/webhooks/razorpay",
    auth: "HMAC sig",
    description:
      "Razorpay webhook receiver — HMAC-SHA256 over the raw body bytes with a timing-safe compare. payment.captured flips the order to prepaid and closes the RTO window. Missing or tampered signatures are rejected with 400; while RAZORPAY_WEBHOOK_SECRET is unset the verifier mock-accepts and flags it.",
    requestCurl: `curl -s -X POST ${BASE_URL}/api/v1/webhooks/razorpay \\
  -H "Content-Type: application/json" \\
  -H "X-Razorpay-Signature: 8f14e45fceea167a5a36dedd4bea25438f14e45fceea167a5a36dedd4bea2543" \\
  -d '{
    "event": "payment.captured",
    "payload": {
      "payment": {
        "entity": {
          "id": "pay_NhKs8uA2",
          "amount": 1249900,
          "currency": "INR",
          "status": "captured"
        }
      }
    }
  }'`,
    responseBody: `{
  "received": true,
  "handled": true,
  "event": "payment.captured",
  "payment_id": "pay_NhKs8uA2",
  "refund_id": null,
  "amount": 1249900,
  "status": "captured",
  "note": "event payment.captured processed",
  "mock": true
}`,
  },
];

const METHOD_STYLES: Record<HttpMethod, string> = {
  GET: "border-mint-500/30 bg-mint-500/10 text-mint-700",
  POST: "border-brand-500/30 bg-brand-500/10 text-brand-600",
  DELETE: "border-signal-red/30 bg-signal-red/10 text-danger",
};

function MethodPill({ method }: { method: HttpMethod }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-md border px-2 py-0.5 font-mono text-xs font-bold tracking-widest ${METHOD_STYLES[method]}`}
    >
      {method}
    </span>
  );
}

function AuthBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-md border border-border/60 bg-muted/50 px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
      {label}
    </span>
  );
}

function EndpointCard({ doc }: { doc: EndpointDoc }) {
  return (
    <Card className="gap-4 shadow-card">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            <MethodPill method={doc.method} />
            <span className="min-w-0 break-all font-mono text-sm font-semibold text-navy-950">
              {doc.path}
            </span>
          </div>
          <AuthBadge label={doc.auth} />
        </div>
        <CardDescription>{doc.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 lg:grid-cols-2">
          <CodeBlock title="request · cURL" code={doc.requestCurl} />
          <CodeBlock title="response · 200" code={doc.responseBody} />
        </div>
      </CardContent>
    </Card>
  );
}

export default function ApiDocsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">API reference</h1>
        <p className="max-w-2xl text-sm text-muted-foreground">
          Real-time COD risk scoring — every route below is live on this
          deployment, with a labeled mock fallback when the Python scorer is
          offline.
        </p>
      </div>

      <div className="space-y-6">
        {ENDPOINTS.map((doc) => (
          <EndpointCard key={`${doc.method} ${doc.path}`} doc={doc} />
        ))}
      </div>

      <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-4 text-sm text-muted-foreground sm:flex-row sm:items-center sm:gap-3">
        <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider">
          Base URL
        </span>
        <code className="shrink-0 font-mono text-xs text-foreground/80">
          {BASE_URL}
        </code>
        <span className="hidden h-4 w-px shrink-0 bg-border sm:block" aria-hidden />
        <p>
          All routes return an{" "}
          <code className="font-mono text-xs">X-Mock-Mode: true</code> header
          when served from the mock fallback. Response bodies on this page are
          examples — live responses add generated ids and timestamps.
        </p>
      </div>
    </div>
  );
}
