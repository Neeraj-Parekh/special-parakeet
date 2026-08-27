"""Human-in-the-loop review queue. REVIEW decisions auto-open a case.

Dual-mode (Day 2 Track E):
  * Postgres (DATABASE_URL set): one row per case in the ``cases`` table.
    open_case = INSERT; resolve = UPDATE; list_cases = SELECT.
  * File fallback: the original JSONL-of-events + Python merge in list_cases.

Status values across both modes: OPENED, UNDER_REVIEW, APPROVED, REJECTED,
ESCALATED.
"""
from __future__ import annotations

import json
import threading
import uuid

from src.audit.logger import AuditLogger


class CaseService:
    def __init__(self, path: str = "out/cases.jsonl"):
        from src.config import get_settings

        self.settings = get_settings()
        self._lock = threading.Lock()

        if self.settings.is_postgres:
            import psycopg

            # Separate table (``cases``) — the migration puts it next to
            # ``audit_records``. The original file-mode reused AuditLogger as
            # a JSONL store; the table version is cleaner (one row per case,
            # no event-merge step in list_cases).
            self._conn = psycopg.connect(self.settings.database_url, autocommit=False)
            self.store = AuditLogger(path)  # backward-compat (.path attr)
        else:
            self._conn = None
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
        if self._conn is not None:
            return self._open_postgres(
                case_id, prediction_id, order_id, priority, reason, actor
            )
        # File mode — original behaviour, unchanged.
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
        if self._conn is not None:
            return self._resolve_postgres(case_id, decision, notes, actor)
        # File mode — original behaviour, unchanged.
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

    # _latest() was a placeholder stub — removed by Track B (Day 1). The
    # original method always returned None and was never called by any
    # caller (grep `_latest` in src/ + scripts/ returns zero references).
    # If you need "latest case for an order", use list_cases + take the
    # last entry; or in Postgres mode, run
    #   SELECT * FROM cases WHERE order_id = %s ORDER BY created_at DESC LIMIT 1

    def list_cases(self, status: str | None = None) -> list[dict]:
        if self._conn is not None:
            return self._list_postgres(status)
        # File mode — original behaviour, unchanged.
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

    # ------------------------------------------------------------------ #
    # Postgres mode                                                      #
    # ------------------------------------------------------------------ #

    def _open_postgres(
        self,
        case_id: str,
        prediction_id: str,
        order_id: str,
        priority: str,
        reason: str,
        actor: str,
    ) -> str:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cases
                      (case_id, prediction_id, order_id, status, priority,
                       reason, assigned_to, resolution_decision)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case_id,
                        prediction_id,
                        order_id,
                        "OPENED",
                        priority,
                        reason,
                        actor,
                        None,
                    ),
                )
                self._conn.commit()
        return case_id

    def _resolve_postgres(
        self, case_id: str, decision: str, notes: str, actor: str
    ) -> dict:
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cases
                       SET status = %s,
                           resolution_decision = %s,
                           resolution_notes = %s,
                           resolution_by = %s,
                           resolved_at = NOW(),
                           updated_at = NOW()
                     WHERE case_id = %s
                    """,
                    (decision, decision, notes, actor, case_id),
                )
                if cur.rowcount == 0:
                    self._conn.rollback()
                    raise ValueError(f"case not found: {case_id}")
                self._conn.commit()
        return {"case_id": case_id, "status": decision}

    def _list_postgres(self, status: str | None) -> list[dict]:
        with self._conn.cursor() as cur:
            if status is None:
                cur.execute(
                    """
                    SELECT case_id, prediction_id, order_id, status, priority,
                           reason, assigned_to, created_at, resolved_at,
                           resolution_notes, resolution_by, resolution_decision
                      FROM cases
                     ORDER BY created_at DESC
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT case_id, prediction_id, order_id, status, priority,
                           reason, assigned_to, created_at, resolved_at,
                           resolution_notes, resolution_by, resolution_decision
                      FROM cases
                     WHERE status = %s
                     ORDER BY created_at DESC
                    """,
                    (status,),
                )
            rows = cur.fetchall()
        cols = [
            "case_id", "prediction_id", "order_id", "status", "priority",
            "reason", "assigned_to", "created_at", "resolved_at",
            "resolution_notes", "resolution_by", "resolution_decision",
        ]
        out: list[dict] = []
        for row in rows:
            d = dict(zip(cols, row))
            # datetime → ISO 8601 so the JSON-serializable contract holds
            # across both modes (file-mode stores string timestamps).
            for k in ("created_at", "resolved_at"):
                if d.get(k) is not None:
                    d[k] = d[k].isoformat()
            out.append(d)
        return out


def json_loads(line: str) -> dict:
    return json.loads(line)
