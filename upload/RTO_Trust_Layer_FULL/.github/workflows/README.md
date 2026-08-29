# GitHub Actions Workflows

This directory contains 5 GitHub Actions workflows that cover the full RTO Trust Layer CI/CD/MLOps surface — lint+test, container build/release, nightly retraining, MLOps 7-stage pipeline, and auto-screenshots deployed to GitHub Pages.

> **Note on authentication:** the user's GitHub repo (`Neeraj-Parekh/special-parakeet`) is now **PRIVATE**. This means the auto-provisioned `GITHUB_TOKEN` works for GHCR (`packages: write`) and Pages (`pages: write` + `id-token: write`) without any extra PAT or App configuration. Each workflow that needs those scopes declares them explicitly in a top-level `permissions:` block.

## Workflows

### 1. `ci.yml` — CI Quality

**Trigger:** `push` to `main`/`master`, `pull_request` to `main`/`master`, manual `workflow_dispatch`. Concurrency-cancelled by ref.

**Purpose:** runs on every commit to gate merges. Three jobs: `lint-test` (ruff + pytest against a real Postgres + Redis service container, then a champion-model-artifact validation gate, then a group-leakage gate against the committed `data/processed/train_processed.csv`, then Alembic migrations + isolated Postgres-path + Redis streaming tests), `docker-build` (Buildx build of the in-repo Dockerfile, Trivy CRITICAL+HIGH scan that fails the run on any vuln, SARIF upload to the Security tab), and `load-test` (k6 against the freshly-built image via docker compose, 3 scenarios — steady 50vu/2m, ramp to 200vu/2.5m, spike 400rps/30s, thresholds p99<400ms + error_rate<1%).

**Produces:** JUnit test-results artifact, Trivy SARIF report, k6 load profile summary. Does NOT push an image (the `mlops.yml` pipeline owns the GHCR push after canary gating).

### 2. `mlops.yml` — MLOps Pipeline

**Trigger:** `push` to `main`/`master` on `data/**`, `src/models/**`, `src/features/**`, `scripts/evaluate.py` and related scripts; weekly `schedule: cron: "0 2 * * 1"` (Monday 2am UTC); manual `workflow_dispatch`. Not concurrency-cancelled (a started training run finishes).

**Purpose:** the 7-stage TFX-style continuous-training pipeline (Baylor 2017 + Paleyes 2022): (1) `data-analysis` — TFX `generate_data_statistics` per-feature stats; (2) `data-validation` — TFX `build_and_apply_schema` with actionable anomaly descriptions, fails on blocking anomalies; (3) `model-training` — TFX `Trainer` with warm-starting on rolling 90-day window (Gama 2014 §3.3), CI gate fails if PR-AUC < 0.60, registers the new champion in the Postgres-backed `model_registry` table (Track E dual-mode); (4) `model-gate` — TFX `gate_model_promotion` canary vs incumbent on PR-AUC + cost-weighted error + per-slice metrics (merchant_category, cod_vs_prepaid, pin_code_tier), blocks promotion on regression > 5%; (5) `container-build` — CD Buildx + push to `ghcr.io/<repo>:<sha>` with GHA cache; (6) `deploy-staging` — CD deploy hook (blue-green pattern documented, `kubectl` commands as `::notice` annotations — honest, not sandbox-runnable without K8s); (7) `monitor` — `scripts/check_error_rate.py` is the real monitor (queries Prometheus, exits 1 on threshold breach), `kubectl rollout undo` documented as a production pattern.

**Produces:** promoted champion row in `model_registry`, pushed GHCR image, canary-gate decision audit row, Prometheus error-rate probe result.

### 3. `train.yml` — Nightly Retrain (Olist)

**Trigger:** `schedule: cron: "0 2 * * *"` (2 AM UTC nightly), manual `workflow_dispatch`. Not concurrency-cancelled.

**Purpose:** nightly retraining on the committed `data/olist/olist_merged_orders.csv` (Brazilian e-commerce, not the Indian COD/Kaggle path). Single job `train` on `ubuntu-latest`, Python 3.12. Runs an inline Python training step that auto-detects the target column (`is_returned` / `returned` / `rto` / derived from `order_status`), the time column (for chronological 80/20 split — no customer leakage), drops high-cardinality ID columns, one-hot encodes categoricals, and trains a `HistGradientBoostingClassifier(max_iter=250, max_depth=4, learning_rate=0.08, l2_penalty=0.1, class_weight='balanced', random_state=42)`. Saves the artifact triplet (`model.pkl` + `metrics.json` + `priors.json`) to `models/olist/`. **PR-AUC gate:** fails the run if `pr_auc < 0.35` (the user's spec — a more lenient floor than `mlops.yml`'s 0.60 because Olist is Brazilian cross-border with a different RTO base-rate). If the gate passes, `stefanzweifel/git-auto-commit-action@v5` commits the artifact triplet back to `main` as `rto-bot`. If the gate fails, no commit (the run is preserved for debugging). `metrics.json` is uploaded as an artifact with 30-day retention.

