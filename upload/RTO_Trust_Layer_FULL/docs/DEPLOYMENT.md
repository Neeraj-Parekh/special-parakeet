# Deployment — RTO Trust Layer

Four deployment paths, each verified to the extent the environment
allows. Pick the one that fits your use case.

## 1. Vercel (dashboard preview — no card required) — RECOMMENDED

The Next.js dashboard at `web/` deploys to Vercel's Hobby tier (free,
no credit card required). The dashboard has a built-in mock-mode
fallback (`src/lib/api-proxy.ts` returns mock data + sets
`X-Mock-Mode: true` when the Python backend is unreachable) so the
deploy renders a fully-populated dashboard even with no backend.

### Deploy steps (~3 min)

1. Go to https://vercel.com → New Project → Import from GitHub
2. Select `Neeraj-Parekh/special-parakeet`
3. **Root Directory: `web`** (critical — the Next.js app is the `web/` subfolder)
4. Framework auto-detected as Next.js → Deploy
5. URL: `https://special-parakeet-web-xxxx.vercel.app` — shareable

### Two demo modes

- **Mock mode (default, no backend):** the dashboard shows 3 demo
  orders (ACCEPT / REVIEW / REJECT) with a "preview without backend"
  badge. Polished + shareable but not live inference.
- **Live mode (optional):** run the Python API on your laptop
  (`RTO_SCORER_KEYS=score-demo-key uvicorn src.api.routes:create_app
  --factory --port 8000`), expose it via a free tunnel
  (`cloudflared tunnel --url http://localhost:8000` or `ngrok`), then
  set the Vercel project's `API_BASE_URL` env var to the tunnel URL.
  The dashboard then calls the real model live — SHAP, risk score,
  audit trail, all real, all over the public Vercel URL.

### Why Vercel over Render here

- Vercel Hobby tier requires NO credit card. Render's free web
  services require a card on file.
- The dashboard's mock-mode fallback means a Vercel deploy renders
  cleanly with zero backend — Render would need the Python service
  running to serve anything.
- Vercel cold-starts are faster for Next.js (the dashboard is
  server-rendered + cached at the edge).

## 2. Render (production demo — card required)

The repo's `render.yaml` is a Render Blueprint. One-command deploy:

### Via Render API (automated)

```bash
curl -H "Authorization: Bearer $RENDER_API_TOKEN" \
     -H "Content-Type: application/json" \
     -X POST https://api.render.com/v1/services \
     -d '{
       "type": "web_service",
       "name": "rto-trust-layer",
       "ownerId": "me",
       "repo": "https://github.com/Neeraj-Parekh/special-parakeet",
       "branch": "main",
       "runtime": "python",
       "plan": "starter",
       "region": "singapore",
       "buildCommand": "pip install -r requirements.txt",
       "startCommand": "uvicorn src.api.routes:create_app --factory --host 0.0.0.0 --port 10000",
       "envVars": [
         {"key": "PYTHON_VERSION", "value": "3.12.0"},
         {"key": "RTO_SCORER_KEYS", "value": "score-demo-key"},
         {"key": "RTO_ADMIN_KEYS", "value": "admin-demo-key"},
         {"key": "RTO_MANDATE_SECRET", "value": "ci-secret"},
         {"key": "RTO_AUDIT_SALT", "value": "ci-salt"}
       ]
     }'
```

### Via Render dashboard (manual)

1. Go to https://render.com → New → Blueprint
2. Connect your GitHub, select `Neeraj-Parekh/special-parakeet`
3. Render reads `render.yaml` → click Apply
4. Wait ~2 min for build + deploy
5. URL: `https://rto-trust-layer.onrender.com`

### Post-deploy verification

```bash
# Health (must return 200 + {"status":"ok"})
curl -s https://rto-trust-layer.onrender.com/health | jq .

# Dashboard (must return 200 HTML)
curl -s -o /dev/null -w "%{http_code}\n" https://rto-trust-layer.onrender.com/dashboard/

# Score an order (golden path)
curl -s -X POST https://rto-trust-layer.onrender.com/risk/score \
  -H "Authorization: Bearer score-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"DEMO-1","prior_orders":3,"prior_returns":1,"is_cod":1,"category":"Electronics","device":"Mobile","city_tier":"T1","address_quality":"verified"}' | jq .
```

### Render caveats (honest)

- **Free tier:** 750 instance-hours/month, spins down after 15min idle
  (~30s cold-start on first request). Sufficient for the buildathon
  judging window.
- **No persistent disk on free tier:** audit JSONL is wiped on
  re-deploy. For RBI MRM compliance, set `DATABASE_URL` to a Render
  Postgres instance (paid tier) after first apply.
