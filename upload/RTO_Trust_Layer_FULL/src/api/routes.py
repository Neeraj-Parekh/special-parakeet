"""Train + evaluate RTO risk model. Prints JSON metrics; writes model + report."""
from __future__ import annotations

import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api.breaker import CircuitBreaker  # noqa: E402
from src.api.mandates import MandateVerdict, issue_mandate, verify_mandate  # noqa: E402
from src.api.metrics import Metrics, now_ms  # noqa: E402
from src.api.security import TokenBucket, bearer_token, check_key, default_keys  # noqa: E402
from src.audit.logger import AuditLogger, redact_customer  # noqa: E402
from src.business.cost_optimizer import optimal_decision  # noqa: E402
from src.cases.service import CaseService  # noqa: E402
from src.features.cleaning import load_orders  # noqa: E402
from src.features.enrich import add_address_features  # noqa: E402
from src.ml.registry import current_champion, psi  # noqa: E402
from src.models.explain import reason_codes_batch  # noqa: E402
from src.models.splitting import group_split  # noqa: E402
from src.models.train import build_feature_frame, fit_model, save_model  # noqa: E402
from src.rules.engine import (
    Rule,  # noqa: E402
    RulesEngine,  # noqa: E402
)

ACCEPT_T, REJECT_T = 0.15, 0.60


class OrderIn(BaseModel):
    order_id: str = Field(min_length=3, max_length=64)
    amount_inr: float = Field(gt=1, le=1_000_000)
    category: str = Field(min_length=2, max_length=32)
    customer_id: str = Field(min_length=3, max_length=64)
    address_quality: str = Field(default="complete", pattern="^(complete|partial|vague)$")
    city_tier: str = Field(default="tier_2", pattern="^tier_[123]$")
    payment_method: str = Field(default="COD", pattern="^(COD|Prepaid)$", max_length=16)
    prior_orders: int = Field(default=0, ge=0, le=10_000)
    prior_returns: int = Field(default=0, ge=0, le=10_000)
    items: int = Field(default=1, ge=1, le=100)
    order_hour: int = Field(default=12, ge=0, le=23)
    device: str = Field(default="Android App", max_length=32)


class RuleIn(BaseModel):
    rule_id: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=3, max_length=64)
    field: str = Field(max_length=48)
    op: str = Field(pattern="^(gt|lt|eq|in)$")
    value: float | str | bool | list
    action: str = Field(pattern="^(BLOCK|REVIEW)$")
    priority: int = Field(default=100, ge=1, le=1000)
    created_by: str = "admin"


