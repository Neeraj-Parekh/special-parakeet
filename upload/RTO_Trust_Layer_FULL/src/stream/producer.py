"""Redis Streams producer — fire-and-forget publish to the 5 RTO streams.

Track F Day 2. Closes §A item 18 (Redis declared in ``docker-compose.yml``
but unused by the API) + driver G2 (REST-only, no event/streaming backbone).

**Fire-and-forget contract**: if ``REDIS_URL`` is unset OR Redis is
unreachable, ``publish()`` returns ``None`` and the API response is unaffected.
This is the pragmatic hackathon pattern (V3 §10.3 prescribes a full
transactional outbox table drained by a worker — deferred; the fire-and-forget
publish here is the demo-able minimum).

Lazy connect: ``redis.from_url`` is only called on the first ``publish``,
so importing this module never requires Redis to be reachable. This matters
because the 63 existing tests import ``src.api.routes`` which imports
``StreamProducer`` — without lazy connect, every test would require Redis.

Field values MUST be ``str | bytes`` per Redis Streams' ``XADD`` contract;
the API call sites in ``src/api/routes.py`` already pass strings (UUIDs,
decision names, ISO timestamps, floats stringified). If a non-str scalar
slips through, we coerce to ``str()`` here so the publish never raises
``redis.exceptions.DataError`` and silently drops the message instead.
"""
from __future__ import annotations

import sys
from typing import Any

# --- Stream name constants (V2 §5). Keep in sync with consumer.py. ----------
STREAM_RISK_SCORES = "risk.scores"
STREAM_AUDIT_RECORDS = "audit.records"
STREAM_CASES_CREATED = "cases.created"
STREAM_MODEL_DRIFT = "model.drift"
STREAM_NOTIFICATIONS = "notifications"

# Top-level flag the test suite can flip to True after monkey-patching
# fakeredis in place; avoids the cost of a real `import redis` when no test
# needs it. The producer still imports ``redis`` lazily inside ``__init__``
# so the module is import-safe in CI environments without redis installed.
_REDIS_AVAILABLE: bool | None = None


class StreamProducer:
    """Fire-and-forget Redis Streams publisher.

    Usage in the API (``src/api/routes.py``)::

        state["stream"] = StreamProducer(settings.redis_url)
        ...
        state["stream"].publish(STREAM_RISK_SCORES, {
            "prediction_id": prediction_id,
            "order_id": order.order_id,
            "decision": decision,
            "score": float(p_rto),
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    The publish call is non-blocking w.r.t. the API response — if Redis is
    down, the failure is logged to stderr and ``publish()`` returns ``None``;
    the API continues + returns its normal JSON body. The downstream
    consumers (``stream-worker`` service running ``python -m src.stream.consumer``
    + ``stream-processor`` service running ``python -m src.stream.processor``)
    drain the streams asynchronously via ``XREADGROUP``.
    """

    def __init__(self, redis_url: str | None) -> None:
        self.redis_url = redis_url
        # Lazy: don't open the connection until the first publish. This makes
        # ``StreamProducer(None)`` a no-op (the 63 existing tests pass with no
        # Redis fixture) and ``StreamProducer("redis://...")`` only connects
        # when there's actually a message to send.
        self.client: Any = None
        self._connect_attempted = False

    def _ensure_client(self) -> Any:
        """Lazily import + connect to Redis. Idempotent — if the first
        attempt raised, we don't retry on every publish (the worker is
        typically either up for the duration or down for the duration).
        """
        if self._connect_attempted:
            return self.client
        self._connect_attempted = True
        if not self.redis_url:
            return None
        try:
            import redis  # type: ignore[import-not-found]

            self.client = redis.from_url(self.redis_url, decode_responses=False)
        except ImportError:
            # redis-py not installed (e.g. sandbox without ``redis>=5.0`` in
            # the venv). Print to stderr so the operator notices in `docker
            # compose logs api`; publish() returns None silently afterward.
            print(
                "[stream] redis package not installed — publish() will be a no-op. "
                "Add `redis>=5.0` to requirements.txt and `pip install`.",
                file=sys.stderr,
            )
            self.client = None
        except Exception as e:  # pragma: no cover — defensive, never raise
            print(
                f"[stream] redis connect failed ({type(e).__name__}: {e}) — "
                "publish() will be a no-op until the worker is restarted.",
                file=sys.stderr,
            )
            self.client = None
        return self.client

    def publish(self, stream: str, fields: dict) -> str | None:
        """Fire-and-forget ``XADD`` to a Redis Stream.

        Returns the Redis-generated message ID (e.g. ``"1700000000000-0"``)
        on success, or ``None`` on any failure (Redis down, package missing,
        ``REDIS_URL`` unset). Never raises — the API caller doesn't see the
        publish outcome, by design (fire-and-forget).

        Field values are coerced to ``str`` so a non-string scalar (e.g. a
        float score) doesn't raise ``DataError``. Redis Streams require all
        field values to be ``bytes`` or ``str``.
        """
        client = self._ensure_client()
        if client is None:
            return None
        # Coerce all values to str. This is the documented Redis Streams
        # contract (``XADD key field value [field value ...]`` — values are
        # strings). Numbers, bools, None all stringify cleanly; nested dicts
        # would JSON-stringify but the API call sites don't pass any.
        try:
            safe_fields = {
                str(k): ("" if v is None else str(v))
                for k, v in fields.items()
                if k is not None
            }
            return client.xadd(stream, safe_fields)
        except Exception as e:
            # Never raise — fire-and-forget. Log + return None so the API
            # response path is unaffected. Common failure modes: Redis
            # crashed mid-request, network blip, OOM on Redis side.
            print(
                f"[stream] publish to {stream} failed ({type(e).__name__}: {e})",
                file=sys.stderr,
            )
            return None

    def close(self) -> None:
        """Close the Redis connection if one was opened. Safe to call
        multiple times. Called from the API lifespan shutdown so the worker
        doesn't leak a Redis connection across hot-reloads.
        """
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # pragma: no cover — best-effort shutdown
                pass
            self.client = None
        self._connect_attempted = False
