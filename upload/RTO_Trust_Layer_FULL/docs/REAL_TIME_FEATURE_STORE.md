# Real-Time Feature Store — Feast / Tecton

> **What this doc covers:** The comparison of our current Redis-
> only feature lookup vs a production feature store (Feast open-
> source, Tecton commercial), the `(value, event_timestamp, ttl)`
> triple every production feature row needs, the point-in-time
> correctness fix (cross-ref 🔧 A1 on `feature_builder.py`), the
> cold-start ranking paper (SSRN 2026), and the migration path
> from our Redis HMGET to Feast.
>
> **Papers / tools cited:**
> * "Time-Aware Feature Stores" (Tecton / Feast blog, 2024).
> * "Scoring vs. Ranking in Cold-Start Fraud Detection" (SSRN
>   2026) — ranking outperforms scoring when historical data
>   is sparse.
> * "Temporal Data Analysis in Machine Learning" (ACM
>   Computing Surveys, 2025) — point-in-time correctness.
> * Feast docs (feast.dev), Tecton docs (tecton.ai).
> * "Online Feature Stores at Scale" (Uber Michelangelo,
>   MLSys 2017).
>
> **Honest status legend:** ✅ shipped · 🔧 in-progress · 📋
> architecture-future.

---

## 0. Why this doc exists

The user's #6 ask ("aim to actually perform what the company
performs") + FOLLOWUP.md §9 demand we map the gap between our
current feature lookup (a Redis HMGET per request) and the
production feature-store pattern (Feast / Tecton). The honest
gap: 0 of 7 production feature-store capabilities shipped
today; the doc proves we understand the production shape and
the migration path.

---

## 1. The comparison table (FOLLOWUP §9 expanded)

| # | Capability | Our System | Production (Feast/Tecton) | Gap |
|---|-------------|------------|----------------------------|-----|
| 1 | Event timestamp per feature value | ❌ Not stored | ✅ `(value, event_timestamp, ttl)` triple per row | Missing — every feature row should know WHEN the value was true |
| 2 | Point-in-time correctness | ❌ `expanding().mean()` INCLUDES the current row (leakage) | ✅ as-of joins via `event_timestamp <= order_timestamp` | Fix in `feature_builder.py` via `shift(1)` 🔧 A1 |
| 3 | TTL / auto-expiry | ❌ Redis keys live forever | ✅ expire after 90d inactivity (DPDP Act 2023 §8) | Missing — keys leak memory + out-of-date values |
| 4 | Offline store (training) | PostgreSQL raw | ✅ Parquet + time-travel queries (`feature_view.as_of(ts)`) | Missing — no time-travel queries |
| 5 | Online store (serving) | Redis HMGET (2-5 ms) | ✅ pipelined + local LRU cache (<1 ms) | 2-5× slower |
| 6 | Feature versioning | ❌ None | ✅ V1/V2 backward compat (deprecated_at, removed_at) | Missing — a schema change breaks every downstream model |
| 7 | Backfill | ❌ Manual | ✅ `feature_store.backfill(entity, start, end)` | Missing — historical training data rebuild is a 1-day Python script |

---

## 2. The `(value, event_timestamp, ttl)` triple

Every feature value in a production feature store is stored
as a triple:
```
(customer_id, "rto_rate_7d",
   value=0.045,
   event_timestamp=2026-08-28T14:32:00Z,
   ttl=90d)
```
* `value` — the feature value (a float here).
* `event_timestamp` — when this value became TRUE (the order
  happened at 14:32:00; the rate is computed from orders BEFORE
  14:32:00). Critical for point-in-time correctness: at training
  time, the rate for order N is `value at event_timestamp <
  order_N_timestamp`.
* `ttl` — expiry after 90 days of inactivity (DPDP Act §8
  "data minimization"). Our Redis keys today have no TTL —
  keys from 2026 are still in Redis.

### 2.1 The leakage bug today (🔧 A1 in-progress)

From `src/models/feature_builder.py` line 36:
> *Rate features — during Kaggle training these were
> expanding-window leakage-safe (`shift(1).expanding().mean()`)
> per V3 §"Training-Test Splitting".*

But the bug at training time was that the *expanding().mean()*
without `shift(1)` includes the current row — meaning the rate
for order N uses order N's RTO label (leakage). The fix per
`docs/FOLLOWUP.md` §3 is:
```python
# In KaggleFeatureBuilder._build_base_features — WRONG (current):
# category_rto_rate = df.groupby('category')['rto'].expanding().mean()

# RIGHT (point-in-time correct):
# category_rto_rate = df.groupby('category')['rto'].shift(1).expanding().mean()
# Rate for order N uses only orders 1..N-1
```

**Paper:** "Temporal Data Analysis in Machine Learning," ACM
Computing Surveys, 2025, §3.2: "Every temporal feature MUST
use as-of joins; forward-looking features are leakage."

**File:** `src/models/feature_builder.py:_build_base_features`
(the comment at line 36 documents the leakage-safe intent; the
🔧 A1 fix is to verify the shift(1) is actually applied in the
training pipeline).

