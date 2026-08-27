"""Signed spending mandates: the only way an agent may transact within bounds.

Doctrine: agents hold ZERO ambient authority. A merchant backend (admin scope) issues
a short-lived, bounded mandate; agents present it; the server enforces bounds and
escalates any breach deterministically. Agents cannot mint, extend, or widen mandates.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode


def _secret() -> bytes:
    return os.environ.get("RTO_MANDATE_SECRET", "dev-only-secret").encode()


def issue_mandate(
    customer_ref: str,
    max_amount_inr: float,
    ttl_seconds: int,
    scope: str = "cod_order",
) -> str:
    payload = {
        "sub": hashlib.sha256(f"{customer_ref}:{self_salt()}".encode()).hexdigest()[:16],
        "scope": scope,
        "max_amount_inr": round(float(max_amount_inr), 2),
        "exp": int(time.time()) + int(ttl_seconds),
        "iat": int(time.time()),
    }
    body = urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


class MandateVerdict:
    VALID = "valid"
    TAMPERED = "tampered"
    EXPIRED = "expired"
    BREACH = "breach"


def verify_mandate(token: str | None, amount_inr: float) -> tuple[str, dict]:
    """Returns (verdict, payload). TAMPERED/BREACH must escalate server-side."""
    empty: dict = {}
    if not token:
        return MandateVerdict.TAMPERED, empty
    try:
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return MandateVerdict.TAMPERED, empty
        pad = "=" * (-len(body) % 4)
        payload = json.loads(urlsafe_b64decode(body + pad))
        if payload.get("exp", 0) < time.time():
            return MandateVerdict.EXPIRED, payload
        if float(amount_inr) > float(payload.get("max_amount_inr", 0)):
            return MandateVerdict.BREACH, payload
        return MandateVerdict.VALID, payload
    except Exception:
        return MandateVerdict.TAMPERED, empty


def self_salt() -> str:
    return os.environ.get("RTO_AUDIT_SALT", "local-demo-salt")
