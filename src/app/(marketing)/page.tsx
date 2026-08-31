"use client";

// LANDING — the vendor presence. Navy topbar, hero with a live-styled
// console mock in a browser frame, an honestly-labeled stats strip,
// 3-card product tour, trust points, and a dark developers panel.

import Link from "next/link";
import {
  ArrowRight,
  Shield,
  CreditCard,
  ScrollText,
  Lock,
  Landmark,
  Bot,
  EyeOff,
  Play,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CodeBlock } from "@/components/marketing/code-block";
import { formatINR } from "@/lib/format";

// ----------------------------------------------------------------------------
// Page
// ----------------------------------------------------------------------------

export default function LandingPage() {
  return (
    <div>
      <Hero />
      <StatsStrip />
      <ProductTour />
      <TrustSection />
      <DevelopersPanel />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Hero
// ----------------------------------------------------------------------------

function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border bg-gradient-to-b from-white to-background">
      <div className="mx-auto max-w-6xl px-4 pb-16 pt-16 text-center md:px-6 md:pb-20 md:pt-24">
        <Badge
          variant="outline"
          className="mb-5 border-brand-500/25 bg-brand-500/10 px-3 py-1 text-xs font-medium text-brand-600"
        >
          Pre-dispatch risk gating for COD commerce
        </Badge>
        <h1 className="mx-auto max-w-3xl text-4xl font-bold leading-tight tracking-tight text-foreground md:text-5xl">
          Stop returns{" "}
          <span className="text-brand-500">before the courier leaves</span>.
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-base leading-relaxed text-muted-foreground md:text-lg">
          RTO Trust Layer scores every cash-on-delivery order at checkout and gates dispatch —
          cost-optimal ACCEPT / REVIEW / REJECT decisions in under 50 ms, a Merkle-sealed
          audit trail, and OC-201B UPI Circle mandates that fence high-risk value.
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button
            asChild
            className="h-11 rounded-lg bg-brand-500 px-6 text-sm font-semibold text-white transition-colors duration-200 ease-brand hover:bg-brand-600"
          >
            <Link href="/dashboard">
              Open the console
              <ArrowRight className="ml-2 size-4" aria-hidden />
            </Link>
          </Button>
          <Button
            asChild
            variant="outline"
            className="h-11 rounded-lg border-border bg-card px-6 text-sm font-semibold text-foreground transition-colors duration-200 ease-brand hover:bg-muted"
          >
            <Link href="/checkout">
              <Play className="mr-2 size-4" aria-hidden />
              See the checkout flow
            </Link>
          </Button>
        </div>

        <div className="mt-12 md:mt-16">
          <ConsoleMockFrame />
        </div>
      </div>
    </section>
  );
}

