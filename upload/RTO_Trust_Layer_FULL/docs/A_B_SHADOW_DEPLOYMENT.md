# A/B Testing & Shadow Deployments

> **What this doc covers:** The 5 deployment patterns a Razorpay
> MLOps team would run (shadow / canary / A-B / key-routing /
> traffic-mirror), the `experiments` table schema that holds
> them, the routing-layer pseudo-code that decides which model
> a request hits, and the 2 auto-rollback conditions (p99 > 150ms,
> error > 0.1% over 5 min). Honest status: 📋 architecture-future
> — the `model_registry.is_challenger` + `traffic_split` columns
> exist in our schema but the runtime routing layer is NOT
> wired (`scripts/canary_gate.py` runs in CI only).
>
> **Paper cited:** Jeffrey Taylor, "A/B Testing in Production
> MLOps," 2025 — "The hard part isn't running the test — it's
> designing it properly and having the discipline to let it
> finish."
>
> **Honest status legend:** ✅ shipped · 🔧 in-progress · 📋
> architecture-future.

---

## 0. Why this doc exists

The user's #6 ask ("aim to actually perform what the company
performs") + FOLLOWUP.md §8 demand we document the 5 deployment
patterns and the routing layer. We do NOT ship the runtime
router today (📋 future); the `experiments` table schema + the
pseudo-code in §4 prove we understand the production shape.

---

## 1. The 5 deployment patterns

| # | Pattern | What it does | Why use it | Our status |
|---|---------|--------------|------------|------------|
| 1 | **Shadow deployment** | New model gets 100% of traffic; output is logged but NOT used. Compare offline. | Safest "is this model even directionally correct?" test — zero user impact | 📋 future |
| 2 | **Canary** (1%→5%→30%→100%) | Gradual shift; auto-rollback on regression | Catches perf / accuracy regressions early | 📋 future |
| 3 | **A/B test** (50/50) | Compare on business metrics (revenue, RTO rate) | Statistical significance on the metric that matters | 📋 future |
| 4 | **Key-based routing** | Gold merchants → model A, platinum → model B | Merchant-tier optimization | 📋 future |
| 5 | **Traffic mirroring** | Copy live requests to offline analysis | Offline replay + retraining data | 📋 future |

---

## 2. The `experiments` table schema

The schema below is 📋 future (not in `alembic/versions/`).
The current `model_registry` table (✅ shipped,
`src/ml/registry.py:373`) has the columns `is_champion`,
`is_challenger`, `traffic_split` but no `experiments` table
linking a challenger to a routing policy + auto-rollback
thresholds + result metrics.

```sql
-- 📋 future — alembic/versions/008_experiments.py
CREATE TABLE experiments (
    experiment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    champion_version  TEXT NOT NULL REFERENCES model_registry(version),
    challenger_version TEXT NOT NULL REFERENCES model_registry(version),
    pattern           TEXT NOT NULL
                      CHECK (pattern IN ('shadow', 'canary', 'ab',
                                          'key_route', 'mirror')),
    traffic_split_a   REAL NOT NULL DEFAULT 0.0  -- 0..1
                      CHECK (traffic_split_a >= 0.0
                         AND traffic_split_a <= 1.0),
    routing_key       TEXT,  -- 'merchant_id' for key_route; NULL otherwise
    routing_values    JSONB,  -- ['gold','platinum'] etc for key_route
    rollback_p99_ms   INT NOT NULL DEFAULT 150,  -- auto-rollback if p99 exceeds
    rollback_err_pct  REAL NOT NULL DEFAULT 0.001,  -- 0.1% errors over 5min
    rollback_window_s INT NOT NULL DEFAULT 300,  -- 5 min rolling
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at          TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'running',
                                        'rolled_back', 'promoted',
                                        'aborted')),
    winner_version    TEXT REFERENCES model_registry(version),
    notes             JSONB,
    created_by        TEXT NOT NULL,
    CHECK ((pattern = 'key_route') = (routing_key IS NOT NULL))
);
CREATE INDEX ix_experiments_status ON experiments(status);
CREATE INDEX ix_experiments_champion ON experiments(champion_version);
```

The constraints enforce:
* `traffic_split_a` is in `[0, 1]` — the fraction of traffic to
  model A (champion). The rest goes to model B (challenger).
* `pattern = 'key_route'` REQUIRES a `routing_key` (else NULL).
* Status lifecycle: `draft → running → (rolled_back | promoted | aborted)`.

