"""Redis Streams consumer — XREADGROUP with consumer-group semantics.

Track F Day 2. The ``stream-worker`` docker-compose service runs
``python -m src.stream.consumer`` — drains ``risk.scores`` +
``cases.created`` (the two streams the API publishes to that have an
immediate consumer use-case). The ``stream-processor`` service runs
``python -m src.stream.processor`` (see ``processor.py``) and consumes
``risk.scores`` for the streaming-transforms pipeline (Microsoft
Eventhouse equivalent; TFX ``generate_data_statistics`` pattern).

Consumer-group semantics:
- ``XGROUP CREATE`` is idempotent (``MKSTREAM`` + ignore BUSYGROUP error)
  so the worker can be scaled to N replicas sharing the same group; each
  replica gets a unique consumer name (env-driven, defaults to ``worker-1``).
- ``XREADGROUP >`` reads only new messages; the consumer never re-reads
  already-delivered messages from the same group.
- ``XACK`` is called only after the handler returns — on handler exception
  the message stays PEL (Pending Entries List) so it can be claimed by
  another consumer via ``XCLAIM`` after the consumer timeout. (Full claim
  loop is deferred — see worklog; the hackathon demo tolerates stuck-PEL
  messages for now.)
"""
from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any, Callable

# Producer stream-name constants are imported here so consumer + producer
# share ONE source of truth for the 5 stream names (V2 §5).
from src.stream.producer import (
    STREAM_AUDIT_RECORDS,
    STREAM_CASES_CREATED,
    STREAM_RISK_SCORES,
)


