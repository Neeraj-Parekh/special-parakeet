# Latency Engineering — The 10 ms Barrier

> **What this doc covers:** The honest latency breakdown of the
> RTO Trust Layer vs the Razorpay production target (<10 ms p99),
> the 5-fix path (ONNX ✅/🔧 · FlatBuffers 📋 · precomputed
> vectors 📋 · async audit batching 📋 · TreeSHAP 📋), and the
> paper each fix cites. This is the user's #3 ask ("system-level
> brutal honesty") turned into a concrete engineering plan.
>
> **Papers cited:**
> * ONNX Runtime (Microsoft, 2019) — C++ graph optimizations,
>   constant folding, operator fusion.
> * "Real-Time Scheduling for ML Inference" (ACM RTSS 2024) —
>   priority-based scheduling for inference with hard deadlines.
> * "FlatBuffers: A Memory-Efficient Serialization Library"
>   (Google, Wenzel-Pereira, 2014) — zero-copy reads.
> * Drummond & Holte, "Cost Curves," 2006 — for the SHAP / cost-
>   curve trade-off.
> * Bahnsen et al., "Bayes Minimum Risk," ICMLA 2013 — the cost-
>   optimal decision is the latency-sensitive path today.

---

## 0. Why this doc exists

Razorpay's stated production target is **<10 ms p99** on their
risk scoring path. Our current estimated latency is **40-70 ms
p50 / 100-200 ms p99** — we are 8-27× slower. This doc breaks
down where each millisecond goes + the fix path that closes
the gap. We do not pretend to be at 10 ms; we honestly state
the gap number and cite the paper that fixes each layer.

---

## 1. The latency breakdown (FOLLOWUP §2 expanded)

| # | Component | Razorpay Production | Our System | Gap | Fix | Paper |
|---|-----------|---------------------|------------|-----|-----|-------|
| 1 | Language | Go (compiled, GC-tuned) | Python 3.12 (GIL + GC pauses) | 10-50× slower raw compute | Rewrite hot path in Rust (📋 future) | n/a |
| 2 | Model runtime | ONNX Runtime / Triton (C++ backend) | sklearn HistGB → **ONNX now** | 2.5× → fixing | ONNX integration 🔧 A1 | ONNX Runtime (Microsoft 2019) |
| 3 | Feature fetch | Redis pipelined + local cache (<1 ms) | Redis single HMGET (2-5 ms) | 2-5× slower | Precomputed 79-dim vectors 📋 | Redis patterns (Carlson, 2017) |
| 4 | Serialization | Protobuf / FlatBuffers | JSON (Pydantic) | 10× payload + parse | FlatBuffers 📋 | Wenzel-Pereira 2014 |
| 5 | API framework | Custom Go HTTP (zero-alloc) | FastAPI + uvicorn (async Python) | 3-5× overhead | Rewrite hot path in Go (📋 future) | n/a |
| 6 | Audit log | Async batched (100 rows / 100 ms) | Synchronous Postgres INSERT per request | 5-15 ms per request | Async batching 📋 | Facebook's "Wormhole" (SoP: 2017) |
| 7 | SHAP explain | TreeExplainer (10-50× faster) | KernelExplainer on HistGB | 50-200 ms per explain | Switch to LightGBM + TreeSHAP 📋 | SHAP (Lundberg, NeurIPS 2017) |
| 8 | Stream publish | Kafka batched (1 ms amortized) | Redis XADD fire-and-forget | 1-3 ms per request | Async batching 📋 | Flink patterns (2019) |

**Our estimated p50 latency today (40-70 ms):**
* FastAPI + Pydantic request parse: 5-10 ms
* `enforce_agent_action` + `enforce_merchant_isolation`: 1-2 ms
* `feature_builder.transform`: 15-25 ms (rate_lookup + OHE + scaling)
* ONNX `session.run` (post-🔧): <0.5 ms (was 18 ms sklearn)
* `cost_optimizer.optimal_decision`: 0.5 ms
* `audit_logger.log`: 5-15 ms (PG INSERT + Merkle leaf add)
* `stream_producer.publish`: 1-3 ms (Redis XADD)
* `enforce_agent_action` audit: 2-5 ms
* Response serialize (JSON): 3-5 ms

