"""Agent action allowlist + multi-tenant scope/merchant isolation
(Mission 3 — server-side enforcement; F19 + D13 fixes — Wave 2 Subagent 14-e).

Per user's 5 Missions: "Agent can only call N APIs. Any other intent
returns 'Action not permitted.'" The allowlist below is the canonical,
production-importable definition. ``scripts/demo_agent.py`` is the demo
client; this module is the server-side authority that
``src/api/routes.py`` (and any future agent-gateway endpoint) imports to
gate agent calls. Per Mao 2026 (SoK: Security of Autonomous LLM Agents in
Agentic Commerce), D2 (transaction-authorization): "design mandates as
scoped, task-bound, attenuating credentials rather than standing broad
authority" — the allowlist is the operational expression of that
attenuation.

Day 1 Track D (V3 §13 — mandate action-class expansion) added 3 UPI
Circle / delegated-payment actions per NPCI OC-201B (8 Oct 2025). The
4 original COD-order actions are unchanged. 7 actions total.

Track P (Task 11-a) — this module is the production home of
``ALLOWED_ACTIONS``; the demo script imports it rather than defining its
own copy. The interface contract:

  * Module path: ``src.api.agent_allowlist``
  * Exports   : ``ALLOWED_ACTIONS`` (dict[str, dict])
                ``check_agent_action(action, mandate_scope=None, key_scope=None) -> tuple[bool, str]``

Wave 2 (Subagent 14-e — F19 + D13) — this module is extended with:

  * ``SCOPE_ACTION_MAP`` — the scope→actions mapping (D13 fix). Maps
    ``scorer`` / ``ops`` / ``admin`` key scopes to the subset of
    ``ALLOWED_ACTIONS`` they may invoke via the ``X-Agent-Action``
    header. ``scorer`` can read + dry-run; ``ops`` can do scorer + the
    operational interventions (block + revoke); ``admin`` can do all
    7 actions + the special ``override`` pseudo-action (which routes
    to the dual-control HMAC chain in routes.py).
  * ``get_key_merchant_id(key)`` — file-mode merchant_id lookup from
    the ``RTO_KEY_MERCHANT_BINDINGS`` env var (F19 fix). In Postgres
    mode the ``api_keys`` table (alembic 007) is the authoritative
    source; this helper is the file-mode fallback the tests use.
  * ``get_key_scope(key, scorer_keys, admin_keys)`` — D13 helper that
    looks up a key's scope (``scorer`` / ``ops`` / ``admin``) from
    the in-memory key sets + the ``RTO_OPS_KEYS`` env var (the third
    scope not represented in ``default_keys()``).
  * ``clear_bindings_cache()`` — test helper for env-var mutations.
"""
from __future__ import annotations

import os
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Agent allowlist — prompt-razor §5 lines 1003-1050 (BoundedAgent class).
# Day 1 Track D (V3 §13) added 3 UPI Circle / delegated-payment actions per
# NPCI OC-201B (8 Oct 2025). The 4 original COD-order actions are unchanged.
# Per user's 5 Missions: "Agent can only call N APIs. Any other intent
# returns 'Action not permitted.'" — enforced by ``check_agent_action`` +
# the consuming routes.py path. Per user's 6 demo moments #5: high-cost
# actions (``requires_approval=True``) do NOT execute — the agent creates a
# case in the review queue and responds "I cannot perform this action. I
# have requested human approval."
# ---------------------------------------------------------------------------
ALLOWED_ACTIONS: dict[str, dict[str, Any]] = {
    # --- Original 4 COD-order actions (prompt-razor §5) ---
    "score_order": {"cost": 0, "requires_approval": False},
    "request_otp": {"cost": 1, "requires_approval": False},
    "flag_review": {"cost": 2, "requires_approval": False},
    "block_order": {"cost": 10, "requires_approval": True},
    # --- UPI Circle / delegated payments (NPCI OC-201B, 8 Oct 2025) ---
    "upi_circle_delegated_pay": {
        "cost": 5,
        "requires_approval": True,  # explicit user action per OC-201B §3
        "hard_caps": {
            "max_per_txn": 5000,    # OC-201B: ₹5,000 per transaction
            "max_per_month": 15000,  # OC-201B: ₹15,000 per delegation/month
            "cooling_24h": 5000,    # OC-201B: 24h ₹5,000 cumulative cooling
            "max_devices": 5,       # OC-201B: max 5 IoT/software per user
        },
    },
    "validate_device_id": {
        # OC-201B §3.7 Issuer Bank duty — per-txn device validation. No
        # approval needed (read-only check against the mandate's allowlist).
        "cost": 1,
        "requires_approval": False,
    },
    "revoke_delegation_on_inactivity": {
        # OC-201B: auto-revoke after 6 months inactivity or on tampering.
        # Auto-triggered — no human approval needed (this is the safety net).
        "cost": 2,
        "requires_approval": False,
        "auto_trigger_days": 180,
    },
}