def create_app(
    scorer_rate_per_min: int = 120, audit_path: str = "out/audit.jsonl"
) -> FastAPI:
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        df = add_address_features(load_orders("data/raw/cod_orders.csv"))
        train_df, _ = group_split(df)
        X_tr, y_tr = build_feature_frame(train_df, "order+addr")
        model_path = Path("out/model_api.joblib")
        if not model_path.exists():
            save_model(fit_model(X_tr, y_tr), str(model_path))
        from src.models.train import load_model

        state["model"] = load_model(str(model_path))
        state["reference"] = X_tr.mode().iloc[0]
        state["base_rate"] = float(y_tr.mean())
        state["audit"] = AuditLogger(audit_path)
        state["keys"] = default_keys()
        state["bucket"] = TokenBucket(scorer_rate_per_min)
        state["rules"] = RulesEngine()
        state["breaker"] = CircuitBreaker()
        state["metrics"] = Metrics()
        state["cases"] = CaseService("out/cases.jsonl")
        num_cols = [c for c in X_tr.columns if str(X_tr[c].dtype) != "category"]
        state["psi_sample"] = {
            c: X_tr[c].dropna().sample(n=min(2000, len(X_tr)), random_state=7).tolist()
            for c in num_cols
        }
        state["idem"] = {}
        yield

    app = FastAPI(title="RTO Trust Layer", version="0.2.0", lifespan=lifespan)
    app.state.core = state

    def to_frame(o: OrderIn) -> pd.DataFrame:
        row = {
            "log_order_value": _log1p(o.amount_inr),
            "discount_pct": 0.0,
            "Items": o.items,
            "OrderDay": 180,
            "OrderHour": o.order_hour,
            "PriorOrders": o.prior_orders,
            "PriorReturns": o.prior_returns,
            "is_cod": int(o.payment_method.upper() == "COD"),
            "category": o.category,
            "device": o.device,
            "city_tier": o.city_tier,
            "address_quality": o.address_quality,
        }
        return pd.DataFrame([row])

    @app.post("/risk/score")
    def score(
        order: OrderIn,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
        x_mandate: str | None = Header(default=None),
    ) -> dict:
        token = bearer_token(authorization)
        ok, err = check_key(token, "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        client = token
        if not state["bucket"].allow(client):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

        cache_key = (idempotency_key or "", order.model_dump_json())
        if idempotency_key and cache_key in state["idem"]:
            return dict(state["idem"][cache_key], replayed=True)

        try:
            t0 = time.monotonic()
            mandate_verdict, _payload = verify_mandate(x_mandate, order.amount_inr)

            fired = state["rules"].evaluate(order.model_dump())
            proba = None
            decision = None
            reasons: list[dict] = []
            degraded = False
            if fired is not None and fired.action == "BLOCK":
                proba = None
                rule_fired = fired.rule_id
                decision = "REJECT"
            else:
                use_model = state["breaker"].allow_attempt()
                if use_model:
                    try:
                        X, _ = build_feature_frame(to_frame(order), "order+addr")
                        reasons = reason_codes_batch(
                            state["model"],
                            X,
                            list(X.columns),
                            state["base_rate"],
                            state["reference"],
                        )
                        proba = float(state["model"].predict_proba(X)[0, 1])
                        state["breaker"].record_success()
                    except Exception:
                        state["breaker"].record_failure()
                        proba = None
                if proba is None:
                    degraded = True

            rule_fired = fired.rule_id if fired else None
            breach_note = None
            if proba is not None:
                if proba < ACCEPT_T:
                    decision = "ACCEPT"
                elif proba > REJECT_T:
                    decision = "REJECT"
                else:
                    decision = "REVIEW"
            elif decision is None:
                decision = "REVIEW"

            if fired is not None and fired.action == "REVIEW" and decision == "ACCEPT":
                decision = "REVIEW"

            if mandate_verdict == MandateVerdict.BREACH:
                decision = "REJECT"
                breach_note = "mandate_amount_breach"
            elif mandate_verdict != MandateVerdict.VALID:
                if x_mandate is not None:
                    decision = "REJECT"
                    breach_note = f"mandate_{mandate_verdict}"

            policy_hint = None
            if proba is not None:
                policy_hint = optimal_decision(proba)[0]

            features_used = (
                {c: float(X.iloc[0][c]) for c in X.columns if str(X[c].dtype) != "category"}
                if proba is not None
                else {}
            )
            case_id = None
            if decision == "REVIEW":
                case_id = state["cases"].open_case(
                    prediction_id="pending", order_id=order.order_id,
                    reason="review_gate" if fired is None else f"rule:{fired.rule_id}",
                )
            audit_id = state["audit"].log(
                {
                    "request": {
                        **order.model_dump(),
                        "customer_id": redact_customer(order.customer_id),
                    },
                    "probability": round(proba, 5) if proba is not None else None,
                    "decision": decision,
                    "reason_codes": reasons[:5],
                    "mandate_verdict": mandate_verdict,
                    "breach_note": breach_note,
                    "rule_fired": rule_fired,
                    "degraded": degraded,
                    "features_used": features_used,
                    "latency_ms": now_ms(t0),
                    "case_id": case_id,
                }
            )
            body = {
                "prediction_id": str(uuid.uuid4()),
                "risk_score": round(proba * 100, 1) if proba is not None else None,
                "probability": round(proba, 4) if proba is not None else None,
                "decision": decision,
                "gate_thresholds": {"accept_below": ACCEPT_T, "reject_above": REJECT_T},
                "explanation": reasons[:5],
                "rule_fired": rule_fired,
                "degraded": degraded,
                "policy_hint": policy_hint,
                "model_version": "rules_only"
                if degraded
                else (current_champion() or {"version": state["audit"].model_version})["version"],
                "latency_ms": now_ms(t0),
                "case_id": case_id,
                "mandate": {"verdict": mandate_verdict, "note": breach_note},
                "audit_trail_url": f"/audit/{audit_id}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            state["metrics"].inc(
                "risk_decisions_total", {"decision": decision, "degraded": str(degraded)}
            )
            state["metrics"].observe_latency(now_ms(t0) / 1000)
            if idempotency_key:
                state["idem"][cache_key] = body
            return body
        except HTTPException:
            raise
        except Exception as e:  # no internal detail leakage
            incident = uuid.uuid4()
            print(f"incident={incident} scoring_failed={type(e).__name__}: {e}", file=sys.stderr)
            raise HTTPException(status_code=500, detail=f"internal_error incident={incident}")

    @app.get("/metrics")
    def prometheus_metrics() -> dict:
        from fastapi import Response

        m: Metrics = state["metrics"]
        state_map = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}
        m.gauge("rto_circuit_state", state_map[state["breaker"].state])
        return Response(content=m.render(), media_type="text/plain; version=0.0.4")

    @app.get("/v1/cases")
    def list_cases(
        status: str | None = None, authorization: str | None = Header(default=None)
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        return {"cases": state["cases"].list_cases(status)}

    @app.post("/v1/cases/{case_id}/resolve")
    def resolve_case(
        case_id: str,
        decision: str,
        notes: str = "",
        authorization: str | None = Header(default=None),
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=403, detail="case resolution requires admin scope")
        try:
            out = state["cases"].resolve(case_id, decision.upper(), notes, actor="admin")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return out

    @app.get("/v1/models/current")
    def models_current(authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        return {"champion": current_champion()}

    @app.get("/v1/models/drift")
    def models_drift(authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        recent = [
            r.get("features_used", {})
            for r in _read_audit_tail(state["audit"], limit=300)
            if r.get("features_used")
        ]
        if len(recent) < 30:
            return {"status": "insufficient_data", "observed": len(recent), "psi": {}}
        report = {}
        for col, ref in state["psi_sample"].items():
            obs = [r[col] for r in recent if col in r]
            if len(obs) >= 10:
                report[col] = round(psi(ref, obs), 4)
        worst = max(report.values(), default=0.0)
        status = "OK" if worst < 0.1 else ("WARNING" if worst <= 0.25 else "CRITICAL")
        return {"status": status, "n_observed": len(recent), "psi": report}

    @app.get("/v1/compliance/audit-export")
    def audit_export(authorization: str | None = Header(default=None)) -> dict:
        import csv
        import io

        from fastapi import Response

        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        records = _read_audit_tail(state["audit"], limit=100000)
        buf = io.StringIO()
        if records:
            w = csv.DictWriter(buf, fieldnames=list(records[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(records)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="audit-export-{stamp}.csv"'},
        )

    @app.get("/v1/compliance/model-card")
    def model_card(authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        champ = current_champion()
        return {
            "model_name": "RTO Trust Layer scorer",
            "model_type": "HistGradientBoostingClassifier (sklearn)",
            "version": champ["version"] if champ else state["audit"].model_version,
            "metrics_at_registration": champ["metrics"] if champ else {},
            "training_data": (
                "CODScore synthetic-but-realistic COD orders "
                "(7235 rows); real-data upgrade path documented"
            ),
            "label_definition": "DeliveryStatus == Returned -> is_returned=1",
            "split_discipline": "customer-grouped holdout; group leakage asserted 0",
            "primary_metric": "PR-AUC (class imbalance ~23% positives)",
            "intended_use": "pre-dispatch COD return-risk gating with human-in-the-loop review",
            "limitations": [
                "synthetic training data; validate before production use",
                "no address-string features in this dataset revision",
                "state-level geo features showed no lift (see E3)",
            ],
            "ethical_notes": "defense-only tool; every decision explainable + hash-chained audited",
        }

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model_loaded": state.get("model") is not None,
            "circuit_state": state["breaker"].state,
            "active_rules": len(state["rules"].list_active()),
            "version": app.version,
        }

    @app.get("/v1/rules")
    def list_rules(authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        return {"rules": state["rules"].list_active()}

    @app.post("/v1/rules")
    def add_rule(rule: RuleIn, authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=403, detail="rule changes require admin scope")
        state["rules"].add(
            Rule(
                rule_id=rule.rule_id,
                name=rule.name,
                field=rule.field,
                op=rule.op,
                value=rule.value,
                action=rule.action,
                priority=rule.priority,
                created_by=rule.created_by,
            )
        )
        state["audit"].log(
            {"request": {"rule_id": rule.rule_id}, "decision": "RULE_ADDED"}
        )
        return {"added": rule.rule_id}

    @app.delete("/v1/rules/{rule_id}")
    def delete_rule(rule_id: str, authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=403, detail="rule changes require admin scope")
        removed = state["rules"].remove(rule_id)
        return {"removed": removed}

    @app.get("/v1/policy/optimal")
    def policy_optimal(
        probability: float,
        c_fp: float = 50,
        c_fn: float = 600,
        authorization: str | None = Header(default=None),
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        if not (0 <= probability <= 1):
            raise HTTPException(status_code=422, detail="probability must be in [0,1]")
        decision, costs = optimal_decision(probability, c_fp=c_fp, c_fn=c_fn)
        return {"probability": probability, "optimal_action": decision, "expected_costs": costs}

    @app.get("/v1/audit/verify-chain")
    def verify_chain(authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        chain_ok, n, bad_id = state["audit"].verify_chain()
        return {"intact": chain_ok, "records_checked": n, "first_bad_audit_id": bad_id}

    @app.post("/v1/mandates")
    def create_mandate(
        customer_ref: str,
        max_amount_inr: float,
        ttl_seconds: int = 3600,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Merchant backend (admin scope) mints bounded agent mandates."""
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        if not (1 <= max_amount_inr <= 1_000_000):
            raise HTTPException(status_code=422, detail="mandate bound out of range")
        if not (30 <= ttl_seconds <= 86_400):
            raise HTTPException(status_code=422, detail="ttl out of range")
        return {
            "mandate": issue_mandate(customer_ref, max_amount_inr, ttl_seconds),
            "max_amount_inr": max_amount_inr,
            "ttl_seconds": ttl_seconds,
            "note": "agents cannot mint or widen mandates",
        }

    @app.post("/risk/{prediction_id}/override")
    def override(
        prediction_id: str,
        new_decision: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Human-in-the-loop overrides are admin-scope only. Agents can never self-approve."""
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(
                status_code=403, detail="decision override requires admin scope"
            )
        if new_decision not in {"ACCEPT", "REVIEW", "REJECT"}:
            raise HTTPException(status_code=422, detail="invalid decision")
        audit_id = state["audit"].log(
            {
                "request": {"prediction_id": prediction_id},
                "decision": new_decision,
                "breach_note": "manual_override_by_admin",
            }
        )
        return {"overridden": prediction_id, "new_decision": new_decision, "audit_id": audit_id}

    @app.get("/audit/{audit_id}")
    def get_audit(
        audit_id: str, authorization: str | None = Header(default=None)
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        rec = state["audit"].read(audit_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="audit record not found")
        return rec

    dashboard_dir = Path(__file__).resolve().parents[2] / "dashboard"
    if dashboard_dir.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount(
            "/dashboard",
            StaticFiles(directory=str(dashboard_dir), html=True),
            name="dashboard",
        )

    return app


def _log1p(x: float) -> float:
    import math

    return math.log1p(x)


def _read_audit_tail(audit_logger, limit: int = 300) -> list[dict]:
    import json as _json

    if not audit_logger.path.exists():
        return []
    lines = audit_logger.path.read_text().splitlines()
    return [_json.loads(line) for line in lines[-limit:] if line.strip()]