---

## 3. The routing layer (pseudo-code)

```python
# 📋 future — src/api/routing.py (NOT shipped today)

def route_request(experiment: dict, order: dict) -> str:
    """Decide which model version serves this request.

    Pseudo-code per FOLLOWUP §8:
        hash(merchant_id) % 100 < traffic_split_a * 100 → A (champion)
        else → B (challenger)

    For shadow: always invoke BOTH A and B; return A's output;
    log B's output for offline comparison.
    For canary: split per the traffic_split_a; rollback on
    threshold breach.
    For ab: 50/50.
    For key_route: lookup routing_key in routing_values → A or B.
    For mirror: invoke A; copy the request to an offline queue
    for B.

    Paper: Taylor 2025 §3.2 — "hash-based routing is the only
    way to get stable per-merchant assignment across requests."
    """
    pattern = experiment["pattern"]
    split_a = experiment["traffic_split_a"]

    if pattern == "shadow":
        out_a = invoke_model(experiment["champion_version"], order)
        # Fire-and-forget to the challenger; output logged only.
        publish_to_shadow_queue(experiment["challenger_version"], order)
        return out_a

    if pattern == "canary" or pattern == "ab":
        # Stable per-merchant hash so a given merchant always
        # sees the SAME model (avoids per-merchant flapping).
        bucket = hash(order["merchant_id"]) % 100
        version = (experiment["champion_version"] if bucket < split_a * 100
                   else experiment["challenger_version"])
        out = invoke_model(version, order)
        # Auto-rollback check.
        if exceeds_rollback_thresholds(experiment):
            set_status(experiment["experiment_id"], "rolled_back")
            promote_to_champion(experiment["champion_version"])
        return out

    if pattern == "key_route":
        merchant_tier = lookup_merchant_tier(order["merchant_id"])
        routing_values = experiment["routing_values"]
        if merchant_tier in routing_values:
            return invoke_model(experiment["challenger_version"], order)
        return invoke_model(experiment["champion_version"], order)

    if pattern == "mirror":
        out = invoke_model(experiment["champion_version"], order)
        # Copy the request + response to the offline analysis queue.
        publish_to_offline_queue(experiment, order, out)
        return out

    raise ValueError(f"unknown pattern {pattern}")


def exceeds_rollback_thresholds(experiment: dict) -> bool:
    """Auto-rollback if p99 latency > 150ms OR error rate > 0.1%
    over a 5-minute rolling window. Per FOLLOWUP §8.

    Implementation would query Prometheus:
        histogram_quantile(0.99,
          rate(http_request_duration_seconds_bucket{model=...}[5m]))
        > 0.150
    AND
        sum(rate(http_requests_total{model=..., status=5xx}[5m]))
        / sum(rate(http_requests_total{model=...}[5m]))
        > 0.001
    """
    p99_ms = prom_query_p99(experiment["challenger_version"])
    err_pct = prom_query_err_rate(experiment["challenger_version"])
    if p99_ms > experiment["rollback_p99_ms"]:
        return True
    if err_pct > experiment["rollback_err_pct"]:
        return True
    return False
```

---

## 4. Auto-rollback conditions (verbatim from FOLLOWUP §8)

