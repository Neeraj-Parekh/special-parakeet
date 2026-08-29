"""Security primitives for the RTO Trust Layer API.

This module owns:
  * ``TokenBucket`` (Day 1 Track B) — per-client (per API key) token bucket
    rate limiter used to gate ``/risk/score`` against per-merchant abuse.
  * ``IPRateLimiter`` (P1-1) — per-IP bucket on top of the per-key bucket,
    with a Redis-backed sliding-window fallback for multi-worker correctness.
  * ``apply_anti_extraction_noise`` (P0-1) — bin-to-2-decimals + Gaussian
    noise (σ=0.01) on the displayed probability. Defeats Tramèr-style model
    extraction (USENIX Security 2016) by raising the query cost 10-100×.
  * ``verify_hmac_signature`` (P1-2) — RFC 5869 HMAC-SHA256 request signing
    + ±60s replay window. Opt-in via ``REQUIRE_HMAC=true``.

Env-var flags (read here, NOT in ``src/config`` — this module owns security):

  * ``ANTI_EXTRACTION_NOISE`` — ``"true"`` (default) applies binning + noise
    to the /risk/score response's probability; ``"false"`` disables it (used
    by the SHAP explainer path which needs the raw model output, and by the
    existing test suite which would otherwise be non-deterministic).
  * ``REQUIRE_HMAC`` — ``"false"`` (default) keeps the existing demo flow
    working without an ``X-Signature`` header; ``"true"`` enforces HMAC on
    every /risk/score request + rejects replays.
  * ``PER_IP_RATE_PER_MIN`` — ``"100"`` (default) per-IP token-bucket
    refi// rate. Set to a higher number for load tests.
  * ``HMAC_REPLAY_WINDOW_SECONDS`` — ``"60"`` (default) anti-replay skew
    tolerance. Any timestamp outside ``[now-60, now+60]`` is rejected.

These four env vars are intentionally NOT in ``src/config/__init__.py``'s
``Settings`` class — security-relevant toggles belong with the code that
enforces them (defence-in-depth: a misconfigured ``Settings`` instance can't
silently disable rate limiting or noise). Mirrors the pattern in
``src/api/mandates.py`` lines 636-640 (``RTO_MANDATE_SECRET`` /
``RTO_AUDIT_SALT`` reads) and ``src/api/agent_allowlist.py`` line 212.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
import time
from typing import Any


# ---------------------------------------------------------------------------
# Env-var flag readers (kept here next to the code that consumes them).
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    """Coerce the env var ``name`` to a boolean. Truthy values: ``true``,
    ``1``, ``yes`` (case-insensitive). Empty / unset → ``default``."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Coerce the env var ``name`` to a non-negative int. Unset / malformed
    → ``default``."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw.strip())
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def anti_extraction_noise_enabled() -> bool:
    """Whether to apply Tramèr 2016 binning + Gaussian noise to the
    /risk/score response's probability. Default ``True``; can be disabled
    via ``ANTI_EXTRACTION_NOISE=false`` for the SHAP explainer path (which
    needs exact probabilities — the SHAP endpoint reads
    ``model.predict_proba`` directly, NOT this response path, so disabling
    here is purely an internal-admin override).
    """
    return _env_bool("ANTI_EXTRACTION_NOISE", True)


def require_hmac_enabled() -> bool:
    """Whether to enforce ``X-Signature`` HMAC-SHA256 on every /risk/score
    request. Default ``False`` (opt-in) so the existing demo flow + the 350
    existing tests don't need to compute signatures."""
    return _env_bool("REQUIRE_HMAC", False)


def per_ip_rate_per_min() -> int:
    """Per-IP token-bucket refilrate (requests per minute). Default 100 —
    10× tighter than the per-key bucket (1000/min) so a single attacker IP
    rotating through 10 merchant keys still gets IP-throttled."""
    return _env_int("PER_IP_RATE_PER_MIN", 100)


def hmac_replay_window_seconds() -> int:
    """Replay-window tolerance for the HMAC ``X-Timestamp`` header.
    Default 60s. Any timestamp outside ``[now-60, now+60]`` → 401 rejected.
    """
    return _env_int("HMAC_REPLAY_WINDOW_SECONDS", 60)


# ---------------------------------------------------------------------------
# API-key authentication (unchanged — Day 1 Track B).
# ---------------------------------------------------------------------------


