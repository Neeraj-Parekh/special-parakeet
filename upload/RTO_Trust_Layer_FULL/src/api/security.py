from __future__ import annotations

import hashlib
import os
import threading
import time


def _keys(env_var: str) -> set[str]:
    raw = os.environ.get(env_var, "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def default_keys() -> dict[str, str]:
    """Demo fallback keys so local runs work without env setup."""
    return {
        "scorer": _keys("RTO_SCORER_KEYS") or {"score-demo-key"},
        "admin": _keys("RTO_ADMIN_KEYS") or {"admin-demo-key"},
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