class StreamConsumer:
    """Redis Streams consumer-group reader.

    ``consume()`` blocks indefinitely on ``XREADGROUP``, calling ``handler``
    per message + ``XACK``-ing on success. On handler exception, the message
    is NOT acked (stays in the PEL for ``XCLAIM`` re-delivery). On Redis
    connection failure, ``consume`` sleeps ``retry_seconds`` and retries —
    never propagates the exception (so the worker is restart-safe under
    Redis blips without a docker ``restart: unless-stopped`` cycle).
    """

    def __init__(
        self,
        redis_url: str,
        group: str = "rto-workers",
        consumer: str | None = None,
    ) -> None:
        if not redis_url:
            raise ValueError("redis_url is required for StreamConsumer")
        self.redis_url = redis_url
        self.group = group
        # Default consumer name is ``worker-<hostname-pid-suffix>`` so two
        # docker-compose replicas don't collide. Env override for explicit
        # naming (CI / debugging).
        self.consumer = consumer or os.environ.get(
            "STREAM_CONSUMER_NAME", f"worker-{os.getpid()}"
        )
        self.client: Any = None
        # Streams we've already ensured the group exists on (XGROUP CREATE
        # is idempotent but we don't want to swallow BUSYGROUP on every poll).
        self._group_streams: set[str] = set()
        # Set by SIGINT/SIGTERM handler; consume() checks it between polls.
        self._stop = False

    def _connect(self) -> Any:
        import redis  # type: ignore[import-not-found]

        if self.client is None:
            self.client = redis.from_url(self.redis_url, decode_responses=True)
        return self.client

    def _ensure_group(self, stream: str) -> None:
        """Idempotent ``XGROUP CREATE``. ``MKSTREAM`` creates the stream if
        it doesn't exist (with a single dummy entry that's immediately
        deleted). On ``BUSYGROUP`` the group already exists — ignore.
        """
        if stream in self._group_streams:
            return
        client = self._connect()
        try:
            client.xgroup_create(stream, self.group, id="0", mkstream=True)
        except Exception as e:
            # BUSYGROUP = group already exists. We can't import the typed
            # exception here (redis-py isn't installed in the sandbox), so
            # match on the class name string.
            if "BUSYGROUP" in type(e).__name__ or "BUSYGROUP" in str(e):
                pass
            else:
                print(
                    f"[consumer] xgroup_create {stream}/{self.group} failed: "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
        self._group_streams.add(stream)

    def consume(
        self,
        streams: list[str],
        handler: Callable[[str, dict], None],
        block_ms: int = 5000,
        retry_seconds: float = 2.0,
    ) -> None:
        """Block on ``XREADGROUP`` for ``streams``, call ``handler(stream,
        fields)`` per message, ``XACK`` on success.

        ``block_ms`` is the per-poll block duration (Redis Streams blocks
        up to this long if no new messages, then returns empty). 5s default
        is a good balance between immediate pickup + low idle CPU.

        ``retry_seconds`` is the sleep between failed polls when Redis is
        unreachable. Never propagates the exception — the worker just
        retries until Redis comes back.
        """
        # Install signal handlers so docker stop sends SIGTERM and we exit
        # cleanly (don't leave a half-acked PEL entry).
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        print(
            f"[consumer] {self.consumer} joining group={self.group} "
            f"streams={streams} block_ms={block_ms}",
            file=sys.stderr,
        )
        while not self._stop:
            try:
                client = self._connect()
                # Ensure the group exists on each stream up-front so
                # XREADGROUP doesn't NOGROUP on us.
                for s in streams:
                    self._ensure_group(s)
                # XREADGROUP reads from the LAST UNDELIVERED ID (``>``) — so
                # messages already delivered to this consumer (and acked)
                # are NOT re-read. Per-stream ID mapping is required by
                # redis-py's xreadgroup signature.
                resp = client.xreadgroup(
                    groupname=self.group,
                    consumername=self.consumer,
                    streamcounts={s: ">" for s in streams},
                    count=10,
                    block=block_ms,
                )
            except Exception as e:
                print(
                    f"[consumer] poll failed ({type(e).__name__}: {e}); "
                    f"retry in {retry_seconds}s",
                    file=sys.stderr,
                )
                # Drop the client so the next poll reconnects (in case the
                # connection is in a bad state).
                self.client = None
                time.sleep(retry_seconds)
                continue

            if not resp:
                # Normal idle — XREADGROUP timed out with no new messages.
                continue
            # resp is a list of (stream_name, [(msg_id, {fields}), ...]) tuples.
            for stream, messages in resp:
                for msg_id, fields in messages:
                    try:
                        handler(stream, dict(fields) if fields else {})
                        # Only XACK after handler success — on handler
                        # exception the message stays PEL for XCLAIM.
                        client.xack(stream, self.group, msg_id)
                    except Exception as e:
                        # Don't XACK — leave for another consumer to claim
                        # (or this one to retry on the next ``XCLAIM`` loop,
                        # which is a deferred enhancement).
                        print(
                            f"[consumer] handler raised on {stream}/{msg_id}: "
                            f"{type(e).__name__}: {e}",
                            file=sys.stderr,
                        )

        print(f"[consumer] {self.consumer} shutting down cleanly", file=sys.stderr)

    def _handle_signal(self, signum, frame) -> None:  # pragma: no cover
        print(f"[consumer] caught signal {signum}, draining...", file=sys.stderr)
        self._stop = True

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:  # pragma: no cover
                pass
            self.client = None


# --- Default handler for the run_consumer entrypoint ---------------------
# The default handler just logs to stderr. Real workers (Track G feedback
# loop, Track I dashboard, Track H notifications) install their own handler
# by importing StreamConsumer directly and calling ``consume`` with a custom
# callback. This default exists so ``python -m src.stream.consumer`` is
# demonstrable end-to-end: start the worker, POST /risk/score, see the
# message logged.


def _default_handler(stream: str, fields: dict) -> None:
    """Logs the message to stderr in a stable one-line format.

    The API publishes ``risk.scores`` and ``audit.records`` and (on REVIEW)
    ``cases.created``. This handler surfaces each one so ``docker compose
    logs stream-worker`` shows the event flow:
        [consumer] risk.scores: prediction_id=abc decision=REJECT score=0.812
        [consumer] audit.records: audit_id=aud_xyz prediction_id=abc
        [consumer] cases.created: case_id=case_xyz prediction_id=abc
    """
    # Compact one-line render: sort by key, drop the ts (always present +
    # already ISO 8601 sortable). Wrap values in ``key=value`` format
    # so it's grep-friendly.
    parts = [f"{k}={v}" for k, v in sorted(fields.items()) if k != "ts"]
    print(f"[consumer] {stream}: {' '.join(parts)}", file=sys.stderr)


def run_consumer() -> None:
    """Entrypoint for ``python -m src.stream.consumer``.

    The default consumer drains ``risk.scores``, ``audit.records``, and
    ``cases.created`` (the three streams the API publishes to). The
    ``model.drift`` stream is consumed by ``src.stream.processor`` (which
    has its own run loop — see ``processor.py``). The ``notifications``
    stream is reserved for Track H (Day 2) + Track I (Day 3); it's not
    drained yet to avoid double-processing — install a custom handler
    when wiring those tracks.
    """
    # Local import so the module is import-safe in CI environments without
    # the full app context (e.g. lint passes).
    from src.config import get_settings

    settings = get_settings()
    if not settings.redis_url:
        print(
            "[consumer] REDIS_URL not set — stream worker cannot start. "
            "Set REDIS_URL=redis://redis:6379 in the environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    consumer = StreamConsumer(settings.redis_url)
    streams = [STREAM_RISK_SCORES, STREAM_AUDIT_RECORDS, STREAM_CASES_CREATED]
    try:
        consumer.consume(streams, _default_handler)
    finally:
        consumer.close()


if __name__ == "__main__":
    run_consumer()
