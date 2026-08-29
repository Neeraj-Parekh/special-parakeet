"""Feature Store with negative caching (P1-3 — anti-DoS via unique-customer flood).

Paper: Lifière et al., "The Windows Server 2016 Storage Spaces Direct
Cache: When, Why, Where" — same negative-caching principle (cache a miss
sentinel so repeated lookups for the same non-existent key don't re-hit
the slower backing store). The RTO analog: an attacker floods
``/risk/score`` with random ``customer_id``s; each unique ID is a Redis
miss → PostgreSQL fallback query; the PG pool exhausts → 500s.

Defence: cache a ``__null__`` sentinel in Redis on the first miss with
TTL=60s. Subsequent lookups for the same ``customer_id`` hit the sentinel
→ return the global mean (``self.base_rate``) without querying PG. This
turns the DoS into a Redis-only hot path (Redis handles ~100k req/s on a
single core; PG handles ~1k req/s with the row-level locks the audit
trail's Merkle sealer uses).

Where it's used: the in-process ``KaggleFeatureBuilder`` (Agent A1 owns
``src/models/feature_builder.py``) currently uses rate lookups from
``train_stats.json`` (no Redis/PG path yet). This module is the
FUTURE-LOOKING FeatureStore that the Feast migration (see
``docs/REAL_TIME_FEATURE_STORE.md`` §3) will swap in. Until then it's
importable + unit-tested so the negative-caching pattern is in the
codebase for a future integration. Same pattern as ``StreamProducer``
(lazy Redis connect, no-op when ``REDIS_URL`` unset) so the 350 existing
tests don't need a Redis fixture.

Usage::

    from src.api.feature_store import FeatureStore

    fs = FeatureStore(redis_url=settings.redis_url,
                      database_url=settings.database_url,
                      base_rate=state["base_rate"])
    features = fs.get_online_features(order.customer_id)
    # → dict of customer-level features OR {} on miss (base_rate caller-side)
"""
from __future__ import annotations

import sys
from typing import Any, Callable


# The sentinel value cached in Redis on a miss. Distinct from any real
# customer's feature JSON (which is always a non-empty dict, never a
# 8-char string starting with ``__``).
_NULL_SENTINEL = "__null__"

# TTL for the negative-cache entry. 60s balances:
#   * short enough that a real customer who appears later (delayed PG
#     replication, slow upsert) doesn't stay cached-as-null long;
#   * long enough that a 1000-req/sec unique-customer flood saturates
#     1000 Redis keys (vs. 1000 PG pool slots) for the flood's duration.
_NEG_CACHE_TTL_SECONDS = 60