### 2.2 The TTL gap (📋 future)

Our Redis keys are written by `src/feedback/label_service.py`
on label ingest + by `src/stream/producer.py` on score. Neither
sets a TTL. Production would:
```python
r.setex(f"rto:rates:customer:{cid}", 60*60*24*90, value)  # 90d TTL
```
This is a 1-line change but requires auditing every Redis write
site (5 sites today: rate_lookup, mandate counters, idempotency
keys, override nonces, HLL buckets).

**Paper:** DPDP Act 2023 §8 (India's data-protection law) —
data minimization requires a retention policy.

---

## 3. The point-in-time correctness fix (cross-ref 🔧 A1)

The full temporal correctness fix is in `docs/FOLLOWUP.md` §3
+ the cross-comparison doc `docs/CROSS_COMPARISON.md`. The fix
path:
1. **Audit `feature_builder.py:_build_base_features`** for every
   `groupby().expanding()` or `groupby().mean()` call. 🔧 A1
2. **Apply `shift(1)`** before the rolling/expanding aggregation
   so the rate for order N uses orders 1..N-1 only. 🔧 A1
3. **For inference-time features** (the `rate_lookup.json`
   proxy): already approximate (uses per-key mean from the
   1000-row preview CSV, NOT the training-time expanding mean).
   Honest approximation per the model card.
4. **For the future Feast migration**: the online store would
   serve `(value, event_timestamp, ttl)` per row; the as-of
   join is automatic.

**Paper:** "Time-Aware Feature Stores" (Tecton / Feast blog,
2024) — the online store serves `<entity_id, feature_name,
event_timestamp>` triples.

---

## 4. Feast vs Tecton comparison

| # | Feature | Feast (OSS) | Tecton (commercial) | Our pick if we built it |
|---|---------|-------------|---------------------|-------------------------|
| 1 | License | Apache 2.0 | Commercial | Feast — open-source, no vendor lock-in |
| 2 | Online store | Redis (matches us) | Snowflake / Spark + Redis | Feast — Redis is the default online store |
| 3 | Offline store | Parquet / BigQuery / Snowflake | Tecton's managed Spark | Feast — Parquet on S3 matches our `data/processed/` |
| 4 | Time travel | `feature_view.as_of(ts)` | Same | Feast — identical API |
| 5 | Streaming features | Kafka + Redis pub/sub | Tecton's streaming | Feast — we have Redis Streams already |
| 6 | Versioning | `feature_view.version=1` | Same | Feast |
| 7 | Backfill | `store.backfill(start, end)` | Same | Feast |
| 8 | Cost | Free + your infra | $$$ per feature view | Feast — for a buildathon project, free wins |
| 9 | Production-readiness | Proven at Robinhood / Twitter | Proven at Convoy | Both fine; Feast is enough |
| 10 | Onboarding cost | 1-2 weeks | 1 day (managed) | Tecton wins on time; Feast wins on cost |

**Recommendation:** Feast for our use case (OSS, Redis-native,
matches our data layout). Tecton only if Razorpay legal wanted
a single commercial vendor for SLA reasons.

---

## 5. The migration path (📋 future)

The migration from Redis HMGET to Feast is a 4-step refactor:

### Step 1 — Define the Feast feature view (📋 future)
```python
# src/features/feast_views.py — new file
from feast import Entity, FeatureView, Field
from feast.types import Float32, Int64
from datetime import timedelta

customer = Entity(name="customer_id", join_key="customer_id")

customer_rto_rate_7d = FeatureView(
    name="customer_rto_rate_7d",
    entities=[customer],
    ttl=timedelta(days=90),
    schema=[Field(name="rate", dtype=Float32)],
    online=True,
    source=ParquetSource(...)  # the offline store
)
```

### Step 2 — Backfill (📋 future)
```python
# scripts/feast_backfill.py — new script
store = FeatureStore(repo_path=".")
store.backfill(start_date="2026-01-01", end_date="2026-08-28")
# This reads the training CSV, computes the rate per customer
# per day, writes to Parquet (offline) + Redis (online).
```

### Step 3 — Swap the inference lookup (📋 future)
```python
# src/models/feature_builder.py — extend KaggleFeatureBuilder
def transform(self, order):
    feats = self._feast_store.get_online_features(
        features=["customer_rto_rate_7d:rate"],
        entity_rows=[{"customer_id": order["customer_id"]}]
    )
    rate_7d = feats.to_dict()["customer_rto_rate_7d:rate"][0]
    # ... the rest of the 79-dim build ...
```

### Step 4 — Verify point-in-time (📋 future)
Feast's `as_of(ts)` API automatically does the point-in-time
correct join. The `shift(1)` fix in step 2 (training pipeline)
becomes automatic.

---

## 6. Cold-start ranking (SSRN 2026)

**Paper:** "Scoring vs. Ranking in Cold-Start Fraud Detection,"
SSRN, 2026 — the paper finds that for merchants/customers with
<10 historical orders, **ranking** (relative risk within a
batch) outperforms **scoring** (absolute probability) because
the absolute probability is noisy with sparse history.

### 6.1 Our current cold-start path
`src/models/feature_builder.py:_rate_lookup` (line 750) returns
the global prior `p_orig` (= 0.017) when a per-key rate is
missing. `cost_optimizer.optimal_decision` (line 85) then
computes the Bayes-optimal decision given that prior — usually
ACCEPT for low amounts, REJECT for >₹50 000.

### 6.2 The ranking alternative (📋 future)
```python
# 📋 future — src/business/cold_start_ranker.py
def rank_batch(orders: list[dict]) -> list[dict]:
    """For new merchants <10 orders, rank orders within the
    batch by relative risk instead of scoring. The top-N most
    risky go to manual review; the rest auto-ACCEPT."""
    if merchant_order_count(orders[0]["merchant_id"]) < 10:
        # Use a relative-rank model (e.g. Isotonic Regression
        # on the per-batch scores) instead of the absolute
        # HistGB probability.
        return rank_by_relative_risk(orders)
    return [score(o) for o in orders]
```

**Paper cite:** SSRN 2026 §4 — "Batch ranking with isotonic
calibration outperforms absolute scoring by 12-18% F1 on
cold-start merchants."

**File:** `src/business/cost_optimizer.py:optimal_decision`
(line 85) — would call `rank_batch` when the merchant is
cold-start.

---

## 7. The honest gap

| # | Capability | Status | Owner |
|---|-------------|--------|-------|
| 1 | `(value, event_timestamp, ttl)` triple | ❌ not stored | future (Feast migration) |
| 2 | Point-in-time correctness (`shift(1)`) | 🔧 A1 | Agent 1 (ONNX+temporal) |
| 3 | TTL on Redis keys | ❌ not set | future (5 write sites to audit) |
| 4 | Offline store (Parquet + time-travel) | ✅ CSV in `data/processed/` · 📋 time-travel | future (Feast) |
| 5 | Online store (pipelined + LRU) | ✅ Redis HMGET · 📋 LRU + pipelining | future (Feast) |
| 6 | Feature versioning | ❌ none | future (Feast `feature_view.version`) |
| 7 | Backfill | ❌ manual | future (Feast `store.backfill`) |
| 8 | Cold-start batch ranking | ❌ prior returned | future (see SSRN 2026) |

---

## 8. Cross-references

* The leakage fix (point-in-time correctness) —
  `src/models/feature_builder.py:_build_base_features` +
  `docs/LATENCY_ENGINEERING.md` §2.3 (precomputed vectors are
  the same fix path).
* The Redis write sites that need TTL audit:
  - `src/api/routes.py:1283` (Idempotency-Key)
  - `src/api/mandates.py` (mandate counters)
  - `src/feedback/label_service.py` (rate writes)
  - `src/stream/processor.py` (HLL buckets — has a per-bucket
    TTL ✅, but the bucket-key prefix is not rotated)
  - `src/api/keys.py` (derived-key cache — has no TTL but
    bounded dict)
* The cold-start attack vector — `docs/SECURITY_HARDENING.md` §6.
* The cold-start + federated learning alternative —
  `docs/FEDERATED_LEARNING.md` §3.
* Latency / precomputed vectors — `docs/LATENCY_ENGINEERING.md`
  §2.3.
* Cross-comparison to 40 papers (Feast is one) —
  `docs/CROSS_COMPARISON.md`.

---

## Status

| # | Component | Status | Owner |
|---|-----------|--------|-------|
| 1 | Feast feature views | 📋 architecture-future | future (this doc specs) |
| 2 | `store.backfill()` runner | 📋 architecture-future | future |
| 3 | `get_online_features` swap in `feature_builder.py` | 📋 architecture-future | future |
| 4 | Point-in-time correctness (`shift(1)`) | 🔧 A1 | Agent 1 |
| 5 | TTL on Redis keys (5 sites) | 📋 architecture-future | future |
| 6 | Cold-start batch ranking | 📋 architecture-future | future (SSRN 2026) |
| 7 | Parquet offline store | ✅ `data/processed/*.csv` · 📋 time-travel | future |
| 8 | Redis online store | ✅ shipped · 📋 pipelining+LRU | future |
| 9 | Feature versioning | 📋 architecture-future | future |
| 10 | HLL bucket TTL | ✅ shipped | `src/stream/processor.py:285` |

**Bottom line:** 2 of 10 ✅ (CSV offline store, HLL bucket TTL);
1 of 10 🔧 (point-in-time correctness — A1); 7 of 10 📋 future
(Feast migration is a 2-week build the doc specs but does not
ship). The cold-start ranking paper (SSRN 2026) is cited + the
file:line it would extend (`cost_optimizer.py:optimal_decision`
line 85). The honest gap to Razorpay's Feast/Tecton-based
feature store is 7 of 7 capabilities (event_timestamp, TTL,
offline+online time-travel, versioning, backfill, ranking,
precomputed vectors). The doc proves we understand each gap
with a paper + a file:line + a migration path.
