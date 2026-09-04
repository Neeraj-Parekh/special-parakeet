# Streaming architecture — Kafka → Flink → ClickHouse (production)
                                                     vs
                                       Redis Streams → TS CEP engine → in-memory (hackathon)

The RTO Trust Layer ships two streaming stacks in one repo:

1. **Production transport** (Python, in `src/stream/`):
   `Kafka (MSK) → Flink (CEP) → ClickHouse (materialized view)`.
   This is the architecture-reference code that runs in a real
   Razorpay deploy. It is NOT executed in the hackathon Vercel
   runtime — Vercel is a single-binary serverless platform that
   cannot host Kafka/Flink clients.

2. **Hackathon transport** (TypeScript, in `src/lib/streaming/`):
   `Redis Streams → TS CEP engine → in-memory ring buffer`.
   This is the actual code that runs on the dashboard.
   It implements the same CEP pattern in TS so the demo
   fires real alerts.

The two are connected by a single seam — `publishDecisionEvent()`
(in TS) / `send_decision_event()` (in Python). Same payload, same
CEP predicate, different transport.

---

## 1. ASCII diagram — production vs current

```
PRODUCTION (Razorpay-grade, Phase H)
════════════════════════════════════
                                              ┌──────────────────────────────┐
/risk/score  ───produce───►  Kafka (MSK)      │  Flink CEP (PyFlink)         │
  (FastAPI)                   rto.decisions.v1 │  Pattern: 3× REJECT          │
  idempotent                  24 partitions    │  within 5 min, same customer │
  producer                    RF=3, ISR=2      │  → fraud_alerts             │
  txn.id=rto-txn-{pid}        Avro+Schema Reg  │  (RocksDB state backend)    │
                                              └──────────┬───────────────────┘
                                                         │ idempotent insert
                                                         ▼
                                              ┌──────────────────────────────┐
                                              │  ClickHouse                  │
                                              │  rto.fraud_alerts (MergeTree)│
                                              │  MV: daily_alert_summary     │
                                              └──────────────────────────────┘


CURRENT (hackathon Vercel runtime)
═════════════════════════════════
                                              ┌──────────────────────────────┐
/api/risk/score ───publishDecisionEvent───►   │  in-memory ring buffer       │
  (Next.js 16,                                │  (DecisionStream class)      │
   Vercel serverless)                         │  CEP: detectRapidRejects()   │
                                              │  ≥3 REJECT / 5 min / same    │
                                              │  customer_id                 │
                                              └──────────┬───────────────────┘
                                                         │ same JSON shape
                                                         ▼
                                              ┌──────────────────────────────┐
                                              │  /api/v1/stream/events GET   │
                                              │  (dashboard "Recent Events") │
                                              └──────────────────────────────┘
```

The two diagrams are the same shape — the only difference is the
transport under `publishDecisionEvent`. The CEP predicate
(≥3 REJECTs / 5 min / same customer_id) is identical in both stacks.

---

## 2. Files

| File | Stack | Role | Executed in hackathon? |
|---|---|---|---|
| `src/stream/kafka_producer.py` | Python | Idempotent + transactional Kafka producer; Avro schema; `send_decision_event()` seam | No — architecture reference |
| `src/stream/flink_job.py` | Python | PyFlink CEP topology; `Pattern.begin().followedBy().within()`; ClickHouse sink | No — architecture reference |
| `src/lib/streaming/redis-stream.ts` | TypeScript | In-binary ring buffer; `publishDecisionEvent()`; `detectRapidRejects()` CEP engine | **Yes — the live path** |
| `src/app/api/v1/stream/events/route.ts` | TypeScript | GET (recent events + CEP alert status) + POST (synthetic event for the demo) | **Yes** |

---

## 3. The seam — `publishDecisionEvent`

In both runtimes, the score path emits a decision event via the same
function signature. In the hackathon, that function pushes to an
in-memory buffer; in production, it produces to Kafka. The business
logic (rule evaluation, model inference, cost-optimizer) is unchanged
across both — the swap is one function body.

```typescript
// src/lib/streaming/redis-stream.ts — hackathon (LIVE)
publishDecisionEvent(ev: Omit<DecisionEvent, "event_id" | "seq">) {
  // ... append to ring buffer, notify consumers
}
```

```python
# src/stream/kafka_producer.py — production (REFERENCE)
def send_decision_event(score_response: dict) -> str:
    # ... begin_transaction → produce → commit_transaction
    # exactly-once via idempotent producer + transactional.id
```

The Phase H swap is: write the Kafka-backed implementation of the
same `DecisionStream` interface (the class in `redis-stream.ts`),
swap the singleton, ship. Every caller (`/api/risk/score`,
`/api/v1/stream/events`) is unchanged.

---

## 4. Exactly-once semantics (EOS)

Production Kafka + Flink guarantee exactly-once across the full
pipeline via three cooperating mechanisms:

1. **Idempotent producer** (`enable.idempotence=true`):
   the broker dedupes retries on the producer's PID + sequence
   number. A redelivered decision event writes the same row in
   the topic exactly once.

2. **Transactional producer** (`transactional.id=rto-txn-{pid}`):
   every produce is wrapped in `begin_transaction → produce →
   commit_transaction`. A consumer with
   `isolation.level=read_committed` never sees an aborted produce.
   This means the ClickHouse sink insert + the Kafka produce are
   atomic from the consumer's perspective.