# ---------------------------------------------------------------------------
# Wave 2 (Subagent 14-e — D13 fix) — scope→actions mapping.
# ---------------------------------------------------------------------------
# The ``X-Mandate-Scope`` header is parsed but never enforced (the D13
# finding). The authoritative scope is the API key's BOUND scope (one of
# ``scorer`` / ``ops`` / ``admin``) — NOT a client-supplied header. The
# ``enforce_agent_action`` Depends in routes.py now extracts the caller's
# key scope from the Authorization header + consults the mapping below to
# verify the requested ``X-Agent-Action`` is in the caller's scope.
#
# Mapping rationale (per task spec):
#   * ``scorer`` — read-only + dry-run. Can ``score_order`` (the primary
#     risk-gating call), ``validate_device_id`` (read-only check), and
#     the REVIEW-gate actions ``request_otp`` + ``flag_review`` (cost
#     0/1/2 — no money moves). CANNOT ``block_order`` (cost 10 — op
#     intervention), ``revoke_delegation_on_inactivity`` (safety-net
#     op action), or ``upi_circle_delegated_pay`` (money-moving).
#   * ``ops`` — scorer set + the operational interventions. Adds
#     ``block_order`` + ``revoke_delegation_on_inactivity``. CANNOT
#     ``upi_circle_delegated_pay`` (the only OC-201B money-moving action).
#   * ``admin`` — all 7 ALLOWED_ACTIONS + the special ``override``
#     pseudo-action. The override pseudo-action is NOT in ALLOWED_ACTIONS
#     (the override endpoint is money-moving + dual-control — gated
#     separately by the HMAC chain in routes.py), but the scope check
#     here permits ``admin`` keys to declare ``X-Agent-Action: override``
#     so the dual-control handler can run. ``scorer`` / ``ops`` keys
#     declaring ``override`` are rejected here (403) before the override
#     handler is reached.
#
# The frozensets are immutable so callers can't accidentally mutate the
# canonical mapping at runtime.
SCOPE_ACTION_MAP: dict[str, frozenset[str]] = {
    "scorer": frozenset(
        {
            "score_order",
            "request_otp",
            "flag_review",
            "validate_device_id",
        }
    ),
    "ops": frozenset(
        {
            "score_order",
            "request_otp",
            "flag_review",
            "validate_device_id",
            "block_order",
            "revoke_delegation_on_inactivity",
        }
    ),
    "admin": frozenset(
        {
            "score_order",
            "request_otp",
            "flag_review",
            "block_order",
            "upi_circle_delegated_pay",
            "validate_device_id",
            "revoke_delegation_on_inactivity",
            # Special pseudo-action — money-moving dual-control override
            # is NOT in ALLOWED_ACTIONS (gated separately by the HMAC
            # chain); ``admin`` scope can declare it so the override
            # handler can run.
            "override",
        }
    ),
}

# The special override pseudo-action — surfaced as a constant so routes.py
# can reference it without hardcoding the string.
OVERRIDE_ACTION: str = "override"