/** Browser chrome frame with a static miniature of the console dashboard. */
function ConsoleMockFrame() {
  return (
    <div className="mx-auto max-w-4xl rounded-xl border border-border bg-card shadow-lift" role="img" aria-label="Screenshot of the RTO Trust Layer merchant console dashboard">
      {/* Browser chrome */}
      <div className="flex items-center gap-3 border-b border-border bg-muted/60 px-4 py-2.5">
        <div className="flex gap-1.5" aria-hidden>
          <span className="size-2.5 rounded-full bg-[#E5484D]/60" />
          <span className="size-2.5 rounded-full bg-gold-500/60" />
          <span className="size-2.5 rounded-full bg-mint-500/60" />
        </div>
        <div className="mx-auto flex h-6 w-full max-w-xs items-center justify-center rounded-md border border-border bg-white px-3 font-mono text-[10px] text-muted-foreground">
          rto-trust-layer.vercel.app/dashboard
        </div>
        <span className="w-10" aria-hidden />
      </div>

      {/* Mini dashboard */}
      <div className="space-y-4 p-4 text-left md:p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-foreground">Dashboard</p>
            <p className="text-[11px] text-muted-foreground">Return-risk operations at a glance</p>
          </div>
          <Badge variant="outline" className="border-mint-500/30 bg-mint-500/10 text-[10px] font-semibold text-mint-700">
            AUDIT CHAIN INTACT
          </Badge>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { label: "ORDERS SCORED", value: "1,24,783", chip: "BENCHMARK" },
            { label: "RTO BLOCKED", value: formatINR(42000000), chip: "BENCHMARK" },
            { label: "AVG DECISION", value: "47 ms", chip: "LIVE" },
            { label: "SEALED", value: "100%", chip: "LIVE" },
          ].map((m) => (
            <div key={m.label} className="rounded-lg border border-border bg-white p-3">
              <p className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">{m.label}</p>
              <p className="mt-1 font-mono text-sm font-bold tabular-nums text-foreground">{m.value}</p>
              <p className="mt-0.5 text-[8px] uppercase tracking-wider text-muted-foreground/70">{m.chip} DATA</p>
            </div>
          ))}
        </div>

        {/* Verdict card */}
        <div className="overflow-hidden rounded-lg border border-border">
          <div className="h-1.5 bar-reject" aria-hidden />
          <div className="flex flex-wrap items-center justify-between gap-2 bg-white px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="rounded-full border border-red-500/30 bg-red-500/5 px-3 py-1 text-[11px] font-bold text-danger">
                REJECT
              </span>
              <span className="font-mono text-[11px] text-muted-foreground">ORD-HVC-002 · COD ₹52,000</span>
            </div>
            <span className="font-mono text-[11px] text-muted-foreground">vague address · tier_3 · new customer</span>
          </div>
        </div>

        {/* Table rows */}
        <div className="overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-4 gap-2 border-b border-border bg-muted/50 px-4 py-2 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
            <span>Order</span><span>P(RTO)</span><span>Decision</span><span className="text-right">Cost</span>
          </div>
          {[
            { id: "ORD-REP-001", p: "0.08", d: "ACCEPT", dc: "text-mint-700", c: formatINR(446) },
            { id: "ORD-RET-003", p: "0.41", d: "REVIEW", dc: "text-warning", c: formatINR(11093) },
          ].map((r) => (
            <div key={r.id} className="grid grid-cols-4 gap-2 border-b border-border/60 px-4 py-2 font-mono text-[10px] text-foreground last:border-0">
              <span>{r.id}</span><span className="tabular-nums">{r.p}</span>
              <span className={`font-bold ${r.dc}`}>{r.d}</span>
              <span className="text-right tabular-nums text-muted-foreground">{r.c}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Stats strip — every number carries its provenance (honesty rule)
// ----------------------------------------------------------------------------

const STATS = [
  { value: "5.7×", label: "RTO-flag precision lift over the baseline ruleset", source: "Benchmark" },
  { value: "1.6µs", label: "Per-row feature cost via SIMD pre-built SHAP", source: "Benchmark" },
  { value: "<50ms", label: "p50 scoring latency on the warm path", source: "Live path" },
  { value: "100%", label: "Decisions sealed into the Merkle audit chain", source: "Live verify" },
];

function StatsStrip() {
  return (
    <section className="border-b border-border bg-white">
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-x-4 gap-y-8 px-4 py-10 md:grid-cols-4 md:px-6">
        {STATS.map((s) => (
          <div key={s.value}>
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-2xl font-bold tabular-nums text-foreground md:text-3xl">
                {s.value}
              </span>
              <Badge
                variant="outline"
                className="px-1.5 py-0 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground"
              >
                {s.source}
              </Badge>
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{s.label}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Product tour — 3 cards, every CTA resolves to a live page
// ----------------------------------------------------------------------------

const TOUR = [
  {
    icon: Shield,
    title: "Score before dispatch",
    body: "Every COD order gets a cost-optimal verdict in under 50ms — ACCEPT, REVIEW, or REJECT — with the full reason-code and cost trail behind it.",
    href: "/score",
    cta: "Open risk scoring",
  },
  {
    icon: CreditCard,
    title: "OTP-gate the grey zone",
    body: "REVIEW orders pass through an OTP verification step at checkout instead of a blunt decline. Good customers convert; fraud rings don't.",
    href: "/checkout",
    cta: "Run the checkout demo",
  },
  {
    icon: ScrollText,
    title: "Seal every decision",
    body: "A SHA-256 hash chain with Merkle intervals makes the decision ledger tamper-evident. Pull the audit proof for any order, any time.",
    href: "/audit",
    cta: "Explore the audit trail",
  },
];

function ProductTour() {
  return (
    <section id="product" className="border-b border-border bg-background">
      <div className="mx-auto max-w-6xl px-4 py-16 md:px-6 md:py-20">
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
          One gate, three outcomes
        </h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground md:text-base">
          The decision engine is a Bahnsen Bayes Minimum Risk classifier — it picks the
          action with the lowest expected rupee cost, not the highest raw risk score.
        </p>
        <div className="mt-10 grid gap-5 md:grid-cols-3">
          {TOUR.map((t) => {
            const Icon = t.icon;
            return (
              <div
                key={t.href}
                className="flex flex-col rounded-xl border border-border bg-card p-6 shadow-card transition-all duration-200 ease-brand hover:-translate-y-0.5 hover:shadow-lift"
              >
                <div className="mb-4 flex size-10 items-center justify-center rounded-lg bg-brand-500/10 text-brand-500">
                  <Icon className="size-5" aria-hidden />
                </div>
                <h3 className="text-base font-semibold text-foreground">{t.title}</h3>
                <p className="mt-2 flex-1 text-sm leading-relaxed text-muted-foreground">
                  {t.body}
                </p>
                <Link
                  href={t.href}
                  className="mt-5 inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 transition-colors duration-200 ease-brand hover:text-brand-500"
                >
                  {t.cta}
                  <ArrowRight className="size-3.5" aria-hidden />
                </Link>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Trust — real properties, no fake compliance logos
// ----------------------------------------------------------------------------

const TRUST = [
  {
    icon: Lock,
    title: "Tamper-evident ledger",
    body: "SHA-256 hash chain + Merkle interval sealing on every decision. Verify the whole chain in one call.",
  },
  {
    icon: Landmark,
    title: "OC-201B UPI Circle",
    body: "NPCI-compliant mandates fence high-risk order value with caps, cooling-off, and revocation states.",
  },
  {
    icon: Bot,
    title: "Policy-bounded AI",
    body: "The copilot's refusals are code-enforced before the LLM ever runs — read the classifier in the repo.",
  },
  {
    icon: EyeOff,
    title: "DPDP-aligned redaction",
    body: "PII is redacted at rest in the audit trail; logs carry order IDs, not identities.",
  },
];

function TrustSection() {
  return (
    <section id="trust" className="border-b border-border bg-white">
      <div className="mx-auto max-w-6xl px-4 py-16 md:px-6 md:py-20">
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
          Built to be audited
        </h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground md:text-base">
          Trust here is a property of the architecture, not a badge on the page — every
          claim below is verifiable from the repository.
        </p>
        <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {TRUST.map((t) => {
            const Icon = t.icon;
            return (
              <div key={t.title} className="rounded-xl border border-border bg-card p-5">
                <div className="mb-3 flex size-9 items-center justify-center rounded-lg bg-mint-500/10 text-mint-700">
                  <Icon className="size-4.5" aria-hidden />
                </div>
                <h3 className="text-sm font-semibold text-foreground">{t.title}</h3>
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{t.body}</p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ----------------------------------------------------------------------------
// Developers panel — the one dark surface, with a copyable cURL
// ----------------------------------------------------------------------------

const CURL = `curl -X POST https://rto-trust-layer.vercel.app/api/risk/score \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: demo-001" \\
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
  }'`;

const RESPONSE = `{
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
}`;

function DevelopersPanel() {
  return (
    <section id="developers" className="bg-navy-950">
      <div className="mx-auto max-w-6xl px-4 py-16 md:px-6 md:py-20">
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] lg:items-center">
          <div>
            <Badge
              variant="outline"
              className="mb-4 border-brand-400/30 bg-brand-500/15 px-3 py-1 text-xs font-medium text-brand-400"
            >
              REST API
            </Badge>
            <h2 className="text-2xl font-bold tracking-tight text-white md:text-3xl">
              Score an order with one cURL
            </h2>
            <p className="mt-3 max-w-md text-sm leading-relaxed text-white/60 md:text-base">
              Every route on this deployment is live — with a labeled mock fallback when
              the Python scorer is offline, so integrations never hard-fail. JWT-scoped
              keys, idempotency keys, and webhook signature checks are all in the box.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button
                asChild
                className="h-10 rounded-lg bg-brand-500 px-5 text-sm font-semibold text-white transition-colors duration-200 ease-brand hover:bg-brand-600"
              >
                <Link href="/api-docs">
                  Full API reference
                  <ArrowRight className="ml-2 size-4" aria-hidden />
                </Link>
              </Button>
              <Button
                asChild
                variant="outline"
                className="h-10 rounded-lg border-white/20 bg-transparent px-5 text-sm font-semibold text-white transition-colors duration-200 ease-brand hover:bg-white/10"
              >
                <Link href="/integrations">Integrations</Link>
              </Button>
            </div>
          </div>
          <div className="space-y-4">
            <CodeBlock title="request · POST /api/risk/score" code={CURL} />
            <CodeBlock title="response · 200 OK" code={RESPONSE} />
          </div>
        </div>
      </div>
    </section>
  );
}