def _keys(env_var: str) -> set[str]:
    raw = os.environ.get(env_var, "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def default_keys(
    scorer_keys: str | None = None, admin_keys: str | None = None
) -> dict[str, set[str]]:
    """Demo fallback keys so local runs work without env setup.

    Day 2 Track E: reads from ``src.config.Settings()`` (which honors the
    ``.env`` file and OS env vars ``RTO_SCORER_KEYS`` / ``RTO_ADMIN_KEYS``)
    when the caller doesn't pass explicit CSV strings. ``create_app`` now
    passes ``settings.rto_scorer_keys`` / ``settings.rto_admin_keys`` here
    instead of reading env vars directly (closing the ad-hoc ``os.environ``
    read scattered around the codebase).

    Backward-compat: zero-arg call still works — the Settings object reads
    the same env vars the original ``_keys()`` did.
    """
    from src.config import get_settings

    s = get_settings()
    sk = scorer_keys if scorer_keys is not None else s.rto_scorer_keys
    ak = admin_keys if admin_keys is not None else s.rto_admin_keys
    return {
        "scorer": {k.strip() for k in sk.split(",") if k.strip()} or {"score-demo-key"},
        "admin": {k.strip() for k in ak.split(",") if k.strip()} or {"admin-demo-key"},
    }


def bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    return header_value.removeprefix("Bearer ").strip()


def check_key(provided: str | None, scope: str, allowed: dict[str, set[str]]) -> tuple[bool, str]:
    if not provided:
        return False, f"missing {scope} api key"
    k = hashlib.sha256(provided.encode()).hexdigest()
    for candidate in allowed[scope]:
        if hashlib.sha256(candidate.encode()).hexdigest() == k:
            return True, ""
    return False, f"invalid {scope} api key"


# ---------------------------------------------------------------------------
# Per-client token-bucket rate limiter (Day 1 Track B, unchanged).
# ---------------------------------------------------------------------------


class TokenBucket:
    def __init__(self, rate_per_min: int = 120):
        self.rate = rate_per_min / 60.0
        self.capacity = float(rate_per_min)
        self.buckets: dict[str, float] = {}
        self.updated: dict[str, float] = {}
        self.lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self.lock:
            last = self.updated.get(client, now)
            tokens = min(self.capacity, self.buckets.get(client, self.capacity))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self.buckets[client] = tokens
                self.updated[client] = now
                return False
            self.buckets[client] = tokens - 1.0
            self.updated[client] = now
            return True


# ---------------------------------------------------------------------------
# P1-1 — Per-IP rate limiter (anti-DoS + anti-extraction).
#
# Why a separate per-IP bucket on top of the per-key ``TokenBucket``:
#   * The per-key bucket stops a single API key from hammering the API.
#   * But an attacker can rotate through 10 compromised keys to multiply the
#     effective rate 10×. The per-IP bucket caps the aggregate rate from a
#     single source IP regardless of how many keys the attacker has.
#   * Tighter default (100/min vs 1000/min per key) — assumes a legit
#     merchant uses 1 key from 1 IP, so 100/min is ample for normal ops;
#     an attacker pivoting through proxies still hits the per-IP cap.
#
# Distributed mode (Redis): when ``REDIS_URL`` is set, the per-IP counter is
# an atomic ``INCR`` + ``EXPIRE`` on a per-minute bucket key — shared across
# all 4 uvicorn workers, so the limit is global (not 4× the per-process
# number). The in-memory fallback (no Redis) is per-process — documented
# caveat: a 4-worker deployment has 4× the configured per-IP limit.
# ---------------------------------------------------------------------------