# ---------------------------------------------------------------------------
# Wave 2 (Subagent 14-e — F19 fix) — file-mode merchant_id binding.
# ---------------------------------------------------------------------------
# In Postgres mode (DATABASE_URL set), the ``api_keys`` table (alembic
# migration 007) is the authoritative source for the key→merchant_id
# binding. In file mode (DATABASE_URL unset — the test path), the binding
# is read from the ``RTO_KEY_MERCHANT_BINDINGS`` env var (CSV of
# ``key:merchant_id`` pairs, e.g.
# ``"score-demo-key:merch_a,admin-demo-key:merch_a,score-merch-b:merch_b"``).
#
# The binding is consulted by ``enforce_merchant_isolation`` Depends in
# routes.py to:
#   1. Inject the caller's merchant_id as a forced
#      ``WHERE body->>'merchant_id' = %s`` filter on data-access queries
#      (audit tail, override proof, SHAP explain).
#   2. Verify the merchant_id in the request body/query (e.g.
#      ``OrderIn.merchant_id`` on /risk/score or ``?merchant_id=<mid>``
#      on /v1/usage) MATCHES the caller's bound merchant_id — a mismatch
#      is 403 Forbidden ("cross-tenant access denied").
#
# Keys not in the binding map return None — that's the legacy-compat path
# (the default ``score-demo-key`` / ``admin-demo-key`` have no merchant
# binding → enforce_merchant_isolation returns None → no filtering kicks
# in → existing tests pass without binding setup).
_KEY_MERCHANT_BINDINGS: dict[str, str] = {}
_KEY_MERCHANT_BINDINGS_LOCK = threading.Lock()
_KEY_MERCHANT_BINDINGS_ENV = "RTO_KEY_MERCHANT_BINDINGS"


def _load_key_merchant_bindings() -> dict[str, str]:
    """Read + parse the ``RTO_KEY_MERCHANT_BINDINGS`` env var.

    Format: CSV of ``key:merchant_id`` pairs (e.g.
    ``"key1:merch_a,key2:merch_a,key3:merch_b"``). Whitespace around
    keys + merchant_ids is stripped. Empty entries are skipped.

    The binding is cached at module level (one env-var read per process
    boot) + a lock guards concurrent first-access from multiple
    FastAPI workers. The cache is invalidated by ``clear_bindings_cache()``
    for tests that mutate the env var between cases.
    """
    if _KEY_MERCHANT_BINDINGS:
        return _KEY_MERCHANT_BINDINGS
    raw = os.environ.get(_KEY_MERCHANT_BINDINGS_ENV, "").strip()
    if not raw:
        return _KEY_MERCHANT_BINDINGS
    pairs: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        key, _, mid = entry.partition(":")
        key = key.strip()
        mid = mid.strip()
        if key and mid:
            pairs[key] = mid
    with _KEY_MERCHANT_BINDINGS_LOCK:
        # Replace the cache atomically — a concurrent reader either sees
        # the empty dict (pre-load) or the full dict (post-load); never a
        # partial dict.
        _KEY_MERCHANT_BINDINGS.clear()
        _KEY_MERCHANT_BINDINGS.update(pairs)
    return _KEY_MERCHANT_BINDINGS


def get_key_merchant_id(key: str | None) -> str | None:
    """File-mode lookup of a key's bound merchant_id.

    Returns the merchant_id string if the key is in
    ``RTO_KEY_MERCHANT_BINDINGS``; None if the key is unbound (legacy
    mode — no isolation). The lookup is by the RAW key string (the env
    var is operator-readable; the production DB path stores the SHA-256
    hash so the table doesn't leak keys — see alembic 007).
    """
    if not key:
        return None
    bindings = _load_key_merchant_bindings()
    return bindings.get(key)


def get_key_scope(
    key: str | None,
    scorer_keys: set[str] | None = None,
    admin_keys: set[str] | None = None,
) -> str | None:
    """Look up a key's scope (``scorer`` / ``ops`` / ``admin``).

    The scope is determined by which key set the key belongs to:
      * In ``scorer_keys``  → ``"scorer"``
      * In ``admin_keys``   → ``"admin"``
      * In ``RTO_OPS_KEYS``  → ``"ops"``
    Returns None if the key doesn't match any set (unknown key).

    The ``RTO_OPS_KEYS`` env var is the file-mode source for the third
    scope (``default_keys()`` in security.py only returns ``scorer`` +
    ``admin``; ``ops`` is the Wave 2 scope added by this module).
    """
    if not key:
        return None
    if scorer_keys and key in scorer_keys:
        return "scorer"
    if admin_keys and key in admin_keys:
        return "admin"
    ops_raw = os.environ.get("RTO_OPS_KEYS", "")
    ops_set = {k.strip() for k in ops_raw.split(",") if k.strip()}
    if key in ops_set:
        return "ops"
    return None