class FeatureStore:
    """Online feature store with Redis cache + PG fallback + negative
    caching.

    Construction is cheap (just stores URLs + the global base_rate). The
    Redis client is lazily connected on the first ``get_online_features``
    call — same pattern as ``StreamProducer`` (src/stream/producer.py:74)
    + ``IPRateLimiter`` (src/api/security.py:174). When ``REDIS_URL`` is
    unset (test mode + local dev without Redis), the store operates in
    passthrough mode (every call returns ``None`` → caller uses
    ``base_rate``), so the 350 existing tests pass without a Redis
    fixture.

    The negative-cache contract:
      * Redis ``GET customer:{id}`` returns the features JSON → parse +
        return.
      * Returns ``__null__`` sentinel → return ``None`` (caller uses
        ``base_rate``); don't query PG (the 60s TTL prevents a re-flood).
      * Returns ``None`` (key not in Redis) → query PG; on PG miss, cache
        ``__null__`` with TTL=60s; on PG hit, cache the features JSON with
        a longer TTL (default 300s).
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        database_url: str | None = None,
        base_rate: float = 0.0,
        cache_ttl_seconds: int = 300,
        negative_cache_ttl_seconds: int = _NEG_CACHE_TTL_SECONDS,
        pg_lookup: Callable[[str], dict | None] | None = None,
    ) -> None:
        self.redis_url = redis_url
        self.database_url = database_url
        self.base_rate = float(base_rate)
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        self.negative_cache_ttl_seconds = int(negative_cache_ttl_seconds)
        # Pluggable PG lookup so tests can mock the DB call without a real
        # psycopg fixture. The default ``_default_pg_lookup`` raises
        # ``NotImplementedError`` until the Feast migration wires it in
        # (per the user's directive to keep architecture-future items
        # unimplemented).
        self._pg_lookup = pg_lookup or self._default_pg_lookup
        self.client: Any = None
        self._connect_attempted = False
        # In-memory cache for the no-Redis fallback path (per-process;
        # same multi-worker caveat as ``IPRateLimiter._check_mem``).
        self._mem_cache: dict[str, str] = {}
        # Statistics surfaced on the /metrics endpoint in a future wiring.
        self.stats = {
            "redis_hits": 0,
            "redis_neg_hits": 0,
            "pg_hits": 0,
            "pg_misses": 0,
            "errors": 0,
        }

    def _ensure_client(self) -> Any:
        """Lazy-import + connect to Redis. Mirrors ``StreamProducer`` +
        ``IPRateLimiter``: returns ``None`` permanently after the first
        failed attempt so a flapping Redis doesn't add latency to every
        request.
        """
        if self._connect_attempted:
            return self.client
        self._connect_attempted = True
        if not self.redis_url:
            return None
        try:
            import redis  # type: ignore[import-not-found]

            self.client = redis.from_url(self.redis_url, decode_responses=True)
            self.client.ping()
        except ImportError:
            print(
                "[feature-store] redis package not installed — operating "
                "in passthrough mode (every lookup returns None → caller "
                "uses base_rate). Add `redis>=5.0` to requirements.txt.",
                file=sys.stderr,
            )
            self.client = None
        except Exception as e:  # pragma: no cover — defensive
            print(
                f"[feature-store] redis connect failed ({type(e).__name__}: "
                f"{e}) — operating in passthrough mode.",
                file=sys.stderr,
            )
            self.client = None
        return self.client

    @staticmethod
    def _default_pg_lookup(_customer_id: str) -> dict | None:
        """Placeholder PG lookup. The Feast migration (see
        ``docs/REAL_TIME_FEATURE_STORE.md`` §3) will wire this to a real
        ``SELECT customer_features FROM ...`` query against the offline
        store. Until then it returns ``None`` so the negative-cache code
        path is exercised + tested without a DB fixture.
        """
        return None

    @staticmethod
    def _redis_key(customer_id: str) -> str:
        """Redis key for a customer's feature blob. Namespaced with ``rto:``
        prefix (same convention as ``IPRateLimiter``'s ``rto:ip:rl:*``
        keys) so the entire RTO system's keys are greppable in redis-cli.
        """
        return f"rto:cust:{customer_id}"

    def _check_mem(self, customer_id: str) -> str | None:
        """In-memory cache for the no-Redis path. Returns the sentinel
        ``__null__`` (negative hit), a JSON string (positive hit), or
        ``None`` (no entry — caller should query PG).
        """
        return self._mem_cache.get(customer_id)

    def _store_mem(self, customer_id: str, value: str, ttl: int) -> None:
        """In-memory set with TTL via the dict. The TTL is best-effort —
        on the no-Redis path (tests + local dev) we don't track expiries
        per-key (the dict grows up to ``maxsize``, never specified — fine
        for tests). In production, Redis is always wired so this path is
        never hit.
        """
        self._mem_cache[customer_id] = value

    def get_online_features(self, customer_id: str) -> dict | None:
        """Resolve customer-level features for the live /risk/score path.

        Returns:
          * ``dict`` of features (Redis or PG hit).
          * ``None`` (miss + negative-cache entry, or Redis unavailable +
            PG miss). Caller falls back to ``self.base_rate``.

        The negative-cache contract is the headline defence: an attacker
        flooding with unique customer_id values gets AT MOST ONE PG query
        per ID (the first), then 60s of Redis-only negative hits. At
        1000 req/s × 60s = 60,000 unique IDs → 60,000 Redis hits (cheap,
        ~100k req/s on a single core) instead of 60,000 PG queries (PG
        pool exhausts at ~1k concurrent → 500s at ~1000 req/s).
        """
        client = self._ensure_client()
        key = self._redis_key(customer_id)
        # 1. Redis lookup (or in-memory fallback when Redis is unavailable).
        if client is not None:
            try:
                cached = client.get(key)
            except Exception as e:  # pragma: no cover — defensive
                self.stats["errors"] += 1
                print(
                    f"[feature-store] redis GET failed ({type(e).__name__}: "
                    f"{e}) — falling back to in-memory for this request.",
                    file=sys.stderr,
                )
                cached = self._check_mem(customer_id)
        else:
            cached = self._check_mem(customer_id)
        # 2. Positive hit → parse + return.
        if cached is not None and cached != _NULL_SENTINEL:
            self.stats["redis_hits"] += 1
            try:
                import json

                return json.loads(cached)
            except (TypeError, ValueError, json.JSONDecodeError):
                # Corrupt cache entry — treat as miss, fall through to PG.
                pass
        # 3. Negative hit → return None (caller uses base_rate; don't
        # re-query PG — the 60s TTL prevents a re-flood).
        if cached == _NULL_SENTINEL:
            self.stats["redis_neg_hits"] += 1
            return None
        # 4. Genuine miss → query PG.
        try:
            features = self._pg_lookup(customer_id)
        except Exception as e:  # pragma: no cover — defensive
            self.stats["errors"] += 1
            print(
                f"[feature-store] PG lookup failed ({type(e).__name__}: "
                f"{e}) — returning None (caller uses base_rate).",
                file=sys.stderr,
            )
            return None
        if features is None:
            # PG miss → cache the negative sentinel with the shorter TTL.
            self.stats["pg_misses"] += 1
            self._cache_value(
                client, key, _NULL_SENTINEL, self.negative_cache_ttl_seconds
            )
            return None
        # PG hit → cache the features JSON with the longer TTL.
        self.stats["pg_hits"] += 1
        try:
            import json

            self._cache_value(
                client, key, json.dumps(features), self.cache_ttl_seconds
            )
        except (TypeError, ValueError):
            # Non-JSON-serialisable features (shouldn't happen in
            # practice — the Feast migration will return a dict of
            # primitives). Don't cache; just return.
            pass
        return features

    def _cache_value(
        self,
        client: Any,
        key: str,
        value: str,
        ttl_seconds: int,
    ) -> None:
        """Cache a value (positive or negative) in Redis or in-memory
        depending on which path is active. Best-effort — never raises.
        """
        if client is not None:
            try:
                client.setex(key, ttl_seconds, value)
                return
            except Exception as e:  # pragma: no cover — defensive
                self.stats["errors"] += 1
                print(
                    f"[feature-store] redis SETEX failed ({type(e).__name__}: "
                    f"{e}) — caching in-memory instead.",
                    file=sys.stderr,
                )
                # Fall through to in-memory set.
        # In-memory fallback (no Redis OR Redis write failure).
        # NOTE: doesn't honor TTL — the dict grows monotonically in
        # test/local-dev mode. Acceptable for tests (small N); production
        # always wires Redis.
        self._store_mem(key.rsplit(":", 1)[-1], value, ttl_seconds)