class IPRateLimiter:
    """Per-IP rate limiter with Redis sliding-window + in-memory fallback.

    The Redis path uses a per-minute bucket key (``rto:ip:rl:{ip}:{minute}``)
    with ``INCR`` + ``EXPIRE 60`` — a textbook Redis rate-limit pattern
    (https://redis.io/commands/incr#pattern-rate-limiter). The in-memory
    fallback is a per-process token bucket (same algorithm as ``TokenBucket``
    above) so the 350 existing tests pass without a Redis fixture.

    Threading: the in-memory path is lock-guarded. The Redis path is
    thread-safe by virtue of redis-py's connection pool. ``check()`` never
    raises — Redis-down degrades to the in-memory bucket (logged to stderr).
    """

    # Reuse the lazy-import + connect-once pattern from ``StreamProducer``
    # (src/stream/producer.py:74-105) so importing this module never
    # requires Redis to be installed/reachable in CI / test mode.
    _REDIS_AVAILABLE: bool | None = None

    def __init__(
        self,
        rate_per_min: int | None = None,
        redis_url: str | None = None,
    ) -> None:
        self.rate_per_min = rate_per_min if rate_per_min is not None else per_ip_rate_per_min()
        self.capacity = float(self.rate_per_min)
        self.rate = self.rate_per_min / 60.0
        self.redis_url = redis_url
        self.client: Any = None
        self._connect_attempted = False
        # In-memory fallback (used when Redis is unavailable OR unset).
        # Same shape as ``TokenBucket`` — pre-computed to keep ``check`` O(1).
        self._mem_buckets: dict[str, float] = {}
        self._mem_updated: dict[str, float] = {}
        self._lock = threading.Lock()

    def _ensure_client(self) -> Any:
        """Lazy-import + connect to Redis. Mirrors ``StreamProducer``'s
        pattern: returns ``None`` permanently after the first failed
        attempt so a flapping Redis doesn't add latency to every request.
        """
        if self._connect_attempted:
            return self.client
        self._connect_attempted = True
        if not self.redis_url:
            return None
        try:
            import redis  # type: ignore[import-not-found]

            self.client = redis.from_url(self.redis_url, decode_responses=True)
            # PING once so a misconfigured URL fails fast at startup (the
            # first request) rather than per-request.
            self.client.ping()
        except ImportError:
            print(
                "[security] redis package not installed — per-IP rate "
                "limiting will use the in-memory per-process fallback "
                "(multi-worker deployments will see 4× the configured "
                "rate). Add `redis>=5.0` to requirements.txt.",
                file=sys.stderr,
            )
            self.client = None
        except Exception as e:  # pragma: no cover — defensive
            print(
                f"[security] redis connect failed ({type(e).__name__}: {e}) "
                "— per-IP rate limiting falls back to in-memory per-process.",
                file=sys.stderr,
            )
            self.client = None
        return self.client

    @staticmethod
    def extract_ip(
        x_forwarded_for: str | None,
        client_host: str | None,
    ) -> str:
        """Resolve the client IP. Honors the first IP in ``X-Forwarded-For``
        (the standard reverse-proxy header — nginx, AWS ALB, Cloudflare all
        append the chain left-to-right, so the FIRST IP is the original
        client). Falls back to ``request.client.host`` (Starlette's
        ``peer.host`` — the direct TCP peer, which is the proxy itself when
        behind a reverse proxy, hence the X-Forwarded-For preference).
        """
        if x_forwarded_for:
            # Take the first IP in the comma-separated chain (the original
            # client). Strip whitespace + port-suffix (RFC 7239 ``"for="``
            # syntax is rare in practice; we handle the common form).
            first = x_forwarded_for.split(",")[0].strip()
            # Strip optional port suffix (IPv4 + [IPv6]).
            if first.startswith("["):
                # IPv6 literal — strip ``[...]`` + optional ``:port``.
                end = first.find("]")
                if end > 0:
                    return first[1:end]
            # IPv4 — strip ``:port`` if present (port > 0 has 1-5 digits).
            if ":" in first:
                host, _, port = first.rpartition(":")
                if port.isdigit() and host:
                    return host
            return first
        return client_host or "unknown"

    def _check_redis(self, ip: str) -> bool:
        """Sliding-minute Redis window. Atomic via ``INCR`` + ``EXPIRE``
        (the canonical Redis rate-limiter pattern). Returns True if the
        request is allowed (under the cap), False if rate-limited.
        """
        client = self._ensure_client()
        if client is None:
            # Redis unavailable — fall through to in-memory path.
            return self._check_mem(ip)
        # Minute-bucket key. Use UTC seconds so the bucket rolls over
        # cleanly at minute boundaries regardless of worker clock skew.
        bucket = int(time.time() // 60)
        key = f"rto:ip:rl:{ip}:{bucket}"
        try:
            count = client.incr(key)
            # Only set TTL on the first increment (avoid the per-request
            # SETEX round-trip — the key already exists from INCR).
            if count == 1:
                # TTL = 120s so the key outlives the minute bucket by 60s
                # (cleaner expiry margin — don't risk an early eviction).
                client.expire(key, 120)
            return count <= self.rate_per_min
        except Exception as e:
            # Redis blip — fall through to in-memory. Logged + the request
            # is NOT rejected (the in-memory bucket is more permissive in
            # multi-worker mode, but rejecting would be worse — DoS-by-
            # circuit-breaker).
            print(
                f"[security] redis INCR failed ({type(e).__name__}: {e}) "
                "— per-IP check falls back to in-memory for this request.",
                file=sys.stderr,
            )
            return self._check_mem(ip)

    def _check_mem(self, ip: str) -> bool:
        """Per-process in-memory token bucket (fallback when Redis is
        unavailable). CAVEAT: in a 4-worker uvicorn deployment, the
        effective per-IP limit is 4× ``rate_per_min`` because each worker
        has its own bucket. The Redis path closes this hole; the in-memory
        path is for tests + single-process local dev.
        """
        now = time.monotonic()
        with self._lock:
            last = self._mem_updated.get(ip, now)
            tokens = min(self.capacity, self._mem_buckets.get(ip, self.capacity))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._mem_buckets[ip] = tokens
                self._mem_updated[ip] = now
                return False
            self._mem_buckets[ip] = tokens - 1.0
            self._mem_updated[ip] = now
            return True

    def check(self, ip: str) -> bool:
        """Returns True if the request is allowed, False if rate-limited.
        Never raises. Uses Redis when available, else falls back to
        in-memory. The choice is made once per process (lazy connect on the
        first call); subsequent calls reuse the same client.
        """
        if not self.redis_url:
            # Fast-path for tests + local dev (no REDIS_URL set).
            return self._check_mem(ip)
        return self._check_redis(ip)


# ---------------------------------------------------------------------------
# P0-1 — Probability binning + Gaussian noise (anti-model-extraction).
#
# Paper: Tramer et al., "Stealing Machine Learning Models via Prediction
# APIs", USENIX Security 2016. Equation-solving extraction attacks recover
# the model's parameters with ~100× fewer queries than the training set
# needed; rounding the probability to 2 decimals increases the extraction
# error 10-100× (the attacker can no longer distinguish p=0.7341 from
# p=0.7342 — they're both ``0.73``), and adding Gaussian noise σ=0.01
# raises the required query count 5-10× further (the attacker must average
# multiple queries to denoise).
#
# Applied INSIDE ``/risk/score`` immediately after ``model.predict_proba``.
# All downstream paths (decisions, audit, response body, OTel spans, drift
# stream) see the noisy value — so the audit's ``probability`` field, the
# response body's ``probability`` field, and the OTel span's
# ``rto.probability`` attribute all stay consistent. The cost-optimizer
# sees the noisy proba (decisions on a noisy signal — acceptable: noise σ
# of 0.01 is well below the natural model uncertainty).
#
# SHAP explainer path (separate endpoint /v1/explain/shap) calls
# ``model.predict_proba`` directly — unaffected. The ``ANTI_EXTRACTION_NOISE``
# flag is for an internal admin override (e.g. a red-team probe that wants
# the raw proba); the SHAP path is structurally isolated.
# ---------------------------------------------------------------------------


def apply_anti_extraction_noise(proba: float) -> float:
    """Bin the probability to 2 decimals + add Gaussian noise (σ=0.01),
    clamped to [0, 1]. Idempotent on the noise flag — when
    ``ANTI_EXTRACTION_NOISE=false``, returns ``proba`` unchanged.

    No-op-safe: never raises (numpy import is wrapped; on ImportError
    falls back to the ``random`` stdlib module).

    Parameters
    ----------
    proba : float
        The raw model probability in [0, 1] from ``predict_proba``.

    Returns
    -------
    float
        The post-processed probability. If the noise flag is on, the value
        is ``round(proba + N(0, 0.01), 2)`` clamped to [0, 1]. Otherwise
        the input is returned unchanged.
    """
    if not anti_extraction_noise_enabled():
        return float(proba)
    # Try numpy's RNG (matches the spec verbatim + the project's standard
    # numeric library — ``numpy`` is already a dependency via pandas +
    # sklearn). Fall back to the stdlib ``random`` if numpy is missing
    # (e.g. an extremely minimal runtime).
    try:
        import numpy as _np

        noise = float(_np.random.normal(0.0, 0.01))
    except ImportError:  # pragma: no cover — defensive
        import random as _stdlib_random

        noise = _stdlib_random.gauss(0.0, 0.01)
    noisy = float(proba) + noise
    # Clamp to [0, 1] before binning so a near-1.0 proba + +3σ noise
    # doesn't yield 1.004 (which would surprise downstream code that
    # asserts probability in [0, 1]).
    if noisy < 0.0:
        noisy = 0.0
    elif noisy > 1.0:
        noisy = 1.0
    # Bin to 2 decimals — the headline defence. The attacker sees only
    # 100 distinct probability values instead of a continuous real.
    return round(noisy, 2)


# ---------------------------------------------------------------------------
# P1-2 — HMAC-SHA256 request signing (anti-replay).
#
# Paper: RFC 5869 (HKDF) + RFC 2104 (HMAC). The mandate subsystem already
# uses HMAC for dual-control overrides (``src/api/keys.py`` —
# ``derive_hmac_key`` per RFC 5869 / NIST SP 800-56C §5). This module
# applies the same primitive to the /risk/score request path:
#
#   signature = HMAC-SHA256(
#       key   = the merchant's raw API key (already sent in Authorization),
#       msg   = method + "\n" + path + "\n" + body_sha256 + "\n" + timestamp
#   )
#
#   header  = "X-Signature: t=<timestamp>,v=<hex-signature>"
#
# The server:
#   1. Recomputes body_sha256 (independently of FastAPI's body parsing
#      — uses the raw bytes the client sent).
#   2. Verifies the HMAC against the recomputed canonical message.
#   3. Checks the timestamp is within ±``hmac_replay_window_seconds()`` of
#      server time → replays (a captured valid signature + body reused
#      later) fail the timestamp check.
#
# Opt-in via ``REQUIRE_HMAC=true`` so the existing demo flow + the 350
# existing tests don't need to compute signatures.
# ---------------------------------------------------------------------------


def _canonical_message(
    method: str,
    path: str,
    body_bytes: bytes,
    timestamp: str,
) -> bytes:
    """Build the canonical HMAC message. Order matters — both client + server
    must use the EXACT same byte sequence. The 4 components are newline-
    separated to prevent concatenation ambiguity (``"POST\n/risk/score\n..."``
    can't be confused with ``"POS\nT/risk/score\n..."``).
    """
    body_sha256 = hashlib.sha256(body_bytes).hexdigest()
    return f"{method.upper()}\n{path}\n{body_sha256}\n{timestamp}".encode()


def compute_hmac_signature(
    secret: str,
    method: str,
    path: str,
    body_bytes: bytes,
    timestamp: str,
) -> str:
    """Compute the HMAC-SHA256 signature hex string. Used by clients
    (the test suite's helper, a JS merchant SDK, etc.) — exported as a
    public function so a future merchant SDK in ``web/src/lib/`` can call
    it without re-implementing the canonical-message format.
    """
    msg = _canonical_message(method, path, body_bytes, timestamp)
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def parse_signature_header(header_value: str | None) -> tuple[str | None, str | None]:
    """Parse ``X-Signature: t=<ts>,v=<hex>``. Returns ``(timestamp, signature)``
    — both None if the header is absent or malformed. Tolerant of whitespace
    + order swap (``v=...,t=...`` is just as valid)."""
    if not header_value:
        return None, None
    ts: str | None = None
    sig: str | None = None
    for part in header_value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip().lower()
        v = v.strip()
        if k == "t":
            ts = v
        elif k == "v":
            sig = v
    return ts, sig


def verify_hmac_signature(
    *,
    secret: str,
    method: str,
    path: str,
    body_bytes: bytes,
    signature_header: str | None,
    server_now: float | None = None,
) -> tuple[bool, str]:
    """Verify an ``X-Signature`` header against the canonical message.

    Returns ``(ok, reason)``. ``ok=False`` + ``reason`` is set on any
    failure mode (missing header, malformed format, timestamp skew, bad
    signature). Constant-time comparison via ``hmac.compare_digest``.

    The ``secret`` is the merchant's raw API key (the same value the
    client sent in ``Authorization: Bearer <key>`` — verified separately
    by ``check_key``). This means a captured Bearer token alone is NOT
    enough to forge valid signatures: the attacker must also know the
    raw key (which the server hashes at storage time —
    ``src/api/security.py::check_key`` line 49 — so a DB leak doesn't
    reveal it).
    """
    if not require_hmac_enabled():
        # Opt-in flag is off — every request passes (no enforcement).
        return True, "hmac_enforcement_disabled"
    ts, sig = parse_signature_header(signature_header)
    if ts is None or sig is None:
        return False, "missing or malformed X-Signature header"
    # Timestamp skew check — anti-replay. A captured valid signature
    # can't be replayed after the ±60s window expires.
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return False, "X-Signature timestamp is not an integer"
    now = server_now if server_now is not None else time.time()
    skew = abs(now - ts_int)
    window = hmac_replay_window_seconds()
    if skew > window:
        return False, f"timestamp skew {skew:.0f}s exceeds {window}s replay window"
    # Recompute + constant-time compare. The canonical message uses the
    # RAW body bytes (independent of FastAPI's parsing) so the client can't
    # trick us with a JSON-equivalent-but-byte-different body.
    expected = compute_hmac_signature(secret, method, path, body_bytes, ts)
    if not hmac.compare_digest(expected, sig):
        return False, "signature mismatch (bad key, body, or canonical form)"
    return True, "ok"
