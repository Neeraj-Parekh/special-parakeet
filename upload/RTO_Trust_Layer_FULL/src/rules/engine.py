"""Deterministic rules engine evaluated before ML. Ops-tunable, no redeploy needed.

P0-2 (Tramer USENIX 2016 — anti-evasion): numeric ``gt``/``lt`` rules on
MONETARY fields (``amount_inr``) apply ±₹500 jitter to the threshold on
every evaluation. This makes the effective threshold a moving target so
binary-search attacks (e.g. recover the exact ``RULE-001`` threshold
``amount > 50000`` in ``log₂(50000) = 16`` queries) fail — the attacker
gets a slightly different boundary on every probe. Jitter is OFF for
categorical rules (``op="eq"``, ``op="in"``) and for numeric rules on
non-monetary fields (``items``, ``prior_orders``, ``prior_returns``).

Paper: IEEE Access 2024 — "Adversarial Attacks and Defenses in ML for
Tabular Data". §IV.A: "tabular features are interpretable → binary-search
on numeric thresholds recovers them in O(log N) queries; randomized
thresholds raise this to O(N) on average (the attacker must average many
samples per probe)".

Toggle: env var ``RULES_RANDOMIZE_THRESHOLDS`` (default ``"true"``).
Set to ``"false"`` for deterministic test runs (none of the existing tests
assert on boundary behaviour at the ±₹500 scale, so the default stays on).
"""
from __future__ import annotations

import os
import random
import threading
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Env flag (kept here next to the code that consumes it — same pattern as
# ``src/api/security.py``).
# ---------------------------------------------------------------------------


def _randomize_thresholds_enabled() -> bool:
    raw = os.environ.get("RULES_RANDOMIZE_THRESHOLDS")
    if raw is None or raw == "":
        return True  # default ON — anti-evasion defence
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Fields where the value is a monetary INR amount. The jitter applies to
# numeric ``gt``/``lt`` rules on these fields ONLY — categorical rules
# (``address_quality eq "vague"``) and non-monetary numerics (``items``,
# ``prior_orders``) are left untouched so the demo's rule-toggling
# behaviour (RULE-002 etc.) stays deterministic.
_MONETARY_FIELDS: frozenset[str] = frozenset({
    "amount_inr",
    "order_value_inr",
    "OrderValue",
    "max_amount_inr",
})

# ±₹500 jitter per the IEEE Access 2024 spec. Tight enough that legit
# high-value orders (₹50,001) still trip the rule on the +500 sample,
# wide enough that the boundary is genuinely fuzzy to an attacker.
_JITTER_AMPLITUDE: float = 500.0


def _jitter_threshold(field: str, value: object) -> object:
    """Apply ±₹500 jitter to a numeric threshold on a monetary field.

    Returns the original ``value`` unchanged when:
      * ``RULES_RANDOMIZE_THRESHOLDS=false`` is set in the env, OR
      * ``field`` is not in ``_MONETARY_FIELDS`` (categorical or non-amount
        numeric like ``items``), OR
      * ``value`` can't be coerced to float (defensive — never raises).
    """
    if not _randomize_thresholds_enabled():
        return value
    if field not in _MONETARY_FIELDS:
        return value
    try:
        base = float(value)
    except (TypeError, ValueError):
        return value
    # ``uniform(-A, +A)`` — symmetric ±amplitude. Returns float; the
    # comparison below coerces both operands via ``float(...)`` anyway.
    return base + random.uniform(-_JITTER_AMPLITUDE, _JITTER_AMPLITUDE)


@dataclass
class Rule:
    rule_id: str
    name: str
    field: str
    op: str  # gt | lt | eq | in
    value: object
    action: str  # BLOCK | REVIEW
    priority: int = 100
    active: bool = True
    created_by: str = "system"


DEFAULT_RULES: list[Rule] = [
    Rule(
        rule_id="RULE-001",
        name="High-value COD new customer",
        field="amount_inr",
        op="gt",
        value=50_000,
        action="BLOCK",
        priority=1,
    ),
    Rule(
        rule_id="RULE-002",
        name="High-value vague address COD",
        field="_high_value_vague_cod",
        op="eq",
        value=True,
        action="REVIEW",
        priority=10,
    ),
]


def _derived_fields(order: dict) -> dict:
    o = dict(order)
    o["_high_value_vague_cod"] = (
        order.get("address_quality") == "vague"
        and str(order.get("payment_method", "")).upper() == "COD"
        and float(order.get("amount_inr", 0)) > 20_000
    )
    return o


class RulesEngine:
    def __init__(self) -> None:
        self._rules: list[Rule] = list(DEFAULT_RULES)
        self._lock = threading.Lock()

    def evaluate(self, order: dict) -> Rule | None:
        o = _derived_fields(order)
        with self._lock:
            rules = sorted(
                [r for r in self._rules if r.active], key=lambda r: r.priority
            )
        for r in rules:
            actual = o.get(r.field)
            if actual is None:
                continue
            try:
                # P0-2 — apply ±₹500 jitter to numeric thresholds on
                # monetary fields (amount_inr) ONLY. Categorical rules
                # (``op="eq"``, ``op="in"``) + numeric rules on
                # non-monetary fields (``items``, ``prior_orders``) keep
                # the deterministic original threshold.
                if r.op in ("gt", "lt"):
                    effective_threshold = _jitter_threshold(r.field, r.value)
                    if r.op == "gt" and float(actual) > float(effective_threshold):
                        return r
                    if r.op == "lt" and float(actual) < float(effective_threshold):
                        return r
                else:
                    if r.op == "eq" and actual == r.value:
                        return r
                    if r.op == "in" and actual in r.value:
                        return r
            except (TypeError, ValueError):
                continue
        return None

    def add(self, rule: Rule) -> None:
        with self._lock:
            self._rules.append(rule)

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.rule_id != rule_id]
            return len(self._rules) < before

    def list_active(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "rule_id": r.rule_id,
                    "name": r.name,
                    "field": r.field,
                    "op": r.op,
                    "value": r.value,
                    "action": r.action,
                    "priority": r.priority,
                }
                for r in sorted(self._rules, key=lambda x: x.priority)
            ]
