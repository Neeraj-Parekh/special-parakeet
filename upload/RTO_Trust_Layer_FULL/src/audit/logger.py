from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def self_salt() -> str:
    import os

    return os.environ.get("RTO_AUDIT_SALT", "local-demo-salt")


def redact_customer(customer_id: str) -> str:
    """Never store raw customer identifiers; store salted digest prefix."""
    import hashlib

    return "cust_" + hashlib.sha256(f"{customer_id}:{self_salt()}".encode()).hexdigest()[:16]


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class AuditLogger:
    """Append-only JSONL audit log, tamper-evident hash chain, O(1) indexed reads.

    raw_hash = sha256(canonical(record_without_hash_fields) + previous_raw_hash).
    Editing any historical record breaks every subsequent link; `verify_chain`
    recomputes the full chain for compliance audits.
    """

    HASH_FIELDS = ("previous_hash", "raw_hash")

    def __init__(self, path: str = "out/audit.jsonl", model_version: str = "dev"):
        self.path = Path(path)
        self.model_version = model_version
        self._index: dict[str, int] = {}
        self._lock = threading.Lock()
        self.last_hash = GENESIS
        if self.path.exists():
            with self.path.open() as f:
                for offset, line in enumerate(f):
                    rec = json.loads(line)
                    self._index[rec.get("audit_id", "")] = offset
                    self.last_hash = rec.get("raw_hash", self.last_hash)

    def log(self, payload: dict) -> str:
        audit_id = str(uuid.uuid4())
        base = {
            "audit_id": audit_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_version": self.model_version,
            **payload,
        }
        with self._lock:
            base["previous_hash"] = self.last_hash
            base["raw_hash"] = self._hash(base)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                self._index[audit_id] = f.tell()
                f.write(json.dumps(base, default=str) + "\n")
            self.last_hash = base["raw_hash"]
        return audit_id

    def read(self, audit_id: str) -> dict | None:
        offset = self._index.get(audit_id)
        if offset is None or not self.path.exists():
            return None
        with self.path.open() as f:
            f.seek(offset)
            return json.loads(f.readline())

    def verify_chain(self) -> tuple[bool, int, str]:
        """Recompute entire chain. Returns (ok, records_checked, first_bad_id)."""
        expected_prev = GENESIS
        n = 0
        if not self.path.exists():
            return True, 0, ""
        with self.path.open() as f:
            for line in f:
                rec = json.loads(line)
                stored_hashes = {k: rec.get(k) for k in self.HASH_FIELDS}
                body = {k: v for k, v in rec.items() if k not in self.HASH_FIELDS}
                want_prev = expected_prev
                want_raw = self._hash(body, prev=want_prev)
                hashes_ok = (
                    stored_hashes["previous_hash"] == want_prev
                    and stored_hashes["raw_hash"] == want_raw
                )
                if not hashes_ok:
                    return False, n, rec.get("audit_id", "?")
                expected_prev = stored_hashes["raw_hash"]
                n += 1
        return True, n, ""

    @staticmethod
    def _hash(record: dict, prev: str | None = None) -> str:
        import hashlib

        body = {k: v for k, v in record.items() if k not in AuditLogger.HASH_FIELDS}
        prev_hash = prev if prev is not None else record.get("previous_hash", GENESIS)
        return hashlib.sha256((canonical(body) + prev_hash).encode()).hexdigest()
