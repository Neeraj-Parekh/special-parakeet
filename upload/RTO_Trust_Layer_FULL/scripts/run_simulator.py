#!/usr/bin/env python3
"""Multi-source ingest simulator — Task 12-e.

Generates realistic cross-source traffic for the 4 RTO Trust Layer data
sources (ecommerce, mobile, callcenter, atm) and publishes the events
to one of three sinks:

  1. **Redis Streams** (preferred) — publishes each source's native
     event dict to the corresponding ``ingest.{source}`` stream. The
     ``stream-processor`` (``src/stream/processor.py``) currently
     consumes ``risk.scores`` (post-decision audit records); the
     ``ingest.{source}`` streams are NEW raw-event streams for future
     per-source enrichment jobs. ``StreamProducer.publish()`` is
     fire-and-forget — if Redis is down the publish is a no-op and
     the simulator's main loop continues uninterrupted.

  2. **HTTP POST to /risk/score** (fallback) — when Redis isn't
     available, the simulator normalizes each source-specific event
     to the unified ``OrderIn`` schema (via the source module's
     ``normalize()`` function) and POSTs it to the existing
     ``/risk/score`` endpoint with the appropriate ``X-Channel``
     header. The API then runs the full decision pipeline (mandate
     check → rules engine → cost-optimizer → audit → stream publish)
     on the event. Requires the API to be running on ``--api-url``
     (default: ``http://localhost:8000``) and ``--scorer-key`` to
     be valid.

  3. **Stdout (dry-run)** — when neither Redis nor the API are
     available (or the ``--dry-run`` flag is set), the simulator
     prints each event as JSON to stdout. This is the safest mode
     for CI / local dev — the simulator is fully functional without
     any external service.

Usage:
    # 5-second smoke test at 10 events/sec, no infra needed:
    python scripts/run_simulator.py --duration 5 --rate 10 --dry-run

    # Realistic 60-second run, publishing to Redis (fallback to HTTP):
    python scripts/run_simulator.py --duration 60 --rate 5

    # Single-source (e-commerce only) at higher rate, 15% RTO-injected:
    python scripts/run_simulator.py --source ecommerce --rate 20 --rto-rate 0.15

    # Correlated cross-source chains (20% of events share a customer):
    python scripts/run_simulator.py --correlated --duration 30 --rate 3

The simulator is safe to run with no Redis and no API — it falls
through to stdout and exits cleanly.

Exit codes:
    0  — simulator completed (or stopped cleanly on SIGINT)
    1  — hard failure (e.g. argparse error, unhandled exception
         during setup). Per-event publish/post errors do NOT cause
         a non-zero exit — the simulator is best-effort by design.

Source: Kandula 2021 (Payment_Type as a discriminator feature for
fraud detection). The simulator's per-source event generation +
X-Channel header surfaces the channel discriminator on every audit
record → per-channel drift detection via TFX generate_data_statistics.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from typing import Any

# Make ``src/`` importable when running as a script (no install needed).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingest.simulator_data import (  # noqa: E402
    DEFAULT_SOURCE_WEIGHTS,
    SOURCE_TO_STREAM,
    generate_atm_event,
    generate_callcenter_event,
    generate_correlated_chain,
    generate_customer,
    generate_ecommerce_order,
    generate_mobile_event,
    inject_rto_risk,
    normalize_for_source,
    pick_source,
)

# ---------------------------------------------------------------------------
# Publisher abstraction.
# ---------------------------------------------------------------------------

class _StdoutPublisher:
    """Print events as JSON to stdout. Used when neither Redis nor the
    API is available (or ``--dry-run`` is set)."""

    def __init__(self) -> None:
        self.count = 0

    def publish(self, source: str, stream: str, event: dict, orderin: dict) -> None:
        self.count += 1
        print(json.dumps({
            "seq": self.count,
            "source": source,
            "stream": stream,
            "order_id": orderin.get("order_id"),
            "amount_inr": orderin.get("amount_inr"),
            "payment_method": orderin.get("payment_method"),
            "channel": source,
            "event_native": event,
            "orderin": orderin,
        }, default=str))


class _RedisPublisher:
    """Publish source-native events to Redis Streams via StreamProducer.

    Uses the existing ``src.stream.producer.StreamProducer`` (lazy
    connect — safe to construct even when Redis is down; ``publish()``
    returns ``None`` silently on failure).
    """

    def __init__(self, redis_url: str) -> None:
        from src.stream.producer import StreamProducer
        self.producer = StreamProducer(redis_url)
        self.redis_url = redis_url
        # Probe — actually PING Redis so we know it's reachable. The
        # StreamProducer._ensure_client() returns a non-None client
        # even when Redis is unreachable (because ``redis.from_url()``
        # is itself lazy — the connection happens on the first command).
        # Without this probe, the simulator would silently no-op every
        # publish + never fall through to the HTTP fallback.
        client = self.producer._ensure_client()
        if client is None:
            raise RuntimeError(
                f"Redis at {redis_url} unavailable (or redis-py not installed)"
            )
        try:
            # Actually connect — PING is the cheapest Redis command that
            # forces a TCP connect. Time-boxed to 1 second so the
            # simulator doesn't hang on a slow/unreachable Redis.
            client.ping()
        except Exception as e:
            # Reset so the next publisher construction attempt isn't
            # short-circuited by the cached _connect_attempted flag.
            self.producer._connect_attempted = False
            self.producer.client = None
            raise RuntimeError(
                f"Redis at {redis_url} PING failed: {type(e).__name__}: {e}"
            ) from e

    def publish(self, source: str, stream: str, event: dict, orderin: dict) -> None:
        # StreamProducer.publish coerces values to str — but JSON-
        # stringify the event dict first so the stream record carries
        # the structured payload (not Python's repr).
        fields = {
            "source": source,
            "order_id": orderin.get("order_id") or "",
            "customer_id": orderin.get("customer_id") or "",
            "amount_inr": str(orderin.get("amount_inr") or ""),
            "payment_method": orderin.get("payment_method") or "",
            "channel": source,
            "event_json": json.dumps(event, default=str),
            "orderin_json": json.dumps(orderin, default=str),
            "ts": str(time.time()),
        }
        self.producer.publish(stream, fields)


class _HttpPublisher:
    """POST normalized OrderIn to /risk/score with X-Channel header.

    Used when Redis is unavailable but the FastAPI app is running.
    Requires the scorer-scope API key for Authorization.
    """

    def __init__(self, api_url: str, scorer_key: str) -> None:
        try:
            import requests  # noqa: F401
        except ImportError as e:
            raise RuntimeError(f"requests library not installed: {e}") from e
        # Probe — actually issue a HEAD to the API root so we know
        # whether it's up. Fail fast so the simulator can fall back
        # to stdout instead of timing out on every event.
        import requests as _r
        try:
            _r.head(api_url, timeout=2, allow_redirects=True)
        except Exception as e:
            raise RuntimeError(f"API at {api_url} unreachable: {e}") from e
        self.api_url = api_url.rstrip("/")
        self.scorer_key = scorer_key
        self._r = _r

    def publish(self, source: str, stream: str, event: dict, orderin: dict) -> None:
        # The /risk/score endpoint accepts OrderIn-conformant JSON.
        # The X-Channel header sets the audit record's channel field.
        channel_map = {
            "ecommerce": "ecommerce",
            "mobile": "mobile",
            "callcenter": "call_center",
            "atm": "atm",
        }
        headers = {
            "Authorization": f"Bearer {self.scorer_key}",
            "X-Channel": channel_map.get(source, "ecommerce"),
            "Content-Type": "application/json",
        }
        url = f"{self.api_url}/risk/score"
        try:
            resp = self._r.post(url, json=orderin, headers=headers, timeout=5)
            if resp.status_code != 200:
                print(
                    f"[http] {orderin.get('order_id')} → HTTP {resp.status_code}: "
                    f"{resp.text[:200]}",
                    file=sys.stderr,
                )
        except Exception as e:
            print(
                f"[http] {orderin.get('order_id')} → ERROR {type(e).__name__}: {e}",
                file=sys.stderr,
            )


def _build_publisher(args: argparse.Namespace) -> Any:
    """Build the publisher based on args + availability.

    Priority: --dry-run > Redis > HTTP > stdout.
    """
    if args.dry_run:
        print("[simulator] --dry-run mode — printing events to stdout", file=sys.stderr)
        return _StdoutPublisher()

    # Try Redis first.
    if not args.no_redis:
        try:
            pub = _RedisPublisher(args.redis_url)
            print(
                f"[simulator] ✅ connected to Redis at {args.redis_url} — "
                f"publishing to streams {list(SOURCE_TO_STREAM.values())}",
                file=sys.stderr,
            )
            return pub
        except Exception as e:
            print(
                f"[simulator] ⚠️  Redis unavailable ({type(e).__name__}: {e}); "
                f"trying HTTP fallback...",
                file=sys.stderr,
            )

    # Try HTTP fallback.
    if not args.no_http:
        try:
            pub = _HttpPublisher(args.api_url, args.scorer_key)
            print(
                f"[simulator] ✅ connected to API at {args.api_url} — "
                f"POSTing normalized OrderIn to /risk/score with X-Channel header",
                file=sys.stderr,
            )
            return pub
        except Exception as e:
            print(
                f"[simulator] ⚠️  API at {args.api_url} unreachable "
                f"({type(e).__name__}: {e}); falling back to stdout.",
                file=sys.stderr,
            )

    # Final fallback — stdout.
    print(
        "[simulator] ⚠️  no Redis, no API — printing events to stdout. "
        "Use --dry-run to silence this warning.",
        file=sys.stderr,
    )
    return _StdoutPublisher()


# ---------------------------------------------------------------------------
# Event generation.
# ---------------------------------------------------------------------------

def _generate_event(
    source: str,
    correlated: bool,
    rto_rate: float,
    customer: dict | None = None,
    correlated_order: dict | None = None,
) -> tuple[dict, dict]:
    """Generate one source-specific event + its OrderIn-normalized form.

    Args:
        source: One of "ecommerce", "mobile", "callcenter", "atm".
        correlated: If True, the event uses the provided customer +
            correlated_order (for cross-source correlation).
        rto_rate: Probability [0,1] of injecting RTO risk factors into
            e-commerce orders. Ignored for non-ecommerce sources.
        customer: Customer-context dict (for correlation). If None,
            a fresh customer is generated.
        correlated_order: An e-commerce order dict to tie the mobile/
            callcenter event to (for correlation). Ignored for
            ecommerce/atm sources.

    Returns:
        A 2-tuple ``(event_dict, orderin_dict)``.
    """
    cust = customer or generate_customer()
    if source == "ecommerce":
        event = generate_ecommerce_order(cust)
        # RTO injection applies only to e-commerce orders.
        if rto_rate > 0 and random.random() < rto_rate:
            event = inject_rto_risk(dict(event))
    elif source == "mobile":
        event = generate_mobile_event(cust, correlated_order if correlated else None)
    elif source == "callcenter":
        event = generate_callcenter_event(cust, correlated_order if correlated else None)
    elif source == "atm":
        event = generate_atm_event(cust)
    else:
        raise ValueError(f"unknown source: {source!r}")
    orderin = normalize_for_source(source, event)
    return event, orderin


# ---------------------------------------------------------------------------
# Main loop.
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RTO Trust Layer — multi-source ingest simulator (Task 12-e)",
    )
    parser.add_argument(
        "--duration", type=float, default=60.0,
        help="Duration in seconds to run (default: 60). Use 0 for infinite.",
    )
    parser.add_argument(
        "--rate", type=float, default=5.0,
        help="Events per second total across all sources (default: 5).",
    )
    parser.add_argument(
        "--source",
        choices=["ecommerce", "mobile", "callcenter", "atm", "all"],
        default="all",
        help="Single source to generate (default: all, weighted).",
    )
    parser.add_argument(
        "--correlated", action="store_true",
        help="Generate correlated cross-source chains (~20% of events "
             "share a customer across sources).",
    )
    parser.add_argument(
        "--rto-rate", type=float, default=0.15,
        help="Fraction of e-commerce orders with RTO risk injected "
             "(0-1, default: 0.15).",
    )
    parser.add_argument(
        "--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        help="Redis URL for StreamProducer (default: env REDIS_URL or "
             "redis://localhost:6379).",
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("RTO_API_URL", "http://localhost:8000"),
        help="FastAPI URL for HTTP fallback (default: env RTO_API_URL "
             "or http://localhost:8000).",
    )
    parser.add_argument(
        "--scorer-key", default=os.environ.get("RTO_SCORER_KEY", "score-demo-key"),
        help="Scorer-scope API key for HTTP fallback Authorization header "
             "(default: env RTO_SCORER_KEY or score-demo-key).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip Redis + HTTP probes; print events to stdout.",
    )
    parser.add_argument(
        "--no-redis", action="store_true",
        help="Skip the Redis probe (force HTTP fallback).",
    )
    parser.add_argument(
        "--no-http", action="store_true",
        help="Skip the HTTP fallback probe (force stdout if Redis down).",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility (default: None = system entropy).",
    )
    args = parser.parse_args(argv)

    # Seed the global random — affects all generator calls.
    import random as _random_mod
    if args.seed is not None:
        _random_mod.seed(args.seed)

    # Build publisher.
    publisher = _build_publisher(args)

    # SIGINT handler — graceful shutdown (drains the in-flight event
    # then exits the main loop).
    stop_requested = {"flag": False}

    def _sigint(signum, frame):
        print(
            f"\n[simulator] received signal {signum} — draining...",
            file=sys.stderr,
        )
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _sigint)

    # Correlation state — keep a small pool of recent customers so a
    # mobile event can pick up the e-commerce order that the same
    # customer placed a few events earlier in the same run.
    recent_orders_by_customer: dict[str, dict] = {}

    interval = 1.0 / args.rate if args.rate > 0 else 1.0
    start = time.monotonic()
    event_count = 0
    rto_count = 0
    source_counts: dict[str, int] = {s: 0 for s in DEFAULT_SOURCE_WEIGHTS}

    print(
        f"[simulator] starting: duration={args.duration}s rate={args.rate}/sec "
        f"source={args.source} correlated={args.correlated} rto_rate={args.rto_rate}",
        file=sys.stderr,
    )

    while (args.duration == 0 or time.monotonic() - start < args.duration) \
            and not stop_requested["flag"]:
        # Pick source.
        if args.source == "all":
            source = pick_source()
        else:
            source = args.source

        # For correlated chains, occasionally emit the full 4-event
        # chain (ecommerce → mobile → callcenter → atm) sharing one
        # customer. The chain represents a realistic customer journey.
        if args.correlated and source == "ecommerce" and _random_mod.random() < 0.20:
            # Emit a correlated chain: 4 events sharing one customer.
            customer = generate_customer()
            chain = generate_correlated_chain(customer)
            # Optionally RTO-inject the e-commerce order in the chain.
            if args.rto_rate > 0 and _random_mod.random() < args.rto_rate:
                chain[0] = ("ecommerce", inject_rto_risk(dict(chain[0][1])))
            for src, ev in chain:
                orderin = normalize_for_source(src, ev)
                stream = SOURCE_TO_STREAM.get(src, "ingest.unknown")
                publisher.publish(src, stream, ev, orderin)
                event_count += 1
                source_counts[src] = source_counts.get(src, 0) + 1
                if src == "ecommerce" and orderin.get("payment_method") == "COD" \
                        and orderin.get("amount_inr", 0) >= 15000:
                    rto_count += 1
                # Honor the rate between chain events too.
                time.sleep(interval)
            continue

        # Otherwise — single uncorrelated event (or correlated-with-
        # recent-order mobile/callcenter event).
        customer = generate_customer()
        correlated_order = None
        if args.correlated and source in ("mobile", "callcenter"):
            # ~50% of the time, correlate with a recent e-commerce order
            # from a previously-seen customer (simulates the customer
            # tracking their order on mobile after placing it).
            if recent_orders_by_customer and _random_mod.random() < 0.50:
                cid = _random_mod.choice(list(recent_orders_by_customer.keys()))
                customer = {"customer_id": cid, **{
                    k: v for k, v in generate_customer().items()
                    if k != "customer_id"
                }}
                # Re-derive city_tier from the new city.
                from src.ingest.simulator_data import _city_to_tier
                customer["city_tier"] = _city_to_tier(customer["city"])
                correlated_order = recent_orders_by_customer[cid]

        event, orderin = _generate_event(
            source=source,
            correlated=args.correlated,
            rto_rate=args.rto_rate,
            customer=customer,
            correlated_order=correlated_order,
        )

        # Track recent e-commerce orders for cross-source correlation.
        if source == "ecommerce":
            recent_orders_by_customer[customer["customer_id"]] = event
            # Bound the pool — keep only the last 50 customers.
            if len(recent_orders_by_customer) > 50:
                # Remove an arbitrary (oldest-insertion-order) entry.
                oldest = next(iter(recent_orders_by_customer))
                recent_orders_by_customer.pop(oldest, None)

        # Publish.
        stream = SOURCE_TO_STREAM.get(source, "ingest.unknown")
        publisher.publish(source, stream, event, orderin)
        event_count += 1
        source_counts[source] = source_counts.get(source, 0) + 1
        if source == "ecommerce" and orderin.get("payment_method") == "COD" \
                and orderin.get("amount_inr", 0) >= 15000:
            rto_count += 1

        # Progress report every 100 events.
        if event_count % 100 == 0:
            elapsed = int(time.monotonic() - start)
            print(
                f"  [{elapsed}s] {event_count} events published — "
                f"source={source} rto_injected={rto_count}",
                file=sys.stderr,
            )

        time.sleep(interval)

    elapsed = int(time.monotonic() - start)
    print(
        f"\n[simulator] ✅ complete: {event_count} events in {elapsed}s "
        f"({event_count / max(elapsed, 1):.1f}/sec)",
        file=sys.stderr,
    )
    print("[simulator] source breakdown:", file=sys.stderr)
    for src, n in source_counts.items():
        print(f"  {src:12} → {n} events", file=sys.stderr)
    print(f"[simulator] RTO-injected orders: {rto_count}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
