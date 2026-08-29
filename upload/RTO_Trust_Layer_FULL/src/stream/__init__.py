"""Redis Streams streaming backbone for the RTO Trust Layer (Track F, Day 2).

Closes perceived-gap driver G2 (REST-only, no event/streaming backbone —
Microsoft has Eventstreams → Eventhouse → Activator) + §D item P7 (streaming
transforms) + §A item 18 (no streaming / message bus; Redis declared but
unused).

**Design principle: fire-and-forget publish.** After a decision is made and
the audit record is written, the API async-publishes to Redis Streams. If
Redis is down, the publish fails silently (logged to stderr but doesn't
block the response). This is NOT the full transactional outbox (V3 §10.3
prescribes outbox — a separate outbox table drained by a worker). For the
hackathon, fire-and-forget is pragmatic + demo-able. The full outbox is a
future enhancement (see worklog Day 2 Track F deferral list).

Five stream names per V2 §5 (decision: Redis Streams over Kafka per
``04-TECH-STACK-DECISIONS.md``: "V3 explicitly rejected Kafka as cargo-cult"):

    STREAM_RISK_SCORES     = "risk.scores"        # every POST /risk/score decision
    STREAM_AUDIT_RECORDS   = "audit.records"      # every audit hash-chain append
    STREAM_CASES_CREATED   = "cases.created"      # every REVIEW decision → case open
    STREAM_MODEL_DRIFT     = "model.drift"        # stream-processor anomaly alerts
                                                   # (consumed by Track G DDM/ADWIN)
    STREAM_NOTIFICATIONS   = "notifications"      # fan-out to merchant / agent

The producer is dual-mode: ``StreamProducer(None).publish(...)`` returns
``None`` silently so the 63 existing tests (which never set ``REDIS_URL``)
still pass without a Redis fixture. With ``REDIS_URL`` set, the producer
connects lazily on first ``publish`` via ``redis.from_url``.
"""

from src.stream.consumer import StreamConsumer, run_consumer
from src.stream.producer import (
    STREAM_AUDIT_RECORDS,
    STREAM_CASES_CREATED,
    STREAM_MODEL_DRIFT,
    STREAM_NOTIFICATIONS,
    STREAM_RISK_SCORES,
    StreamProducer,
)

__all__ = [
    "STREAM_AUDIT_RECORDS",
    "STREAM_CASES_CREATED",
    "STREAM_MODEL_DRIFT",
    "STREAM_NOTIFICATIONS",
    "STREAM_RISK_SCORES",
    "StreamConsumer",
    "StreamProducer",
    "run_consumer",
]
