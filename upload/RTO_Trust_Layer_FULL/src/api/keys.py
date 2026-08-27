"""Cryptographic key-derivation helpers for the RTO Trust Layer.

Stdlib-only (no ``cryptography`` dep) — uses ``hashlib`` + ``hmac`` to
implement HKDF-Extract + HKDF-Expand per RFC 5869 so the raw API keys
configured via ``RTO_ADMIN_KEYS`` never appear directly in HMAC calls.

A1 fix (Subagent 14-d, Wave 1 of the 25-question self-check remediation):
the dual-control override handler (T1.1 — Subagent 11-a's HMAC chain)
used to pass ``admin2_key`` (a raw admin API key string from the env var)
DIRECTLY as the HMAC key. Best practice (RFC 5869 §2 + NIST SP 800-56C
§5): derive a context-bound subkey via HKDF so:

  * the raw key never appears in any HMAC call (DB / memory / stack
    snapshots after a compromise don't leak the long-lived secret),
  * the derived key is context-bound to the dual-control override use
    case (``info=b"dual-control"``) — a leak of the derived key cannot
    be replayed against any other HMAC consumer in the system (the
    salt + info tuple domain-separates the derivation), and
  * the salt ``b"rto-override-v1"`` is version-tagged so a future
    rotation (``v2``) cleanly invalidates prior derived keys without
    touching the raw keys in env / secrets manager.

The HKDF construction is cheap (~1 μs per derivation); we additionally
cache derived keys in a module-level dict keyed by the (raw_key, salt,
info, length) tuple so the hot path doesn't recompute on every override.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Module-level derived-key cache. Keyed by (raw_key_bytes, salt, info,
# length) so the SAME raw admin key + salt + info reuses the derived key
# across requests (HKDF is deterministic). Bounded LRU is overkill — the
# number of distinct (raw_key, salt, info) tuples in production is small
# (= number of admin keys × 1 salt × 1 info tuple), so a plain dict
# guarded by a lock is fine.
# ---------------------------------------------------------------------------
_derived_cache: dict[tuple[bytes, bytes, bytes, int], bytes] = {}
_derived_cache_lock = threading.Lock()


def _hkdf_extract(salt: bytes, ikm: bytes, hash_algo: str = "sha256") -> bytes:
    """HKDF-Extract per RFC 5869 §2.2.

    PRK = HMAC-Hash(salt, IKM)

    ``salt`` is the public salt (empty string per RFC 5869 §2.2 default
    is a string of HashLen zeros; we require a non-empty salt for the
    A1 fix — the ``b"rto-override-v1"`` version tag).
    """
    return hmac.new(salt, ikm, getattr(hashlib, hash_algo)).digest()


def _hkdf_expand(
    prk: bytes,
    info: bytes,
    length: int,
    hash_algo: str = "sha256",
) -> bytes:
    """HKDF-Expand per RFC 5869 §2.3.

    T(0) = empty
    T(i) = HMAC-Hash(PRK, T(i-1) | info | byte(i))   for i = 1..N
    OKM  = first L octets of T(1) | T(2) | ... | T(N)

    where N = ceil(L / HashLen).
    """
    hash_len = getattr(hashlib, hash_algo)().digest_size  # e.g. 32 for sha256
    if length > 255 * hash_len:
        # RFC 5869 §2.3 + §3 (security analysis on max output length).
        raise ValueError(
            f"requested HKDF length {length} exceeds RFC 5869 maximum "
            f"of 255 * {hash_algo}_digest_size={255 * hash_len}"
        )
    blocks: list[bytes] = []
    t_prev = b""
    block_index = 1
    while len(b"".join(blocks)) < length:
        t_prev = hmac.new(
            prk,
            t_prev + info + bytes([block_index]),
            getattr(hashlib, hash_algo),
        ).digest()
        blocks.append(t_prev)
        block_index += 1
    return b"".join(blocks)[:length]


def derive_hmac_key(
    raw_key: bytes | str,
    salt: bytes,
    info: bytes,
    length: int = 32,
    *,
    hash_algo: str = "sha256",
    use_cache: bool = True,
) -> bytes:
    """Derive a context-bound subkey from a raw key via HKDF (RFC 5869).

    Construction::

        PRK = HMAC-SHA256(salt=salt, IKM=raw_key)
        OKM = HKDF-Expand(PRK, info, length)
        return OKM  # use OKM as the HMAC key in subsequent calls

    The raw ``raw_key`` is NEVER used directly as an HMAC key by the
    caller — only the derived ``OKM`` is. A DB / memory / stack
    snapshot that leaks ``OKM`` does NOT compromise the raw admin key
    (the derivation is one-way — HKDF-Extract + HKDF-Expand are both
    built on HMAC; recovering the IKM from the PRK or OKM is as hard
    as inverting HMAC-SHA256).

    Parameters
    ----------
    raw_key : bytes | str
        The raw key (e.g. an admin API key string from ``RTO_ADMIN_KEYS``).
        Strings are UTF-8 encoded; bytes pass through unchanged.
    salt : bytes
        Public salt (RFC 5869 §2.2). MUST be non-empty for the A1 fix
        so the derivation is domain-separated from any other HMAC
        consumer that might re-use the same raw key.
    info : bytes
        Context-binding info string (RFC 5869 §2.3). For the
        dual-control override path, use ``b"dual-control"`` so the
        derived key is bound to that single use case.
    length : int, default 32
        Output key length in bytes (32 = HMAC-SHA256 key length).
    hash_algo : str, default "sha256"
        Hash algorithm for the underlying HMAC (sha256 per RFC 5869
        default Hash; the only value tested here).
    use_cache : bool, default True
        If True (default), the derived key is cached in a module-level
        dict keyed by (raw_key, salt, info, length) so subsequent calls
        with the same arguments return the cached bytes (HKDF is
        deterministic — caching is safe). Set False only for tests
        that want to re-derive to assert determinism.

    Returns
    -------
    bytes
        The derived subkey (``length`` bytes). Use this as the ``key``
        argument to ``hmac.new(derived_key, msg, hashlib.sha256)``.
    """
    if isinstance(raw_key, str):
        ikm = raw_key.encode("utf-8")
    else:
        ikm = raw_key
    if not salt:
        raise ValueError(
            "HKDF salt MUST be non-empty for the A1 fix — the salt "
            "domain-separates the derivation from any other HMAC "
            "consumer that might re-use the same raw key."
        )
    if not info:
        raise ValueError(
            "HKDF info MUST be non-empty for the A1 fix — the info "
            "context-binds the derived key to a single use case."
        )
    if length <= 0:
        raise ValueError(f"HKDF length must be positive, got {length}")

    cache_key: tuple[bytes, bytes, bytes, int] = (ikm, salt, info, length)
    if use_cache:
        cached = _derived_cache.get(cache_key)
        if cached is not None:
            return cached

    prk = _hkdf_extract(salt, ikm, hash_algo=hash_algo)
    okm = _hkdf_expand(prk, info, length, hash_algo=hash_algo)

    if use_cache:
        with _derived_cache_lock:
            # setdefault — defensive against a concurrent derivation
            # that raced us between the get() above + this lock; either
            # derivation produces the same bytes (HKDF is deterministic)
            # so the winner of the race is irrelevant.
            _derived_cache.setdefault(cache_key, okm)
    return okm


def clear_derived_key_cache() -> None:
    """Test helper — drop the derived-key cache so a subsequent call
    re-derives (used by tests that mutate the env var ``RTO_ADMIN_KEYS``
    between test cases and want to assert the new key produces a new
    derived key without being shadowed by a stale cache entry). Not
    part of the public API.
    """
    global _derived_cache
    with _derived_cache_lock:
        _derived_cache = {}


__all__: list[str] = [
    "derive_hmac_key",
    "clear_derived_key_cache",
]