- **Single service:** the Blueprint deploys ONE Python web service
  (file-mode audit, no Postgres/Redis). The Kafka + K8s path (below)
  is for self-hosted production.

## 2. Local (development + testing)

### Option A — pip + uvicorn (no Docker required)

```bash
git clone https://github.com/Neeraj-Parekh/special-parakeet.git
cd special-parakeet
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.routes:create_app --factory --host 0.0.0.0 --port 8000
```

Verify:
```bash
curl -s http://localhost:8000/health | jq .
# → {"status":"ok"}
```

### Option B — Docker Compose (full stack with Postgres + Redis)

```bash
docker compose up -d --wait
curl -s http://localhost:8000/health | jq .
```

`docker-compose.yml` starts: api, postgres, redis, stream-worker,
stream-processor. The API picks up Postgres + Redis mode automatically
via `DATABASE_URL` + `REDIS_URL` env vars.

## 3. Kubernetes (production-grade horizontal scale)

The `infra/k8s/` directory has a complete Kustomize stack:

```bash
# One-command deploy
kubectl apply -k infra/k8s/

# Wait for pods Ready
kubectl -n rto-trust-layer wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=rto-trust-layer --timeout=180s

# Port-forward for local testing
kubectl -n rto-trust-layer port-forward svc/api 8000:80 &
curl -s http://localhost:8000/health | jq .
```

### Resources deployed

| Resource | Kind | Purpose |
|---|---|---|
| `namespace.yaml` | Namespace | `rto-trust-layer` |
| `postgres-secret.yaml` | Secret | DB credentials (template) |
| `api-keys-secret.yaml` | Secret | API auth keys (template) |
| `postgres-statefulset.yaml` | StatefulSet | Postgres 15 + 5Gi PVC |
| `postgres-service.yaml` | Service | `postgres:5432` |
| `redis-deployment.yaml` | Deployment | Redis 7 (AOF, 256mb LRU) |
| `redis-service.yaml` | Service | `redis:6379` |
| `api-configmap.yaml` | ConfigMap | Non-sensitive env (REDIS_URL, KAFKA_BROKERS, RTO_HEAL_BACKEND) |
| `api-deployment.yaml` | Deployment | FastAPI, 2 replicas, liveness/readiness/startup probes |
| `api-service.yaml` | Service | `api:80` → container `:8000` |
| `hpa.yaml` | HPA | 2–10 replicas, CPU 70% / memory 80% target |

### HPA verification

```bash
kubectl -n rto-trust-layer get hpa api-hpa --watch
```

Load-test to trigger scale-up:
```bash
kubectl -n rto-trust-layer run loadgen --rm -it --image=busybox -- \
  /bin/sh -c 'while true; do wget -q -O- http://api/health; done'
```

### Kafka toggle (optional)

The `api-configmap.yaml` sets `KAFKA_BROKERS=""` (empty → Redis Streams
default). To enable Kafka:

```bash
# Deploy a Kafka broker (Strimzi or bitnami chart)
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install kafka bitnami/kafka -n rto-trust-layer

# Patch the ConfigMap + roll the Deployment
kubectl -n rto-trust-layer patch configmap api-config \
  -p '{"data":{"KAFKA_BROKERS":"kafka:9092"}}'
kubectl -n rto-trust-layer rollout restart deployment/api

# Verify the transport flipped
kubectl -n rto-trust-layer port-forward svc/api 8000:80
curl -s http://localhost:8000/health | jq .stream_transport
# → "kafka"
```

## Health check endpoints

After ANY deploy, verify these three return 200:

```bash
# 1. Liveness (is the process alive?)
curl -sf http://<host>/health && echo " ✅"

# 2. Dashboard (does the UI render?)
curl -sf -o /dev/null http://<host>/dashboard/ && echo " ✅"

# 3. Golden path (does the decision flow work end-to-end?)
curl -sf -X POST http://<host>/risk/score \
  -H "Authorization: Bearer score-demo-key" \
  -H "Content-Type: application/json" \
  -d '{"order_id":"SMOKE-1","prior_orders":3,"prior_returns":1,"is_cod":1,"category":"Electronics","device":"Mobile","city_tier":"T1","address_quality":"verified"}' \
  | jq -e '.verdict and .risk_score and .audit_id' && echo " ✅"
```

## Environment variable reference

See [`docs/ARCHITECTURE.md` § Environment variables](ARCHITECTURE.md#environment-variables)
for the complete table of required + optional env vars, defaults, and
purpose.
