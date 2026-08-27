"""Deterministic rules engine evaluated before ML. Ops-tunable, no redeploy needed."""
from __future__ import annotations

import threading
from dataclasses import dataclass


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
                if r.op == "gt" and float(actual) > float(r.value):
                    return r
                if r.op == "lt" and float(actual) < float(r.value):
                    return r
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