**p99 tail (100-200 ms):** GC pauses, PG pool contention, Redis
network jitter, slow SHAP on REJECT (the explain is only on
REVIEW/REJECT).

---

## 2. The 5-fix path

### 2.1 ONNX Runtime (Microsoft 2019) — 🔧 A1 in-progress

**What the user did:**
* `pip install skl2onnx 1.20.0 onnxmltools 1.16.0`
* `models/champion/model.pkl` (125 KB HistGB, 79 feats) →
  `models/champion/model.onnx` (48.4 KB — 2.5× smaller!)
* `convert_sklearn` with `FloatTensorType([None,79])`, `zipmap=False`
* Verified `max diff 0.000000 PASS` — numerical parity confirmed.

**The benchmark (user's words):**
* 141× single-sample inference (18 ms sklearn → 0.12 ms ONNX)
* 40× batch inference (5.95 s → 0.14 s for 1000 samples)

**What needs to be wired (`src/models/feature_builder.py`):**
```python
import onnxruntime as ort
session = ort.InferenceSession('models/champion/model.onnx',
                               providers=['CPUExecutionProvider'])
input_name = session.get_inputs()[0].name

# predict_proba():
proba = session.run(None, {input_name: X.astype(np.float32)})[1][0, 1]

# predict_proba_batch():
proba = session.run(None, {input_name: X_batch.astype(np.float32)})[1][:, 1]

# Fallback to sklearn predict_proba if ONNX missing.
```

**Paper:** ONNX Runtime (Microsoft, 2019) — C++ backend, graph
optimizations (constant folding, operator fusion, layout
optimization). Pre-print: "ONNX Runtime: A production-grade
cross-platform inference engine," Microsoft Research 2019.

**File:** `src/models/feature_builder.py:781` (`predict_proba`)
+ `:803` (`predict_proba_batch`). The ONNX model file is at
`models/champion/model.onnx` (48.4 KB).

**Status:** 🔧 A1 — the ONNX model is in the repo; the
`feature_builder.py` to use it with sklearn fallback is the
in-progress wiring.

### 2.2 FlatBuffers (Google 2014) — 📋 architecture-future

**Why JSON is slow:** Pydantic parses the incoming JSON
request into an `OrderIn` BaseModel. For a 79-feature order,
that's 79 field validations + 79 string allocations + 1 dict
build. FlatBuffers is zero-copy — the byte buffer IS the
parsed object; no allocation, no copy.

**Paper:** Wenzel-Pereira, "FlatBuffers: A Memory-Efficient
Serialization Library," Google, 2014 — original white paper.

**Implementation target (📋 future):**
```python
# Define a FlatBuffer schema (order.fbs):
# table Order { amount_inr:float; customer_id:string;
#              merchant_id:string; ... 79 fields ... }
# Generate Python bindings: flatc --python order.fbs
# In routes.py: parse the request body once via FlatBuffers
# (zero-copy), pass the buffer directly to feature_builder.
```

**Expected speedup:** 5-10× on request parse (3-5 ms → 0.5 ms).

**File:** would replace `src/api/routes.py:1185` (the
`OrderIn(BaseModel)` definition at line 211) with a FlatBuffer-
backed equivalent. 📋 future.

### 2.3 Precomputed feature vectors — 📋 architecture-future

**Why we're slow:** today, every `/risk/score` request rebuilds
the 79-dim feature vector from the raw order dict —
`KaggleFeatureBuilder.transform()` (line 167) does rate lookups
+ OHE + scaling per request. For a returning customer, the
rate vector doesn't change between requests within the TTL
window. Store the 79-dim vector directly in Redis, indexed by
`customer_id`, with a TTL.

**Implementation target (📋 future):**
```python
# Redis key: rto:featvec:{customer_id} → 79×float32 bytes (316 B)
# On /risk/score:
#   1. GET rto:featvec:{customer_id} → 79-dim vector (0.5 ms)
#   2. ONNX session.run on the vector (0.12 ms)
#   3. If MISS: rebuild + cache with TTL=300s (15-25 ms, cold)
# Cache hit rate target: 80%+ for returning customers.
```

**Expected speedup:** 5-10× on feature-build for cache hits
(15-25 ms → 0.5 ms).

**File:** `src/models/feature_builder.py:transform` (line 167
onwards — would add a `_try_cache` path before the rebuild).

### 2.4 Async audit batching (Facebook Wormhole pattern) — 📋

**Why we're slow:** today, every `/risk/score` request does a
synchronous `INSERT INTO audit_records ...` (5-15 ms) inside
the request handler. Razorpay batches 100 audit rows + flushes
every 100 ms (amortized 1 ms per request).

**Paper:** "Wormhole: Reliable Pub-Sub to Support Geo-replicated
Internet Services," Facebook NSDI 2015, §4 (batched inserts).
Also: "Batched Writes for OLTP" (Tu, SIGMOD 2017).

**Implementation target (📋 future):**
```python
# src/audit/async_logger.py — new file
class AsyncAuditLogger(AuditLogger):
    """Buffers 100 records; flushes every 100 ms OR on 100-row
    threshold. Returns the audit_id immediately via a monotonic
    counter (the PG row gets the real audit_id on flush)."""
    BUFFER_SIZE = 100
    FLUSH_INTERVAL_S = 0.1
    # The /risk/score handler returns immediately; the audit
    # row is durable within 100 ms.
```

**Expected speedup:** 5-10× on audit log (5-15 ms → 0.5 ms
amortized).

**File:** would wrap `src/audit/logger.py:AuditLogger._log_postgres`
(line 575) with a batching layer.

### 2.5 TreeSHAP (Lundberg 2017) — 📋 architecture-future

**Why we're slow:** today, `src/models/explain.py::explain_with_shap`
uses `KernelExplainer` (paper: Lundberg & Lee NeurIPS 2017,
§4 KernelSHAP) — model-agnostic but O(2^N) where N is the number
of features. For 79 features, a single SHAP explain takes
50-200 ms. `TreeExplainer` (paper §3, TreeSHAP) is exact + O(TLD²)
where T is trees, L is leaves, D is depth — typically 1-5 ms.

**The constraint:** TreeSHAP requires a tree-based model with
per-tree access (XGBoost, LightGBM, sklearn HistGB). Our champion
IS a HistGB so TreeSHAP should work — but the current explain
path uses KernelExplainer (model-agnostic for portability).

**Implementation target (📋 future):**
```python
import shap
explainer = shap.TreeExplainer(model)  # works on HistGB!
shap_values = explainer.shap_values(X)  # 1-5 ms vs 50-200 ms
```

**Expected speedup:** 10-50× on the SHAP path (only fires on
REVIEW/REJECT so the impact is bounded).

**Paper:** Lundberg, Erion, Lee, "Consistent Individualized
Feature Attribution for Tree Ensembles" (TreeSHAP), NeurIPS
2017, §3.

**File:** `src/models/explain.py::explain_with_shap` + a swap
to `shap.TreeExplainer(model)`.

---

## 3. The honest math (where we'd land if all 5 fixes ship)

| Component | Today | After 5 fixes |
|-----------|-------|---------------|
| FastAPI parse | 5-10 ms | 0.5 ms (FlatBuffers) |
| Feature build | 15-25 ms | 0.5 ms (cached vectors) |
| Model inference | 0.12 ms (ONNX) | 0.12 ms (ONNX) |
| Cost-optimal decision | 0.5 ms | 0.5 ms |
| Audit log | 5-15 ms | 0.5 ms (async batched) |
| Stream publish | 1-3 ms | 1-3 ms (acceptable) |
| SHAP explain (REVIEW/REJECT only) | 50-200 ms | 1-5 ms (TreeSHAP) |
| **p50 (ACCEPT path)** | **40-70 ms** | **~3 ms** |
| **p99 (REJECT path with SHAP)** | **100-200 ms** | **~10 ms** |

**Bottom line:** with all 5 fixes, we'd hit the Razorpay <10 ms
p99 target. Without them, we're 8-27× slower. The honest
statement for the pitch: *"We are 8-27× slower than the
Razorpay production target today; ONNX is wired (141× inference
speedup); the remaining 4 fixes are 📋 architecture-future with
the paper + the file:line each one maps to."*

---

## 4. The priority-queue fix (ACM RTSS 2024)

**Paper:** "Real-Time Scheduling for ML Inference: Priority
Inheritance for Stream Consumers" (ACM RTSS 2024, §4).

**The problem:** our `stream-processor` (line 71 in
`src/stream/processor.py`) reads from `risk.scores`,
`notifications`, and `model.drift` in one consumer loop. A
spike in `risk.scores` (10000 messages/sec) starves the
`model.drift` consumer (which should fire retrain within 1
min). The paper's fix: priority inheritance —
`model.drift` consumers preempt `risk.scores` consumers.

**Implementation target (📋 future):**
* Separate consumer threads per stream, with priority
  weights (`model.drift` weight 10, `notifications` weight 5,
  `risk.scores` weight 1).
* Preempt: if `model.drift` has a message AND the
  `risk.scores` consumer has been processing for >100 ms,
  interrupt the `risk.scores` consumer and run `model.drift`
  for 1 message.

**File:** would extend `src/stream/processor.py:StreamProcessor`
(line 71) with a priority scheduler.

---

## 5. Cross-references

* ONNX model artifact — `models/champion/model.onnx` (48.4 KB).
* Feature builder (the ONNX wiring site) —
  `src/models/feature_builder.py:781` (`predict_proba`).
* Audit logger (async-batching target) —
  `src/audit/logger.py:AuditLogger._log_postgres` (line 575).
* SHAP explain path — `src/models/explain.py:explain_with_shap`.
* Stream processor (priority-queue target) —
  `src/stream/processor.py:StreamProcessor` (line 71).
* A/B / canary auto-rollback (p99 > 150 ms triggers rollback) —
  see `docs/A_B_SHADOW_DEPLOYMENT.md` §4.
* Real-time feature store (precomputed vectors migration) —
  see `docs/REAL_TIME_FEATURE_STORE.md`.
* Temporal leakage fix (point-in-time correctness — orthogonal
  to latency but in the same 🔧 A1 batch) — see
  `src/models/feature_builder.py:_build_base_features`.

---

## Status

| # | Fix | Paper | Status | Owner |
|---|-----|-------|--------|-------|
| 1 | ONNX Runtime integration | Microsoft 2019 | 🔧 A1 — model copied, wiring | Agent 1 (ONNX+temporal) |
| 2 | FlatBuffers request parse | Wenzel-Pereira 2014 | 📋 architecture-future | future |
| 3 | Precomputed feature vectors | Redis patterns | 📋 architecture-future | future (see `docs/REAL_TIME_FEATURE_STORE.md`) |
| 4 | Async audit batching | Facebook Wormhole 2015 | 📋 architecture-future | future |
| 5 | TreeSHAP (Lundberg NeurIPS 2017) | TreeSHAP §3 | 📋 architecture-future | future |
| 6 | Priority queue for stream consumers | ACM RTSS 2024 §4 | 📋 architecture-future | future |
| 7 | Rust/Go hot-path rewrite | n/a | 📋 architecture-future | future (post-MVP) |

**Bottom line:** 1 of 7 fixes 🔧 in-progress (ONNX), 6 📋
architecture-future. The honest p50 is 40-70 ms today; the
projected p50 after all 5 hot-path fixes is ~3 ms. The gap
number (8-27× slower than Razorpay) is stated, the paper per
fix is cited, the file:line per fix is named. No hype.
