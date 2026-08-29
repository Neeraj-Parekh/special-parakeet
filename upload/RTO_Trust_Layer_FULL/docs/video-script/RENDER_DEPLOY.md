# Manual Render Deploy Steps (fallback path)

The Render API call to auto-create the service was blocked because:
- The repo `Neeraj-Parekh/special-parakeet` is private
- The PAT provided has only `contents:write` scope (can't change visibility)
- Render's GitHub App is NOT installed on the user's GitHub account
  (Render needs this to fetch private repos)

The user's explicit fallback directive applies: "If the API call fails,
fall back to: write render.yaml, push, and tell me to go to render.com →
Blueprints → New Blueprint Instance → select repo → done."

## Steps for the user (~2 minutes)

1. Open https://render.com/dashboard in your browser (you're already
   logged in — your Render API token `<REDACTED: dead Render API token, revoked by user 2025-08-29>`
   is registered to "My Workspace" team).

2. First-time only: install the Render GitHub App.
   - Go to https://render.com/docs/configure-repo-access-via-github-app
   - Click "Configure GitHub App"
   - Select your `Neeraj-Parekh` GitHub account
   - Grant Render access to the `special-parakeet` repo (or "All repos"
     if you prefer)
   - Click "Install"

3. Create the blueprint instance.
   - Open this URL directly (one-click):
     https://render.com/dashboard#/infrastructure/blueprint/new?source=repo&repo=Neeraj-Parekh/special-parakeet&branch=main&blueprintPath=render.yaml
   - Render detects the `render.yaml` at the repo root.
   - Review the env vars (RTO_SCORER_KEYS / RTO_ADMIN_KEYS / etc. —
     all pre-filled from the YAML).
   - Pick the "starter" plan + "singapore" region (already set in YAML).
   - Click "Apply Blueprint".

4. Wait for the build (~5-8 min). Render runs:
   - `pip install -r requirements.txt`
   - `uvicorn src.api.routes:create_app --factory --host 0.0.0.0 --port 10000`
   - Health check at `/health` until 200

5. Get your URL: `https://rto-trust-layer.onrender.com`
   - Verify: `curl https://rto-trust-layer.onrender.com/health`
   - Dashboard: `https://rto-trust-layer.onrender.com/dashboard/`
   - OpenAPI docs: `https://rto-trust-layer.onrender.com/docs`

6. For the pitch video: open `https://rto-trust-layer.onrender.com/dashboard/`
   in your browser, follow the 6 demo moments in `docs/video-script/SCRIPT.md`.

## Free-tier caveats (per Render docs)
- 750 free instance-hours per month (sufficient for judging window)
- Spins down after 15 min idle — first request takes ~30s cold-start
- Hit `/health` once to wake it before running a demo
- No persistent disk on free tier — audit JSONL is wiped on re-deploy
  (production path uses Render-managed Postgres)