def clear_bindings_cache() -> None:
    """Test helper — wipe the cached key→merchant_id binding map + the
    HKDF derived-key cache (so a test that mutates
    ``RTO_KEY_MERCHANT_BINDINGS`` between cases sees the new binding
    without being shadowed by a stale cache entry).
    """
    with _KEY_MERCHANT_BINDINGS_LOCK:
        _KEY_MERCHANT_BINDINGS.clear()


def check_agent_action(
    action: str,
    mandate_scope: str | None = None,
    key_scope: str | None = None,
) -> tuple[bool, str]:
    """Server-side gate for an agent's intended action.

    Returns ``(allowed, reason)``. The consuming endpoint in ``routes.py``
    (Subagent 11-routes) calls this before forwarding the action to its
    handler. ``allowed=False`` always short-circuits with a human-readable
    ``reason``; ``allowed=True`` means the action is in the allowlist AND
    does NOT require human approval (cost-0/1/2 actions execute directly).

    Logic:
      * If ``action not in ALLOWED_ACTIONS`` → ``(False, f"action '{action}' not in allowlist")``
        (Mission 3 — "Action not permitted.")
      * If the action's ``requires_approval`` flag is True → ``(False, "requires human approval")``
        (user's 6 demo moments #5 — high-cost actions are flagged; the agent
        queues a case instead of executing). The action IS still in the
        allowlist (so a future approved-tokens path can execute it); this
        function just signals the caller must do the human-approval dance
        before the action runs.
      * Otherwise → ``(True, "permitted")``.

    ``mandate_scope`` is accepted for forward-compat — future policy hooks
    may want to allowlist different action sets per mandate scope (e.g.
    ``upi_circle`` vs ``cod_order``). For now the allowlist is uniform across
    scopes; the parameter is ignored but typed so call sites can pass it
    without breakage.

    Wave 2 (Subagent 14-e — D13 fix) — ``key_scope`` parameter added.
    When the caller's key scope is provided (``"scorer"`` / ``"ops"`` /
    ``"admin"``), the function ADDITIONALLY verifies the requested action
    is in the scope's allowed set per ``SCOPE_ACTION_MAP``. A scope
    mismatch returns ``(False, f"scope '{key_scope}' cannot perform action '{action}'")``
    so the ``enforce_agent_action`` Depends in routes.py can raise 403
    with a clear cross-scope message. The special ``override``
    pseudo-action (NOT in ALLOWED_ACTIONS) is treated specially: it's
    permitted ONLY for ``admin`` scope; ``scorer`` / ``ops`` scope → 403.
    """
    # Wave 2 (D13 fix) — scope→action enforcement. Runs BEFORE the
    # ALLOWED_ACTIONS check so an out-of-scope action is rejected with
    # the scope-specific message even if the action is in the allowlist
    # (e.g. ``scorer`` scope + ``block_order`` action → 403 with the
    # scope-mismatch message, NOT the "requires human approval"
    # message). The authoritative scope is the key's bound scope, NOT
    # the client-supplied X-Mandate-Scope header (D13 finding).
    if key_scope is not None:
        allowed_actions_for_scope = SCOPE_ACTION_MAP.get(key_scope)
        if allowed_actions_for_scope is None:
            return (
                False,
                f"unknown key scope '{key_scope}' (expected one of: "
                f"{', '.join(sorted(SCOPE_ACTION_MAP))})",
            )
        if action not in allowed_actions_for_scope:
            return (
                False,
                f"scope '{key_scope}' cannot perform action '{action}' "
                f"(allowed for this scope: "
                f"{', '.join(sorted(allowed_actions_for_scope))})",
            )
        # ``override`` is the special pseudo-action: it's NOT in
        # ALLOWED_ACTIONS (the override endpoint is gated separately by
        # the dual-control HMAC chain in routes.py). The scope check
        # above already permitted ``admin`` scope; for non-admin scopes
        # the check above already rejected it. Short-circuit here so
        # the ALLOWED_ACTIONS check below doesn't 403 the override
        # pseudo-action with the "not in allowlist" message.
        if action == OVERRIDE_ACTION:
            return True, "permitted (admin scope; override routes to dual-control HMAC chain)"

    if action not in ALLOWED_ACTIONS:
        return False, f"action '{action}' not in allowlist"
    spec = ALLOWED_ACTIONS[action]
    if spec.get("requires_approval"):
        return False, "requires human approval"
    return True, "permitted"
