# RTO Trust Layer — Production Dashboard (Next.js 16)

> The judge-facing Risk Console. Replaces the legacy 215-line vanilla
> `dashboard/index.html` (which the buildathon advice correctly called
> "looks like a 2010 PHP admin panel"). This is the Stripe-quality
> rewrite: Next.js 16 App Router + TypeScript + Tailwind 4 + shadcn/ui
> + Recharts, with a Python FastAPI backend proxied via Next.js API
> routes (mock-mode fallback when the Python service is unreachable).

## Run locally

```bash
cd web
bun install            # or npm install
bun run dev            # starts on :3000
```

The dashboard talks to the Python backend at `http://localhost:8000`
(configurable via `API_BASE_URL`). When the backend is unreachable it
falls back to **mock mode** — every card renders with realistic data
derived from the real model metrics, and a `MOCK` badge flags the
origin so a judge is never misled.

## The 6 demo moments (the golden path a judge walks in 90 seconds)

| # | Demo | Where | What they see |
|---|------|-------|---------------|
| 1 | **Score an order** | Order form → "Score order" | Verdict card: decision (ACCEPT/REVIEW/REJECT), P(RTO), latency, rule_fired, audit-trail link |
| 2 | **Explainability** | Verdict card → Explainability panel | "73% risk because: COD + ₹12,400, new customer, vague address in Tier-3 city" — the one-line why |
| 3 | **SHAP waterfall** | Verdict card → SHAP contribution chart | Horizontal diverging bars: base E[f(x)] → ±Δ per feature → final P(RTO), with the ACCEPT/REJECT threshold ladder |
| 4 | **Rules toggle → instant REJECT** | Rules engine what-if card | Flip the "Block COD > ₹50K" switch; the before→after diff shows REJECT→REVIEW with a FLIPPED badge (no API call) |
| 5 | **Agent refuses manual override** | Operator console | Type "Block order ORD-123"; the bounded agent responds "I cannot for ORD-123. Track D V3 §7.1 — no manual per-order override path exists…" with a REFUSED pill + policy cite |
| 6 | **Cost-curve slider** | Cost-curve slider card | Slide C_fn from ₹600 → ₹5,000; the BMR decision callout flips REVIEW→REJECT live as the REJECT cost-curve crossover drops to lower P(RTO) |

Plus the **Narrative Pivot card** — the honest thesis: Amazon India
PR-AUC 0.1027 (no customer IDs, ~0.12 ceiling) → Olist Brazil PR-AUC
0.3950 (same model, real customer IDs, 32× baseline lift). The weakest
number becomes proof of the insight: per-customer history is the signal,
and Razorpay has it natively.

## Component map

```
src/
├── app/
│   ├── page.tsx                  # the 4-tab Risk Console V2 (854+ lines)
│   ├── layout.tsx                # root layout (AppShell, fonts, metadata)
│   └── api/                      # Next.js API routes → proxy to Python
│       ├── risk/score/route.ts   # POST /risk/score (mock fallback)
│       ├── v1/rules/route.ts     # GET/POST /v1/rules
│       ├── audit/                # audit trail + verify-chain
│       ├── copilot/route.ts      # z-ai-web-dev-sdk LLM copilot
│       ├── feedback/ingest/      # Track-B feedback loop
│       ├── metrics/route.ts      # Prometheus proxy
│       └── v1/...                # simulate, models, policy, usage
├── components/
│   ├── shap-waterfall.tsx        # ← NEW: diverging SHAP contribution chart
│   ├── rules-toggle-card.tsx     # ← NEW: live what-if rules engine (demo #4)
│   ├── agent-console.tsx         # ← NEW: bounded operator console (demo #5)
│   ├── narrative-pivot-card.tsx  # ← NEW: Amazon 0.10 → Olist 0.40 story
│   ├── cost-curve-slider.tsx     # ← NEW: live BMR cost-curve slider (demo #6)
│   ├── decision-badge.tsx        # ACCEPT/REVIEW/REJECT pill + source
│   ├── app-shell.tsx             # QueryClient + ThemeProvider + ApiKey
│   ├── app-header.tsx            # nav + API-key inputs + mock badge
│   ├── app-footer.tsx            # sticky footer
│   ├── copilot-fab.tsx           # floating LLM assistant
│   ├── api-key-context.tsx       # scorer/admin key store
│   ├── theme-provider.tsx       # next-themes
│   └── ui/                       # shadcn/ui (45 components)
└── lib/
    ├── api-proxy.ts              # callBackend + mockOk + forwardResponse
    ├── mock-data.ts              # DEMO_ORDERS + mockScore + DEFAULT_RULES
    ├── db.ts                     # Prisma client
    └── utils.ts                  # cn() class merge
```

## Why this isn't vanilla HTML

The advice said "This is a science fair project." It was — the old
`dashboard/index.html` was 215 lines of hand-rolled HTML with no
component model, no mock-mode, no SHAP viz, no rules toggle, no agent
console. This rewrite is a real product surface:

- **854-line page** with 4 tabs (Risk Console / Audit Explorer / Rules
  Manager / Model Health), 17 API routes, 45 shadcn/ui primitives
- **Mock-mode fallback** so the demo works without the Python backend
- **SessionStorage persistence** so a refresh mid-demo doesn't wipe the
  recent-decisions table
- **Accessibility**: semantic HTML, ARIA labels, sr-only live regions,
  44px touch targets, keyboard-navigable
- **Responsive**: mobile-first, lg:grid-cols-[420px_1fr] desktop split
- **Sticky footer** (flex min-h-screen + mt-auto)

## Honest metrics (no PR-AUC 0.55 lie)

