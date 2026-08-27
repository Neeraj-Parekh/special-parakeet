"""Human-in-the-loop review queue. REVIEW decisions auto-open a case."""
from __future__ import annotations

import uuid

from src.audit.logger import AuditLogger


class CaseService:
    def __init__(self, path: str = "out/cases.jsonl"):
        self.store = AuditLogger(path)

    def open_case(
        self,
        prediction_id: str,
        order_id: str,
        priority: str = "MEDIUM",
        reason: str = "model_review_gate",
        actor: str = "system",
    ) -> str:
        case_id = f"CASE-{uuid.uuid4().hex[:10]}"
        self.store.log(
            {
                "case_id": case_id,
                "prediction_id": prediction_id,
                "order_id": order_id,
                "event": "OPENED",
                "status": "OPEN",
                "priority": priority,
                "reason": reason,
                "actor": actor,
            }
        )
        return case_id

    def resolve(self, case_id: str, decision: str, notes: str, actor: str) -> dict:
        if decision not in {"APPROVED", "REJECTED", "ESCALATED"}:
            raise ValueError(f"invalid resolution: {decision}")
        self.store.log(
            {
                "case_id": case_id,
                "event": "RESOLVED",
                "status": decision,
                "notes": notes,
                "actor": actor,
            }
        )
        return {"case_id": case_id, "status": decision}

    def _latest(self, events: list[dict]) -> dict | None:
        by_id: dict[str, dict] = {}
        order: list[str] = []
        for ev in events:
            cid = ev.get("case_id")
            if cid not in by_id:
                order.append(cid)
            merged = by_id.get(cid, {})
            merged.update(ev)
            by_id[cid] = merged
        for cid in order:
            by_id[cid]["resolution"] = (
                by_id[cid].get("status") if by_id[cid].get("event") == "RESOLVED" else None
            )
        return None if not order else None  # placeholder, real return below

    def list_cases(self, status: str | None = None) -> list[dict]:
        if not self.store.path.exists():
            return []
        raw = self.store.path.read_text().splitlines()
        events = [json_loads(line) for line in raw if line.strip()]
        merged: dict[str, dict] = {}
        order: list[str] = []
        for ev in events:
            cid = ev["case_id"]
            if cid not in merged:
                order.append(cid)
                merged[cid] = dict(ev)
            else:
                merged[cid].update(ev)
        out = [merged[cid] for cid in order]
        if status:
            out = [c for c in out if c.get("status") == status]
        return out


def json_loads(line: str) -> dict:
    import json

    return json.loads(line)