**Produces:** `models/olist/model.pkl`, `models/olist/metrics.json`, `models/olist/priors.json` committed to `main` (only if PR-AUC ≥ 0.35).

### 4. `docker.yml` — Docker Release

**Trigger:** `push` of `v*` tags (e.g. `v1.2.3`), manual `workflow_dispatch`. Not concurrency-cancelled.

**Purpose:** build and push the production image to GHCR. Single job `docker` on `ubuntu-latest`. Steps: QEMU (multi-arch), Buildx setup, GHCR login with auto-provisioned `GITHUB_TOKEN`, metadata-action for tags + labels (`ghcr.io/<owner>/<repo>:latest` + `:version` + `:sha-<sha7>`), Buildx `build-push-action` with `cache-from: type=gha` + `cache-to: type=gha,mode=max`, platforms `linux/amd64,linux/arm64`, `push: true`. The `Inspect pushed image` step prints the multi-arch digests to the Actions log for the release notes.

**Produces:** pushed multi-arch image at `ghcr.io/<owner>/<repo>:latest` + version + SHA tags.

### 5. `screenshot.yml` — Screenshots + GitHub Pages

**Trigger:** `push` to `main`, manual `workflow_dispatch`. Not concurrency-cancelled (a half-deployed Pages site is worse than a stale one).

**Purpose:** auto-capture screenshots of the running FastAPI app and deploy them to GitHub Pages so the pitch deck can embed live URLs (`https://<owner>.github.io/<repo>/openapi-docs.png`). Single job `screenshot` on `ubuntu-latest` in the `github-pages` environment. Steps: install Python deps, start the API in background with CI demo keys (`RTO_SCORER_KEYS=ci-scorer RTO_ADMIN_KEYS=ci-admin RTO_MANDATE_SECRET=ci-secret RTO_AUDIT_SALT=ci-salt uvicorn src.api.routes:create_app --factory --host 0.0.0.0 --port 8000 &`), wait for `/health` (30s retry loop), set up Node 20, install Playwright Chromium, run `node tests/screenshot.js` which captures 4 full-page screenshots at 1280×800: `/docs` (OpenAPI), `/health`, `/` (dashboard or fallback to `/docs`), and `/risk/score` (the 405/404 response page). Upload screenshots as artifact (30-day retention), then deploy `./screenshots/` to GitHub Pages via `actions/configure-pages@v4` + `actions/upload-pages-artifact@v3` + `actions/deploy-pages@v4`. Uvicorn is killed in an `if: always()` teardown step.

**Produces:** GitHub Pages site at `https://<owner>.github.io/<repo>/` serving `openapi-docs.png`, `health.png`, `dashboard.png`, `score-endpoint.png`, plus a `screenshots` Actions artifact.

## Files

| File | Lines | Purpose |
|---|---|---|
| `ci.yml` | ~237 | Lint + test + docker build + Trivy + k6 load test, every push. |
| `mlops.yml` | ~471 | 7-stage TFX pipeline (data → train → gate → build → deploy → monitor), weekly + data-change-triggered. |
| `train.yml` | ~210 | Nightly 2am UTC Olist retrain with PR-AUC gate + rto-bot commit-back. |
| `docker.yml` | ~85 | `v*` tag push → multi-arch GHCR image build + push. |
| `screenshot.yml` | ~155 | Push to main → 4 Playwright screenshots → GitHub Pages deploy. |

## Notes for the user

- All workflows are valid YAML — verified via `python -c "import yaml; yaml.safe_load(open('.github/workflows/<file>.yml'))"` for each.
- The `ci.yml` and `mlops.yml` trigger config has `branches: [main, master]` (YAML list form) — never `ain, master]` (which would be invalid YAML; a previous agent's bug was already fixed before this task ran).
- The repo is PRIVATE — `GITHUB_TOKEN` works for GHCR (`packages: write` in `docker.yml`) and Pages (`pages: write` + `id-token: write` in `screenshot.yml`) without extra PAT/App configuration. If you make the repo public again, these still work — `GITHUB_TOKEN` is always auto-provisioned.
- The nightly train.yml's PR-AUC gate uses a `0.35` floor (per the user's spec) — this is intentionally more lenient than `mlops.yml`'s `0.60` floor because Olist is Brazilian cross-border (different RTO base-rate). If you want the same strict floor, edit `train.yml` and change `pr < 0.35` to `pr < 0.60`.
- `tests/screenshot.js` is the Playwright script that `screenshot.yml` runs. It is the only file outside `.github/workflows/` that this task created/touched. Node-side syntax check passes (`node --check tests/screenshot.js`).
