from __future__ import annotations

import hashlib
import os
import threading
import time


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