| Metric | Threshold | Window | Action |
|--------|-----------|--------|--------|
| p99 latency | > 150 ms | rolling 5 min | Set experiment `status = 'rolled_back'`; promote champion to 100% |
| Error rate (5xx) | > 0.1% | rolling 5 min | Same |
| Drift detected (DDM/ADWIN) | DRIFT signal | n/a | Pause experiment; open case |
| Audit write errors | > 0 | 1 min | Pause; alert ops (see `docs/CHAOS_ENGINEERING.md` event #4) |
| Manual abort | operator call | n/a | Set `status = 'aborted'` |

---

## 5. The current state (honest)

### 5.1 What's shipped
* `model_registry` table with `is_champion`, `is_challenger`,
  `traffic_split` columns (`src/ml/registry.py:373` — `_register_model_postgres`).
* `register_model(version, model_path, metrics, champion)` API
  (line 70) + `_safe_register_model` (line 730 in routes) with
  corrupt-registry-file recovery.
* `current_champion()` (line 343) + `get_priors()` (line 254) for
  per-model priors lookup.
* `scripts/canary_gate.py` — CI-time canary gate that runs
  offline on a held-out slice (NOT a runtime router).

### 5.2 What's NOT shipped
* The `experiments` table (📋 future — alembic 008).
* The runtime router (`src/api/routing.py`).
* The Prometheus p99 / error-rate queries (we have Prometheus
  scraping via `monitoring/prometheus.yml` but the queries
  against the metrics API are not wired to the router).
* The shadow-queue + offline-mirror queue (would extend
  `src/stream/producer.py`).

### 5.3 Where we lag Razorpay
Razorpay's MLOps platform runs all 5 patterns via a Spinnaker +
Kayenta pipeline (auto-rollback on Kayenta's canary score).
Our `scripts/canary_gate.py` is a CI-only step. **Gap: 0
runtime router, 0 experiments table, 0 Prom→rollback wiring.**

---

## 6. The honest design choices (Taylor 2025)

Taylor's paper §3 catalogues the failure modes of A/B tests in
MLOps. The 5 we'd cite:

1. **Peeking** — checking the result mid-experiment inflates
   false positives. Fix: pre-register the sample size; only
   check at the end. (We'd store `target_sample_size` in the
   `experiments` row.)
2. **Multiple comparisons** — testing 5 challengers at once
   guarantees a false winner. Fix: Bonferroni correction
   (α/n = 0.05/5 = 0.01 per challenger).
3. **Metric tunnel vision** — A/B on PR-AUC while business
   metric (RTO rate, revenue) regresses. Fix: track BOTH ML
   + business metrics; rollback on either.
4. **Sample Ratio Mismatch (SRM)** — if 50/50 split is actually
   51/49, the test is invalid. Fix: chi-square test on the
   bucket counts before reading the result.
5. **Carryover effect** — a user who saw model A in round 1
   behaves differently in round 2 even on model B. Fix:
   hash-based routing (we have this in §3) — a merchant ALWAYS
   sees the same model.

---

## 7. Cross-references

* Model registry + champion/challenger columns —
  `src/ml/registry.py:373` ✅ shipped.
* Drift detection (auto-rollback trigger) —
  `src/ml/drift.py:55` (DDM) + line 176 (ADWIN) ✅ shipped.
* Auto-remediation skeleton — `src/remediation/auto_heal.py`
  (event `drift_detected` handler triggers `promote_to_champion`).
* Latency target (p99 < 150ms) — see `docs/LATENCY_ENGINEERING.md`
  for the honest 100-200ms p99 today.
* Chaos + canary cadence — `docs/CHAOS_ENGINEERING.md` §4.
* RBI MRM §4.3 independent validation (each challenger is a
  red-team target) — `docs/RBI_MRM_MAPPING.md` row 2.

---

## Status

| # | Component | Status | Owner |
|---|-----------|--------|-------|
| 1 | `model_registry.is_champion` partial-unique index | ✅ shipped | `src/ml/registry.py:373` |
| 2 | `is_challenger` + `traffic_split` columns | ✅ shipped | `src/ml/registry.py:373` |
| 3 | `register_model(version, ..., champion)` API | ✅ shipped | `src/ml/registry.py:70` |
| 4 | `current_champion()` lookup | ✅ shipped | `src/ml/registry.py:343` |
| 5 | `scripts/canary_gate.py` (CI-only canary) | ✅ shipped | CI Quality workflow |
| 6 | `experiments` table (alembic 008) | 📋 architecture-future | future (this doc specs) |
| 7 | Runtime router (`src/api/routing.py`) | 📋 architecture-future | future (pseudo-code in §3) |
| 8 | Shadow / mirror queues (extend `src/stream/producer.py`) | 📋 architecture-future | future |
| 9 | Prom p99 + err-rate → auto-rollback | 📋 architecture-future | future (queries in §3) |
| 10 | Key-based routing (merchant tier → model) | 📋 architecture-future | future |
| 11 | Pre-registered sample size (anti-peeking) | 📋 architecture-future | future (Taylor 2025 §3.1) |
| 12 | SRM chi-square guard | 📋 architecture-future | future (Taylor 2025 §3.4) |

**Bottom line:** 5 of 12 shipped (registry columns, register_model,
current_champion, CI canary gate). 0 of the 5 runtime patterns
shipped (the table + router + Prom-rollback are all 📋 future).
The doc cites Taylor 2025, lists the 5 honest design-failure
modes (peeking, multi-compare, metric tunnel vision, SRM,
carryover), and proves we understand the production shape.