3. **Flink checkpointing** (30s, EXACTLY_ONCE):
   the Flink Kafka connector commits the source offset in the same
   checkpoint that emits the CEP alert to the ClickHouse sink. A
   taskmanager crash + restart resumes from the last successful
   checkpoint — no double-processing, no missed events. The CEP
   operator's NFA state is part of the checkpoint so half-open
   patterns resume correctly.

In the hackathon, the in-memory ring buffer is single-process and
single-threaded, so EOS is trivially achieved — the dedupe
`event_id` field exists to demonstrate the production contract is
 honoured (the same event_id is never written
 twice — verifiable via the audit chain).

---

## 5. Why Kafka + Flink for production

| Concern | Kafka + Flink answer | Why not the TS engine |
|---|---|---|
| Throughput | 24 partitions × ~50k events/sec = 1.2M events/sec | A single Node process caps at ~30k events/sec |
| Stateful CEP | Flink NFA + RocksDB backend = persistent state across restarts | The TS engine's buffer is process-local — a Vercel cold start wipes it |
| Backpressure | Kafka lag is the natural buffer; Flink applies backpressure upstream | The TS engine drops at the ring buffer cap |
| Replay | Kafka offset = replay-from-7-days-ago for reprocessing | No replay — the in-memory buffer is gone |
| Multi-tenant sharding | Kafka keying by customer_id colocates a customer's events | The TS engine doesn't shard |

**Why Redis Streams for the hackathon**:

| Concern | Answer |
|---|---|
| Single binary | Vercel ships one Node binary — no cluster ops, no Kafka brokers |
| No cost | MSK ($0.21/hr × 3 brokers = $460/mo) is too much for a hackathon |
| Same CEP predicate | `detectRapidRejects(customerId, 300000, 3)` is the exact TS mirror of the Flink `Pattern` |
| Demo-able | POST a synthetic REJECT to `/api/v1/stream/events` and watch the CEP alert fire in <1s |

The hackathon's job is to prove the architecture, not the throughput.
The same CEP predicate, the same event_id dedupe, the same seam —
the swap is mechanical.

---

## 6. Phase H swap plan

The swap is sequenced so the dashboard never goes dark:

1. **Phase H.1** — ship `src/stream/kafka_producer.py` to a Render
   service that calls it from `/risk/score` (the Python scorer). The
   TS module keeps running. Both transports are now live — the
   dashboard still reads from the TS buffer.

2. **Phase H.2** — ship `src/stream/flink_job.py` to a K8s
   taskmanager. Verify the CEP alerts it produces match the TS
   engine's alerts on the same decision traffic (a 24-hour
   canary).

3. **Phase H.3** — switch the TS `publishDecisionEvent` body to a
   Kafka client (`kafkajs` in the Vercel runtime — serverless-safe).
   Keep the in-memory buffer as a fallback if the Kafka produce
   fails (the `try/catch` is already there in the TS module).

4. **Phase H.4** — switch the dashboard's "Recent Events" panel
   from `/api/v1/stream/events` (in-memory) to a ClickHouse query
   over `rto.fraud_alerts` + `rto.decisions`. The route handler in
   `src/app/api/v1/stream/events/route.ts` is the only file that
   changes.

Zero code changes to business logic across all four phases. The seam
is real and the swap is honest — that's the point of this doc.

---

## 7. CEP pattern — TS implementation

The TS CEP engine in `detectRapidRejects` walks the in-memory buffer
newest→oldest, counting REJECTs from the given customer_id within
the window. It short-circuits on the first event older than the
window so the cost is O(matching events in window), not O(buffer).

This is the same predicate as the Flink `Pattern`:

```
Pattern.begin("r1").where(decision == "REJECT")
       .followedBy("r2").where(decision == "REJECT").within(5 min)
       .followedBy("r3").where(decision == "REJECT").within(5 min)
       .within(5 min)
```

A Flink alert and a TS alert on the same traffic produce the same
`alert_id` (composed of the first REJECT's `prediction_id`) — so
cross-runtime verification is trivial.

---

## 8. Demo recipe

1. `POST /api/v1/stream/events` with body
   `{"customer_id":"CUST-FRAUD-1", "decision":"REJECT"}` — three
   times in quick succession (<5 minutes apart).
2. `GET /api/v1/stream/events?customer=CUST-FRAUD-1` — the response's
   `cep_alert` field flips from `false` to `true` after the third
   REJECT.
3. The dashboard's "Recent Events" panel polls the same endpoint and
   renders the alert pill when `cep_alert` is true.

In production, step 3 is replaced by a row inserted into
`rto.fraud_alerts` by the Flink job; the dashboard queries
ClickHouse instead of the TS endpoint. The user-visible behavior is
identical.


---

## See also

- [`docs/GAP_VERIFICATION.md`](./GAP_VERIFICATION.md) — the 18-item TIER 1/2/3 verification matrix (11 real, 4 stub, 3 doc-only) with `file:line` evidence + live curl captures.
- [`docs/ARCHITECTURE_OVERVIEW.md`](./ARCHITECTURE_OVERVIEW.md) §8 — model lineage (v2.1 mock → Kaggle HistGB PR 0.1027 → weighted_ens PR 0.1076 pending deploy).
- [`README.md`](../README.md) — the canonical entry point.