| Model | Dataset | PR-AUC | Brier | Note |
|-------|---------|--------|-------|------|
| amazon_histgb_20260827 | Amazon India (128K) | **0.1027** | 0.0179 | no customer_id column; ~0.12 ceiling |
| rto_olist_histgb_20260828 | Olist Brazil (99K) | **0.3950** | 0.0439 | same model + customer_unique_id → 3.8× lift |

The old README claimed PR-AUC 0.55 on synthetic data. That was a lie.
The real numbers are above, with full provenance in
`data/olist/metrics.json` + `reports/kaggle/MODEL_CARD.md`. The
`?dataset=amazon|olist` query param on `/risk/score` lets a judge switch
live and see the user_rto_rate feature carry the lift.

## Deployed URL

The dashboard runs on the sandbox preview (port 3000 via the gateway).
For external deploy, the `Dockerfile` + `docker-compose.yml` at the repo
root build the Python backend; this `web/` folder is a standard Next.js
standalone build (`bun run build` → `.next/standalone`). See
`infra/render.yaml` for a one-click Render.com blueprint.

## Deploy

The dashboard + Python scorer ship as two independent containers so
they can be deployed to any platform with a public URL judges can
click. Pick the path of least resistance:

### Option A — Render.com (recommended · one-click)

The Render Blueprint at `infra/render.yaml` provisions two web
services off the `Neeraj-Parekh/special-parakeet` repo:

| Service | Dockerfile | Port | Health | URL (post-deploy) |
|---------|------------|------|--------|-------------------|
| `rto-trust-layer-api` | `./Dockerfile` | 8000 | `/health` | `https://rto-trust-layer-api.onrender.com` |
| `rto-trust-layer-dashboard` | `./Dockerfile.web` | 3000 | `/` | `https://rto-trust-layer-dashboard.onrender.com` |

One-click deploy URL (paste into a browser, sign in with GitHub):

```
https://render.com/dashboard#/infrastructure/blueprint/new?source=repo&repo=Neeraj-Parekh/special-parakeet&branch=main&blueprintPath=infra/render.yaml
```

Or via the Render CLI:

```bash
pip install render
render blueprint apply infra/render.yaml
```

After the first apply, set the four secret env vars in the Render
dashboard (they're flagged `sync: false` in the YAML so they're not
committed): `RTO_SCORER_KEYS`, `RTO_ADMIN_KEYS`, `RTO_MANDATE_SECRET`,
`RTO_AUDIT_SALT`. The dashboard auto-discovers the api service via
`API_BASE_URL=https://rto-trust-layer-api.onrender.com`.

**Free-tier caveats:** services spin down after 15 min idle (~30s
cold-start); 750 free instance-hours per month covers both services;
no persistent disk on free tier — point `DATABASE_URL` at Render
Postgres (free) for durable audit records.

### Option B — Fly.io

The Fly app definition at `infra/fly.toml` deploys the Python scorer
to Fly.io's free tier (3 shared-cpu-1x 256MB VMs per org, no cold-start
fee). The dashboard can be deployed as a second Fly app pointed at the
same `Dockerfile.web`.

```bash
brew install flyctl        # macOS — or see https://fly.io/docs/hands-on/install-flyctl/
flyctl auth login
flyctl apps create rto-trust-layer
flyctl deploy              # uses infra/fly.toml
# set secrets (don't bake them in):
flyctl secrets set RTO_SCORER_KEYS=score-demo-key
flyctl secrets set RTO_ADMIN_KEYS=admin-demo-key
flyctl secrets set RTO_MANDATE_SECRET=$(openssl rand -hex 32)
flyctl secrets set RTO_AUDIT_SALT=$(openssl rand -hex 16)
# deploy the dashboard as a second app:
flyctl apps create rto-trust-layer-dashboard
flyctl deploy --dockerfile Dockerfile.web \
  --app rto-trust-layer-dashboard \
  --env API_BASE_URL=https://rto-trust-layer.fly.dev
```

Public URLs post-deploy:

- API: `https://rto-trust-layer.fly.dev` (probe `/health`)
- Dashboard: `https://rto-trust-layer-dashboard.fly.dev`

**Free-tier caveats:** apps auto-stop after 1 hour idle, auto-start on
the next request (~30s cold-start); 3 GB outbound transfer free per
month.

### Option C — Local Docker (full stack in one command)

The repo root's `docker-compose.web.yml` brings up both the Python
scorer (port 8000) and the Next.js dashboard (port 3000) with one
command:

```bash
export RTO_SCORER_KEYS=score-demo-key
export RTO_ADMIN_KEYS=admin-demo-key
export RTO_MANDATE_SECRET=$(openssl rand -hex 32)
export RTO_AUDIT_SALT=$(openssl rand -hex 16)
docker compose -f docker-compose.web.yml up --build
```

Then open `http://localhost:3000/`. The dashboard auto-discovers the
scorer at `http://api:8000` via the compose network alias.

For the full observability stack (Postgres + Redis + Jaeger +
Prometheus + Grafana + nginx + the stream workers), use the repo
root's `docker-compose.yml --profile full` instead.

### Deploy files

| File | Purpose |
|------|---------|
| `infra/render.yaml` | Render.com Blueprint (2 services, auto-deploy on `main`) |
| `infra/fly.toml` | Fly.io app definition for the Python scorer (region `sin`, free-tier VM) |
| `Dockerfile.web` | Multi-stage Next.js 16 standalone build (`node:20-alpine`, ~180MB final image) |
| `docker-compose.web.yml` | Local 2-service compose (api + web) — what a judge runs |
| `Dockerfile` | Python scorer image (single-stage, `python:3.12-slim`) |
| `docker-compose.yml` | Full 12-service observability stack (api + postgres + redis + nginx + prometheus + grafana + jaeger + alertmanager + stream workers + 2 volumes, behind the `full` profile) |

