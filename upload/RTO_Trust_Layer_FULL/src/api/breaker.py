"""Circuit breaker around model inference. Fails safe to rules-only REVIEW mode."""
from __future__ import annotations

import threading
import time


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_seconds: int = 30):
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.failures = 0
        self.state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self.last_failure_at = 0.0
        self.lock = threading.Lock()

    def allow_attempt(self) -> bool:
        with self.lock:
            if self.state == "OPEN":
                if time.monotonic() - self.last_failure_at >= self.recovery_seconds:
                    self.state = "HALF_OPEN"
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self.lock:
            self.failures = 0
            self.state = "CLOSED"

    def record_failure(self) -> None:
        with self.lock:
            self.failures += 1
            self.last_failure_at = time.monotonic()
            if self.failures >= self.failure_threshold or self.state == "HALF_OPEN":
                self.state = "OPEN"
