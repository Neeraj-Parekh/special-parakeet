"""Train + evaluate RTO risk model. Prints JSON metrics; writes model + report."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Day 2 Track E — dual-mode (Postgres + file fallback) config + idempotency.
# cachetools provides TTLCache for the file-mode idempotency cache (closes
# §A item 2 — unbounded ``state["idem"]`` dict memory leak). Postgres mode
# uses the ``idempotency_keys`` table instead.
from cachetools import TTLCache  # noqa: E402

from src.api.breaker import CircuitBreaker  # noqa: E402
from src.api.mandates import MandateVerdict, issue_mandate, verify_mandate  # noqa: E402
from src.api.metrics import Metrics, now_ms  # noqa: E402
from src.api.security import TokenBucket, bearer_token, check_key, default_keys  # noqa: E402
from src.audit.logger import AuditLogger, redact_customer  # noqa: E402
from src.business.cost_optimizer import (  # noqa: E402
    DEFAULT_INTERVENTION_WEIGHTS,
    bootstrap_cost_ci,
    calibrate_probabilities,
    cost_curve_sweep,
    find_cost_crossover,
    find_intervention_crossover,
    intervention_curve_sweep,
    optimal_decision,
    optimal_intervention,
)
from src.config import get_settings  # noqa: E402
# Day 2 Track F — Redis Streams producer (fire-and-forget). Closes §A item
# 18 + driver G2 (REST-only, no event/streaming backbone). Lazy connect:
# StreamProducer(None) is a no-op so the 63 existing tests still pass without
# a Redis fixture. The 5 stream-name constants come from the producer module
# so consumer.py + processor.py share the same source of truth (V2 §5).
from src.stream.producer import (  # noqa: E402
    STREAM_AUDIT_RECORDS,
    STREAM_CASES_CREATED,
    STREAM_RISK_SCORES,
    StreamProducer,
)
# Day 4 Track M — OpenTelemetry tracer setup. Dual-mode like Track E's
# DATABASE_URL + Track F's REDIS_URL: if OTEL_EXPORTER_OTLP_ENDPOINT is
# unset, setup_otel() returns None + the /risk/score handler skips the
# span block. The 93 existing tests pass without a Jaeger fixture.
from src.api.otel import setup_otel  # noqa: E402
# Day 2 Track G — LabelFeedbackService wraps DDM + ADWIN over the delayed
# is_returned label stream (Gama 2014 survey §3.2/§3.3). On DRIFT, fires a
# retrain_request notification (the MLOps-DevOps paper's
# plan_drift_triggered_retraining capability). The service is constructed
# once at app boot so the in-memory DDM/ADWIN state persists across requests
# within one worker process.
from src.feedback.label_service import LabelFeedbackService  # noqa: E402

# Legacy static-threshold constants (replaced as the PRIMARY decision path on
# Day 1 Track C by ``optimal_decision()`` — Bahnsen Bayes Minimum Risk, ICMLA
# 2013, DOI 10.1109/ICMLA.2013.68). Kept here for backward compatibility in
# the ``gate_thresholds`` response field (so existing dashboards / consumers
# that read accept_below / reject_above still get a sensible value), and as
# the fallback threshold surface when ``optimal_decision`` is somehow skipped
# (degraded mode below falls back to REVIEW regardless).
ACCEPT_T, REJECT_T = 0.15, 0.60
# Cost weights for the Bahnsen BMR decision layer — must stay in lockstep
# with the defaults in ``src.business.cost_optimizer.optimal_decision`` so that
# the ``/v1/policy/cost-curves`` endpoint reports the same cost model the
# live decision path actually uses.
DEFAULT_COST_WEIGHTS: dict[str, float] = {
    "c_fp": 50.0,            # false-positive (good order held) admin / review fee, INR
    "c_fn": 600.0,           # false-negative (missed RTO) reverse-logistics + refund, INR
    "c_otp": 5.0,            # REVIEW-gate selective-OTP verification cost, INR
    "c_block": 1000.0,       # false-block (good order blocked) goodwill / churn, INR
    "otp_effectiveness": 0.82,  # published selective-OTP RTO-catch rate (0.78-0.84)
}
from src.cases.service import CaseService  # noqa: E402
from src.features.cleaning import load_orders  # noqa: E402
from src.features.enrich import add_address_features  # noqa: E402
from src.ml.registry import (  # noqa: E402
    _close_conn as _close_registry_conn,
    current_champion,
    get_priors,
    psi,
    register_model,
)
# Day 6 Track P (T1.5) — server-side enforcement of the 7-action agent
# allowlist (Mission 3: "Agent can only call N APIs. Any other intent
# returns 'Action not permitted.'"). Imported here so the
# ``enforce_agent_action`` FastAPI dependency can consult the canonical
# allowlist + ``check_agent_action`` gate. Source: Mao 2026 SoK, D2
# (transaction-authorization: design mandates as scoped, task-bound,
# attenuating credentials rather than standing broad authority).
from src.api.agent_allowlist import (  # noqa: E402
    ALLOWED_ACTIONS,
    check_agent_action,
)
from src.models.explain import reason_codes_batch  # noqa: E402
from src.models.splitting import group_split  # noqa: E402
from src.models.train import build_feature_frame, fit_model, save_model  # noqa: E402
from src.rules.engine import (
    Rule,  # noqa: E402
    RulesEngine,  # noqa: E402
)

# Default bootstrap CI parameters for the cost-curve endpoint (Drummond & Holte
# 2006 — skill.yaml recommends ≥500 resamples preserving row marginals).
DEFAULT_COST_CURVE_RESAMPLES = 500
DEFAULT_COST_CURVE_CONFIDENCE = 0.90


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
    # Day 6 Track U (T2.3) — per-merchant traceability. ``merchant_id``
    # is the multi-tenant key for the ``/v1/usage`` metering endpoint's
    # per-merchant GROUP BY (V3 §10.4 — multi-tenant isolation). Defaults
    # to None so the 117 pre-Track-U tests that don't pass it still work
    # (the audit body carries ``merchant_id: null`` and the per-merchant
    # ``/v1/usage`` query returns aggregate when the query param is
    # absent — same as before).
    merchant_id: str | None = Field(default=None, max_length=64)


class RuleIn(BaseModel):
    rule_id: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=3, max_length=64)
    field: str = Field(max_length=48)
    op: str = Field(pattern="^(gt|lt|eq|in)$")
    value: float | str | bool | list
    action: str = Field(pattern="^(BLOCK|REVIEW)$")
    priority: int = Field(default=100, ge=1, le=1000)
    created_by: str = "admin"


class FeedbackIn(BaseModel):
    """Delayed is_returned label (Track G Day 2 — closes §A item 18 feedback
    loop + driver G3 partial). Auth: admin scope (merchants can't self-report
    labels — prevents poisoning). Source: Gama 2014 ACM CSUR 46(4) §6.
    """
    prediction_id: str = Field(min_length=1, max_length=128)
    is_returned: bool
    # ISO 8601 timestamp the label was recorded at (chargeback-style delay
    # — days-weeks after the prediction was made). If None, the endpoint uses
    # ``datetime.now(timezone.utc)``. Used by future prequential evaluation
    # (Gama 2014 §5 — interleaved test-then-train with landmark window).
    returned_at: str | None = None


# Day 2 Track H — V3 §12.1 dual-control override (closes §A item 16 +
# §C T10). The override endpoint below accepts BOTH:
#   * JSON body (the V3-recommended dual-control form): two admin API
#     keys in the payload, both must be valid + different — no
#     self-approval possible.
#   * Legacy query-param form (Track D backward-compat):
#     ``?new_decision=...`` + single ``Authorization: Bearer <admin-key>``
#     header. Track D's ``test_admin_can_override`` +
#     ``test_agent_cannot_self_approve`` still pass on this path.
# The dual-control path is the recommended one for new dashboard wiring;
# the legacy path is retained for gradual migration + so the Track D
# test suite is untouched.
# Day 6 Track V (T1.1) — the dual-control path now uses a REAL HMAC
# chain: signature_2 = HMAC(admin2_key, signature_1 || canonical_body ||
# timestamp). A single-admin compromise cannot forge a dual-control
# override because the second signature is cryptographically bound to
# the first (admin1's key alone is useless; admin2's key alone is
# useless — both must collude or both must be compromised to forge).
class OverrideIn(BaseModel):
    """V3 §12.1 dual-control override request body.

    Both admin_signature_1 + admin_signature_2 must be valid admin-scope
    API keys, AND they must be DIFFERENT (a single admin cannot
    self-approve — the contradiction with the old single-admin endpoint
    that V3 §12.1 calls out). The endpoint records both signatures in
    the audit hash chain so the dual-control trail is tamper-evident.

    Day 6 Track V (T1.1) — signature_2 is now an HMAC output, NOT a
    second raw admin API key. The chain is::

        canonical_body = json.dumps({
            "prediction_id": prediction_id,
            "decision": decision,
            "notes": notes,
        }, sort_keys=True)
        chained_msg = signature_1 + "|" + canonical_body + "|" + str(timestamp)
        signature_2 = HMAC(admin2_key, chained_msg, sha256).hexdigest()

    The client passes ``timestamp`` so the server can recompute the
    chained message identically. If ``timestamp`` is None, the server
    uses ``int(time.time())`` at audit-write time AND tries ±30 seconds
    for clock skew (the client must compute signature_2 within that
    window).
    """

    decision: str = Field(
        pattern="^(ACCEPT|REVIEW|REJECT|APPROVED|REJECTED|ESCALATED)$"
    )
    notes: str = Field(default="", max_length=2000)
    admin_signature_1: str = Field(min_length=1, max_length=256)
    admin_signature_2: str = Field(min_length=1, max_length=256)
    # T1.1 — the unix timestamp (seconds) the client used to compute
    # admin_signature_2. Optional; if None the server uses
    # ``int(time.time())`` + a ±30-second clock-skew window.
    timestamp: int | None = Field(default=None, ge=0)


# Day 2 Track H — V3 §10.3 + §A items 15, 16 + §C T10. The SimulateIn
# model is the request body for ``POST /v1/simulate`` (the dry-run
# policy explorer; merchant "what-if" tuning without writing to the
# audit hash chain or opening a case). Source: SoK Mao 2026 capability
# ``recommend_layered_defenses`` (the policy explorer is the
# market-simulation complement to layer 5's compliance monitoring).
class SimulateIn(BaseModel):
    """Dry-run policy simulation request body. ``dry_run=True`` is forced
    server-side so a merchant with a scorer-scope key can probe the
    pipeline without consuming audit-log / case-queue / streaming
    capacity — useful for cost-tuning before flipping a live policy.
    """
    order: OrderIn
    mandate: str | None = None
    dry_run: bool = True


def create_app(
    scorer_rate_per_min: int = 120, audit_path: str = "out/audit.jsonl"
) -> FastAPI:
    state: dict[str, Any] = {}

    # Day 2 Track E — load Settings once at app-construction time. The
    # ``database_url`` field selects between Postgres mode (full DB-backed
    # audit/cases/registry/idempotency) and file-mode fallback (the 63
    # existing tests run this way). The Settings object itself is cached
    # via ``@lru_cache`` in ``src.config`` so we don't re-read env vars on
    # every TestClient build.
    settings = get_settings()
    state["settings"] = settings

    # Day 2 Track F — Redis Streams producer (fire-and-forget). Constructed
    # eagerly here (cheap — just stores the URL; lazy-connects on first
    # ``publish``) so the /risk/score handler can call it after the audit
    # write + case open. When ``settings.redis_url`` is None (test mode,
    # local dev without REDIS_URL) the producer is a no-op: ``publish()``
    # returns None silently — the API response is unaffected. This is the
    # pragmatic fire-and-forget contract; V3 §10.3 prescribes a full
    # transactional outbox table drained by a worker (deferred; see
    # worklog Day 2 Track F deferral list).
    state["stream"] = StreamProducer(settings.redis_url)

    # Day 2 Track G — Metrics is constructed eagerly here so it can be
    # wired into the LabelFeedbackService below (Day 6 Track S T2.2-helper —
    # Gama 2014 §5 detector-quality metric emission: detection-delay +
    # false-alarm-run-length on every DRIFT detection / WARNING→STABLE
    # revert). Moving the Metrics instantiation out of the lifespan
    # function (the previous home) is safe — ``Metrics()`` is cheap +
    # takes no settings, so doing it at app-construction time vs lifespan
    # time is equivalent for tests (the existing lifespan-time assignment
    # at the original line 297 would have OVERWRITTEN this; we remove the
    # duplicate assignment below).
    state["metrics"] = Metrics()

    # Day 2 Track G — LabelFeedbackService (DDM + ADWIN over the delayed
    # is_returned label stream). Constructed eagerly (cheap — just stores
    # the URLs + creates the DDM/ADWIN instances) so the
    # /v1/feedback/ingest handler can call ``ingest_label`` per request +
    # the /metrics endpoint can read ``current_state()`` for the drift
    # gauges. The lazy StreamProducer inside the service is constructed
    # only on first DRIFT publish — so the 67 existing tests + 7 feedback
    # tests still pass without a Redis fixture.
    # Day 6 Track S (T2.2-helper) — pass ``metrics=state["metrics"]`` so
    # the service can emit ``rto_drift_detection_delay_seconds`` +
    # ``rto_drift_false_alarm_run_length`` summaries on drift transitions
    # (Gama 2014 ACM CSUR 46(4) §5 detector-quality metrics). The kwarg is
    # optional on the constructor (defaults to None → no-op) so test
    # fixtures that build the service directly still work.
    state["feedback"] = LabelFeedbackService(
        redis_url=settings.redis_url,
        database_url=settings.database_url,
        metrics=state["metrics"],
    )

    # Day 4 Track M — OpenTelemetry tracer. Dual-mode like Track E's
    # DATABASE_URL + Track F's REDIS_URL: setup_otel() returns None when
    # OTEL_EXPORTER_OTLP_ENDPOINT is unset (test mode, local dev without
    # Jaeger). The /risk/score handler checks ``state["tracer"] is not
    # None`` before starting a span so the disabled-mode path is a no-op.
    # The 93 existing tests pass without Jaeger.
    state["tracer"] = setup_otel()

    # Idempotency cache. File mode: TTLCache (bounded + auto-expiring —
    # closes §A item 2 memory leak). Postgres mode: the ``idempotency_keys``
    # table; ``state["idem"]`` is unused (kept as an empty dict for backward-
    # compat with any test that asserts the key exists).
    if settings.is_postgres:
        state["idem"] = TTLCache(maxsize=1, ttl=3600)  # placeholder, real cache is the DB
    else:
        state["idem"] = TTLCache(
            maxsize=settings.idem_maxsize, ttl=settings.idem_ttl_seconds
        )

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
        # Day 2 Track E — audit path comes from Settings (defaults to the
        # same ``out/audit.jsonl`` as before, but now .env-configurable +
        # docker-compose-wired). The AuditLogger is dual-mode internally.
        state["audit"] = AuditLogger(audit_path or settings.audit_path)
        # Pass Settings-backed CSV keys to default_keys so the .env file is
        # honored (Track B's Dockerfile change removed the baked ENV defaults;
        # docker-compose now sets them via the environment block).
        state["keys"] = default_keys(
            scorer_keys=settings.rto_scorer_keys,
            admin_keys=settings.rto_admin_keys,
        )
        state["bucket"] = TokenBucket(scorer_rate_per_min)
        state["rules"] = RulesEngine()
        state["breaker"] = CircuitBreaker()
        # NOTE: ``state["metrics"]`` is now constructed at app-construction
        # time (above, before LabelFeedbackService) so it can be wired into
        # the service. The previous ``state["metrics"] = Metrics()`` line
        # here would have overwritten the wired-up instance — removed in
        # Day 6 Track S (T2.2-helper).
        state["cases"] = CaseService(settings.cases_path)
        num_cols = [c for c in X_tr.columns if str(X_tr[c].dtype) != "category"]
        state["psi_sample"] = {
            c: X_tr[c].dropna().sample(n=min(2000, len(X_tr)), random_state=7).tolist()
            for c in num_cols
        }
        # Day 2 Track E — §A item 4 (register_model dead in prod). Register
        # the in-process HistGB with the model registry on every worker boot.
        # File mode: this writes to ``out/model_registry.json`` (the existing
        # behaviour — no-op for tests since they call register_model directly
        # via tmp_path). Postgres mode: INSERT into the ``model_registry``
        # table; the partial-unique index enforces single-champion. PR-AUC
        # + ROC-AUC are surfaced as the metrics payload so the model-card
        # + drift endpoints can read them.
        if settings.is_postgres:
            try:
                from sklearn.metrics import average_precision_score, roc_auc_score
                _p = state["model"].predict_proba(X_tr)[:, 1]
                _pr = float(average_precision_score(y_tr, _p))
                _roc = float(roc_auc_score(y_tr, _p))
                register_model(
                    version=f"v{int(time.time())}",
                    model_path=str(model_path),
                    metrics={"pr_auc": _pr, "roc_auc": _roc},
                    champion=True,
                )
            except Exception as e:  # pragma: no cover — startup-only, defensive
                print(
                    f"model-registry registration skipped: "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
        # Pre-compute labeled (y, p) sequences for the /v1/policy/cost-curves
        # endpoint (Drummond & Holte 2006). The training set carries ground-
        # truth is_returned labels; running the model over it once at startup
        # lets the endpoint respond in O(1) for the per-threshold sweep and
        # ~3-5 sec for the 500-resample bootstrap (per-row performance only).
        # Note: this is in-sample — Day 2 Track E + G will swap in a held-out
        # test slice + delayed-label feedback so the curve reflects deployment
        # conditions, not training fit.
        try:
            _cost_curve_p = state["model"].predict_proba(X_tr)[:, 1].tolist()
            _cost_curve_y = [int(v) for v in y_tr.tolist()]
            # Day 4 Track N — store the dataset median order amount so the
            # /v1/policy/cost-curves endpoint can default the 5-way
            # intervention sweep (Bahnsen Eq.(5): per-amount FN cost) to a
            # representative order value when the caller doesn't pass
            # ``amount_inr``. The raw dataset carries ``OrderValue``; the
            # cleaning step adds ``order_value_inr``; the ``OrderIn`` API
            # surface exposes ``amount_inr`` (all three refer to the same
            # field). Try all three column names, fall back to None if
            # none are present (the endpoint then falls back to 12400 INR —
            # the API_SPEC example value).
            try:
                _amount_col = next(
                    (c for c in ("amount_inr", "order_value_inr", "OrderValue")
                     if c in train_df.columns),
                    None,
                )
                _median_amount = (
                    float(train_df[_amount_col].median()) if _amount_col else None
                )
            except Exception:
                _median_amount = None
            state["cost_curve"] = {
                "y_true": _cost_curve_y,
                "probs": _cost_curve_p,
                "n": len(_cost_curve_y),
                "source": "train_df_in_sample",
                "median_amount_inr": _median_amount,
            }
        except Exception as e:  # pragma: no cover — startup-only, defensive
            print(f"cost-curve warmup skipped: {type(e).__name__}: {e}", file=sys.stderr)
            state["cost_curve"] = None
        yield
        # Day 2 Track E — close the model-registry connection at shutdown so
        # the worker doesn't leak a Postgres connection across hot-reloads.
        if settings.is_postgres:
            _close_registry_conn()
        # Day 2 Track G — close the LabelFeedbackService's lazy StreamProducer
        # so the worker doesn't leak a Redis connection across hot-reloads.
        # Safe to call on a service whose producer was never constructed.
        try:
            state["feedback"].close()
        except Exception:  # pragma: no cover — best-effort shutdown
            pass

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

    @app.post(
        "/risk/score",
        # Day 6 Track P (T1.5) — server-side agent allowlist enforcement.
        # ``enforce_agent_action`` checks the X-Agent-Action header
        # against the 7-action allowlist (Mission 3). Bypasses when the
        # header is absent (existing scorer/admin auth still applies
        # inside the handler).
        dependencies=[Depends(enforce_agent_action)],
    )
    def score(
        order: OrderIn,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
        x_mandate: str | None = Header(default=None),
        # Day 1 Track D (V3 §13): per-txn device_id + user_id for UPI Circle
        # mandates (NPCI OC-201B §3.7 Issuer Bank duty + §3.3 Secondary PSP
        # duty). Both default to None and are ignored for cod_order mandates.
        x_device_id: str | None = Header(default=None),
        x_user_id: str | None = Header(default=None),
        # Day 4 Track M — multi-source ingest channel tag (Microsoft Fabric
        # reference: mobile banking / ATM / e-commerce / call center). The
        # ``channel`` discriminator surfaces in the audit record's ``channel``
        # field + drives per-channel drift detection via TFX
        # generate_data_statistics (Kandula 2021 paper: Payment_Type as a
        # discriminator feature → here ``channel`` is the discriminator).
        # Defaults to ``ecommerce`` for backward-compat with the existing
        # merchant web-checkout path. The 4 ingest simulators in
        # ``src/ingest/`` post with the appropriate channel header.
        x_channel: str | None = Header(default=None),
    ) -> dict:
        token = bearer_token(authorization)
        ok, err = check_key(token, "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        client = token
        if not state["bucket"].allow(client):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

        # Day 2 Track E — idempotency cache (§A item 2: was an unbounded dict;
        # now TTLCache in file mode, Postgres ``idempotency_keys`` table in
        # db mode). File mode preserves the original (key, body) tuple key
        # so two different request bodies with the same Idempotency-Key
        # don't silently cross-pollute. Postgres mode uses the header value
        # alone as the PK (the standard idempotency contract — same key,
        # same response).
        if settings.is_postgres:
            cached = _idem_lookup_postgres(state, idempotency_key)
            if cached is not None:
                # 1% probabilistic cleanup of expired rows.
                if (uuid.uuid4().int % 100) == 0:
                    _idem_cleanup_postgres(state)
                return dict(cached, replayed=True)
        else:
            cache_key = (idempotency_key or "", order.model_dump_json())
            if idempotency_key and cache_key in state["idem"]:
                return dict(state["idem"][cache_key], replayed=True)

        # Day 4 Track M — OpenTelemetry span around the decision pipeline.
        # Dual-mode: when ``state["tracer"]`` is None (test mode, local dev
        # without Jaeger), the ExitStack stays empty + the inline
        # ``if span is not None:`` checks short-circuit, so the OTel SDK
        # is never imported on the disabled path. The span covers the
        # mandate check → rules engine → cost-optimizer → audit → stream
        # publish path so a Jaeger trace surfaces the end-to-end decision
        # pipeline + the Jaeger UI (:16686) shows order_id / amount /
        # decision / score / decision_source on every span. Source: OTel
        # Python SDK §"Manual instrumentation" + the V2 §9.2 observability
        # stack spec (Jaeger all-in-one image 1.55, OTLP gRPC :4317).
        _span_stack = ExitStack()
        span = (
            _span_stack.enter_context(state["tracer"].start_as_current_span("risk.score"))
            if state["tracer"] is not None
            else None
        )
        if span is not None:
            span.set_attribute("order_id", order.order_id)
            span.set_attribute("amount", float(order.amount_inr))

        try:
            t0 = time.monotonic()
            # Mandate verification (cheap; always run; enforcement happens
            # later). Day 1 Track D extended the signature to pass device_id
            # + user_id so UPI Circle mandates can enforce OC-201B §3.3/§3.7
            # per-txn identity validation. cod_order mandates ignore both.
            mandate_verdict, mandate_payload = verify_mandate(
                x_mandate,
                order.amount_inr,
                device_id=x_device_id,
                user_id=x_user_id,
            )
            fired = state["rules"].evaluate(order.model_dump())
            rule_fired = fired.rule_id if fired else None

            proba: float | None = None
            reasons: list[dict] = []
            degraded = False
            breach_note: str | None = None
            cost_breakdown: dict | None = None
            decision: str | None = None
            # Day 4 Track N — 5-way intervention policy fields. Track C's
            # 3-way decision still drives the primary ``decision`` field;
            # ``intervention`` is the cost-optimal V3 §11.6 recommendation
            # (ship / otp_verify / partial_cod / address_check / hold) computed
            # from the per-amount FN cost (Bahnsen Eq.(5)). Surfaced for REVIEW
            # decisions where the operator needs a granular next-step beyond
            # "REVIEW = selective OTP". ``intervention_costs`` carries the full
            # 5-way breakdown for explainability + dashboard rendering.
            intervention: str | None = None
            intervention_costs: dict | None = None
            # ``decision_source`` records which layer actually chose the
            # decision, for the audit trail + dashboard explainability.
            # One of: rules_engine_block | mandate_breach | mandate_invalid
            # | mandate_review_required | degraded_review | cost_optimal_bmr
            # | cost_optimal_bmr_review_rule
            decision_source: str

            # ------------------------------------------------------------------
            # Decision precedence (per Day 1 Track C spec):
            #   1. Rules fast-path (BLOCK)         → short-circuit REJECT
            #   2. Mandate enforcement (BREACH /
            #      TAMPERED / EXPIRED w/ header)   → short-circuit REJECT
            #   2c. Mandate REVIEW (UPI Circle 24h
            #      cooling period, OC-201B)        → short-circuit REVIEW
            #      (Day 1 Track D — mandate action-class expansion)
            #   3. Circuit breaker OPEN            → degraded rules-only REVIEW
            #   4. optimal_decision(p, ...)         → PRIMARY decision path
            #      (replaces legacy static 0.15 / 0.60 thresholds)
            # REVIEW rules don't short-circuit; they gate step 4 to never ACCEPT.
            # ------------------------------------------------------------------
            if fired is not None and fired.action == "BLOCK":
                # 1. Rules-engine BLOCK short-circuits — no model invocation.
                decision = "REJECT"
                decision_source = "rules_engine_block"
            elif mandate_verdict == MandateVerdict.BREACH:
                # 2a. Mandate amount-breach short-circuits.
                decision = "REJECT"
                breach_note = "mandate_amount_breach"
                decision_source = "mandate_breach"
            elif x_mandate is not None and mandate_verdict == MandateVerdict.REVIEW:
                # 2c. UPI Circle 24h cooling-period gate (OC-201B). The txn
                # is permitted in principle; the cooling circuit requires a
                # human to approve before the issuer debits. Routes to the
                # case queue like other REVIEW decisions. verdict_reason
                # (e.g. "cooling_period_active") flows into the audit trail
                # for compliance traceability.
                decision = "REVIEW"
                breach_note = "mandate_review_required"
                decision_source = "mandate_review_required"
            elif x_mandate is not None and mandate_verdict != MandateVerdict.VALID:
                # 2b. Tampered / expired mandate with a header present.
                # Covers OC-201B 6-month inactivity auto-revoke (verdict_reason
                # = "inactivity_auto_revoke") and standard TTL expiry.
                decision = "REJECT"
                breach_note = f"mandate_{mandate_verdict}"
                decision_source = "mandate_invalid"
            else:
                # 3. Circuit breaker guards model invocation.
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
                    # 3. Degraded path: model unavailable → rules-only REVIEW.
                    degraded = True
                    decision = "REVIEW"
                    decision_source = "degraded_review"
                else:
                    # 4. Bahnsen Bayes Minimum Risk decision layer (ICMLA 2013,
                    #    DOI 10.1109/ICMLA.2013.68). Per-order argmin of
                    #    expected cost over {ACCEPT, REVIEW (selective-OTP),
                    #    REJECT}.
                    #
                    #    T2.1 (Track R) — 3-way BMR decision now uses
                    #    per-amount FN cost (Bahnsen Eq.(5): c_fn =
                    #    amount_inr). Same probability produces different
                    #    decisions at different order amounts — the paper's
                    #    headline property. A ₹52,000 order at p=0.4 will
                    #    REJECT; a ₹600 order at p=0.4 will REVIEW. The 5-way
                    #    ``optimal_intervention`` call below ALSO uses
                    #    per-amount FN cost (was already wired in Track N);
                    #    the 3-way decision now matches the 5-way
                    #    intervention's cost model (both per-amount). The
                    #    3-way decision remains the primary authorization
                    #    signal; the 5-way intervention is the operator's
                    #    next-step recommendation.
                    #
                    #    T2.2 (Track R) — Bahnsen Eq.(6) post-resampling
                    #    probability calibration. If the model was trained
                    #    on under-sampled/SMOTE data, the raw probability is
                    #    inflated. Recalibrate:
                    #        P*(f|x) = P(f|x) * P_orig / P_und.
                    #    No-op when priors are equal (no resampling was
                    #    done). ``get_priors()`` returns both-None when the
                    #    live in-process model was registered without
                    #    priors (the pre-Track-R lifespan path) — in that
                    #    case the live path skips calibration, same as
                    #    Track C's behaviour (correct when no SMOTE was
                    #    applied to the training data).
                    _priors = get_priors()
                    if (
                        _priors.get("p_orig") is not None
                        and _priors.get("p_und") is not None
                        and _priors["p_orig"] != _priors["p_und"]
                    ):
                        proba = calibrate_probabilities(
                            [proba], _priors["p_orig"], _priors["p_und"]
                        )[0]
                    decision, costs = optimal_decision(
                        proba, amount_inr=order.amount_inr, **DEFAULT_COST_WEIGHTS
                    )
                    cost_breakdown = costs
                    # Day 4 Track N — V3 §11.6 5-way intervention policy argmin.
                    # Computes the cost-optimal next-step intervention
                    # {ship, otp_verify, partial_cod, address_check, hold} using
                    # the per-amount FN cost (Bahnsen Eq.(5): FN = amount_inr) +
                    # per-intervention effectiveness (Pragma 2025: OTP 0.82,
                    # partial COD 0.65, address check 0.45). Surfaced as a
                    # recommendation for REVIEW decisions; the 3-way ``decision``
                    # field remains the primary authorization signal (the 5-way
                    # ``intervention`` is the operator's next-step hint within
                    # REVIEW / ACCEPT).
                    intervention, intervention_costs = optimal_intervention(
                        proba, order.amount_inr,
                    )
                    # REVIEW rule gate: never ACCEPT when a REVIEW rule fired
                    # (still uses BMR cost math, but the rule forces REVIEW).
                    if fired is not None and fired.action == "REVIEW" and decision == "ACCEPT":
                        decision = "REVIEW"
                        decision_source = "cost_optimal_bmr_review_rule"
                    else:
                        decision_source = "cost_optimal_bmr"

            # Legacy ``policy_hint`` is the BMR-optimal action string (kept for
            # dashboard / consumer backward compat; the actual decision is the
            # body's ``decision`` field — which equals ``policy_hint`` whenever
            # the cost-optimizer was the decision source). T2.1 (Track R):
            # per-amount FN cost is now wired into the 3-way path (was Track-C
            # constant c_fn=600 before). The ``policy_hint`` call mirrors the
            # live decision path's cost model so the dashboard's label matches.
            policy_hint = None
            if proba is not None:
                policy_hint = optimal_decision(
                    proba, amount_inr=order.amount_inr, **DEFAULT_COST_WEIGHTS
                )[0]

            features_used = (
                {c: float(X.iloc[0][c]) for c in X.columns if str(X[c].dtype) != "category"}
                if proba is not None
                else {}
            )
            # Day 2 Track F — generate the prediction_id ONCE so the case
            # row, the audit record's stream publish, and the response body
            # all share the same identifier. Track B's original code passed
            # ``prediction_id="pending"`` to ``open_case`` and generated the
            # real UUID inline in the body dict — that inconsistency meant
            # stream consumers couldn't correlate a ``risk.scores`` message
            # to a ``cases.created`` message (different prediction_id on
            # each). Now the UUID is canonical across the case, the stream,
            # and the response.
            prediction_id = str(uuid.uuid4())
            case_id = None
            if decision == "REVIEW":
                case_id = state["cases"].open_case(
                    prediction_id=prediction_id, order_id=order.order_id,
                    reason=(
                        f"mandate:{mandate_payload.get('verdict_reason')}"
                        if decision_source == "mandate_review_required"
                        else "review_gate" if fired is None
                        else f"rule:{fired.rule_id}"
                    ),
                )
            # Audit payload (Day 1 Track D — V3 §13 mandate action-class
            # expansion). The new fields surface OC-201B compliance metadata
            # in the tamper-evident hash chain: ``mandate_type``,
            # ``bh_purpose_code``, ``device_id``, ``user_id``, and the
            # machine-readable ``mandate_verdict_reason``. The audit logger
            # (Track E Day 2) stores arbitrary JSON payloads; we just pass
            # the new keys through — no logger.py modification needed.
            audit_id = state["audit"].log(
                {
                    "request": {
                        **order.model_dump(),
                        "customer_id": redact_customer(order.customer_id),
                    },
                    "probability": round(proba, 5) if proba is not None else None,
                    "decision": decision,
                    "decision_source": decision_source,
                    "cost_breakdown": cost_breakdown,
                    # Day 4 Track N — V3 §11.6 5-way intervention policy fields
                    # added to the tamper-evident hash chain so an auditor can
                    # verify which intervention the cost-optimizer recommended
                    # alongside the 3-way decision (the operator may execute a
                    # different intervention — the audit captures what the
                    # cost-optimizer suggested vs. what the operator did, which
                    # is the BMR-vs-execution gap Bahnsen 2013 closes).
                    "intervention": intervention,
                    "intervention_costs": intervention_costs,
                    "reason_codes": reasons[:5],
                    "mandate_verdict": mandate_verdict,
                    "mandate_verdict_reason": mandate_payload.get("verdict_reason"),
                    "mandate_type": mandate_payload.get("mandate_type"),
                    "bh_purpose_code": mandate_payload.get("bh_purpose_code"),
                    "device_id": x_device_id,
                    "user_id": x_user_id,
                    "breach_note": breach_note,
                    "rule_fired": rule_fired,
                    "degraded": degraded,
                    "features_used": features_used,
                    "latency_ms": now_ms(t0),
                    # Day 4 Track M — multi-source ingest channel discriminator
                    # (Kandula 2021: Payment_Type as discriminator → here
                    # ``channel`` is the discriminator). Defaults to
                    # ``ecommerce`` so existing merchant web-checkout
                    # requests + the 93 pre-Track-M tests still surface a
                    # ``channel`` value. The 4 simulators in ``src/ingest/``
                    # post with the appropriate ``X-Channel`` header so the
                    # audit record carries the discriminator → per-channel
                    # drift detection via TFX generate_data_statistics
                    # (per Microsoft Fabric fraud-detection reference).
                    "channel": x_channel or "ecommerce",
                    # Day 2 Track G — store the prediction_id in the audit
                    # body so /v1/feedback/ingest can look up the predicted
                    # P(RTO) for a given prediction_id (file mode:
                    # AuditLogger.tail() scan; Postgres mode:
                    # jsonb_path_query on the audit_records.body column).
                    # Additive — doesn't touch the decision logic, just
                    # enriches the audit record with the canonical
                    # correlation key the feedback endpoint needs.
                    "prediction_id": prediction_id,
                    "case_id": case_id,
                    # Day 6 Track U (T2.3) — per-merchant traceability. The
                    # ``merchant_id`` field on ``OrderIn`` is the multi-tenant
                    # key. Stored in the audit body's JSONB so the
                    # ``/v1/usage`` metering endpoint can GROUP BY /
                    # filter by merchant_id. None when the caller didn't
                    # pass one (the 117 pre-Track-U tests + legacy merchant
                    # web-checkout path) — aggregate counts are returned
                    # in that case.
                    "merchant_id": order.merchant_id,
                }
            )
            # Day 2 Track F — fire-and-forget Redis Streams publishes. After
            # the audit hash-chain append + case open, publish to:
            #   * ``risk.scores`` — every decision (the streaming-processor
            #     worker consumes this for TFX-style ``generate_data_statistics``
            #     + anomaly → ``model.drift``)
            #   * ``audit.records`` — every audit record (audit-tail worker
            #     for fan-out to dashboard / compliance export — Track I/H)
            #   * ``cases.created`` — REVIEW decisions only (case-queue
            #     worker for SLA timers + assignment — Track H)
            # All three are fire-and-forget: ``StreamProducer.publish``
            # returns None silently if REDIS_URL is unset (test mode) or
            # Redis is down. The response body is unaffected. The full
            # transactional outbox (V3 §10.3) is deferred — see worklog.
            state["stream"].publish(
                STREAM_RISK_SCORES,
                {
                    "prediction_id": prediction_id,
                    "order_id": order.order_id,
                    "decision": decision or "",
                    "score": "" if proba is None else f"{float(proba):.6f}",
                    "decision_source": decision_source,
                    "model_version": state["audit"].model_version,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            state["stream"].publish(
                STREAM_AUDIT_RECORDS,
                {
                    "audit_id": audit_id,
                    "prediction_id": prediction_id,
                    "decision": decision or "",
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            if decision == "REVIEW" and case_id is not None:
                state["stream"].publish(
                    STREAM_CASES_CREATED,
                    {
                        "case_id": case_id,
                        "prediction_id": prediction_id,
                        "order_id": order.order_id,
                        "reason": (
                            f"mandate:{mandate_payload.get('verdict_reason')}"
                            if decision_source == "mandate_review_required"
                            else "review_gate" if fired is None
                            else f"rule:{fired.rule_id}"
                        ),
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                )
            body = {
                "prediction_id": prediction_id,
                "risk_score": round(proba * 100, 1) if proba is not None else None,
                "probability": round(proba, 4) if proba is not None else None,
                "decision": decision,
                # ``gate_thresholds`` now reports the policy in force. Legacy
                # accept_below / reject_above kept for backward compat — but
                # the live decision path no longer consults them; it consults
                # ``optimal_decision(p, weights=...)``. The dashboard reads
                # ``policy`` to label the decision card.
                "gate_thresholds": {
                    "policy": "cost_optimal_bmr",
                    "weights": DEFAULT_COST_WEIGHTS,
                    "legacy_accept_t": ACCEPT_T,
                    "legacy_reject_t": REJECT_T,
                },
                "decision_source": decision_source,
                "cost_breakdown": cost_breakdown,
                # Day 4 Track N — V3 §11.6 5-way intervention policy fields.
                # The 3-way ``decision`` (ACCEPT/REVIEW/REJECT) drives
                # authorization; the 5-way ``intervention`` (ship/otp_verify/
                # partial_cod/address_check/hold) is the cost-optimal
                # next-step recommendation for the operator — most useful for
                # REVIEW decisions where the operator needs to know whether to
                # send an OTP, call the customer for address verification, or
                # queue for manual review. ``intervention_costs`` is the full
                # 5-way cost breakdown for explainability + dashboard rendering.
                "intervention": intervention,
                "intervention_costs": intervention_costs,
                # ``intervention_weights`` exposes the 5-way cost model so the
                # operator / dashboard can verify the assumptions (OTP
                # effectiveness, partial-COD rate, etc.) — provenance alongside
                # the recommendation (Bahnsen 2013 per-amount FN cost + Pragma
                # 2025 per-intervention effectiveness rates).
                "intervention_weights": DEFAULT_INTERVENTION_WEIGHTS,
                "explanation": reasons[:5],
                "rule_fired": rule_fired,
                "degraded": degraded,
                "policy_hint": policy_hint,
                "model_version": "rules_only"
                if degraded
                else (current_champion() or {"version": state["audit"].model_version})["version"],
                "latency_ms": now_ms(t0),
                "case_id": case_id,
                "mandate": {
                    "verdict": mandate_verdict,
                    "note": breach_note,
                    # Day 1 Track D — V3 §13: surface OC-201B compliance metadata
                    # to the agent / dashboard so the consumer can see WHY
                    # the mandate verdict landed (e.g. "cooling_period_active"
                    # vs "device_id_not_allowed") and WHICH mandate type fired.
                    "verdict_reason": mandate_payload.get("verdict_reason"),
                    "mandate_type": mandate_payload.get("mandate_type"),
                    "bh_purpose_code": mandate_payload.get("bh_purpose_code"),
                },
                "audit_trail_url": f"/audit/{audit_id}",
                # Day 6 Track U (T1.7) — expose ``audit_id`` as a top-level
                # response field so an external verifier can drive the
                # ``GET /v1/audit/{audit_id}/proof`` Merkle inclusion-proof
                # endpoint directly from what the API returns (previously
                # only available as the suffix of ``audit_trail_url``).
                "audit_id": audit_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            state["metrics"].inc(
                "risk_decisions_total", {"decision": decision, "degraded": str(degraded)}
            )
            state["metrics"].observe_latency(now_ms(t0) / 1000)
            # Day 2 Track E — store the response in the idempotency cache.
            # File mode: TTLCache (auto-expires after settings.idem_ttl_seconds;
            # bounded to settings.idem_maxsize). Postgres mode: INSERT into
            # ``idempotency_keys`` with expires_at = NOW() + ttl.
            if idempotency_key:
                if settings.is_postgres:
                    _idem_store_postgres(
                        state,
                        idempotency_key,
                        order.model_dump_json(),
                        body,
                        200,
                    )
                else:
                    cache_key = (idempotency_key, order.model_dump_json())
                    state["idem"][cache_key] = body
            # Day 4 Track M — record the decision attributes on the OTel
            # span before the function returns. The span context exits in
            # the ``finally`` below; Jaeger's UI surfaces these attributes
            # as filterable columns so an operator can query e.g. "all
            # REJECT decisions where decision_source=cost_optimal_bmr"
            # (post-incident review per V3 §13 explainability mandate).
            if span is not None:
                span.set_attribute("decision", decision or "")
                span.set_attribute(
                    "score", float(proba) if proba is not None else 0.0
                )
                span.set_attribute("decision_source", decision_source)
            return body
        except HTTPException:
            raise
        except Exception as e:  # no internal detail leakage
            # Day 4 Track M — record the exception on the OTel span so the
            # Jaeger trace shows the error inline. Then re-raise as 500.
            if span is not None:
                try:
                    span.set_attribute("error", True)
                    span.set_attribute("error.type", type(e).__name__)
                    span.record_exception(e)
                except Exception:  # pragma: no cover — best-effort, never fail the request
                    pass
            incident = uuid.uuid4()
            print(f"incident={incident} scoring_failed={type(e).__name__}: {e}", file=sys.stderr)
            raise HTTPException(status_code=500, detail=f"internal_error incident={incident}")
        finally:
            # Day 4 Track M — exit the OTel span context (closes the
            # ExitStack → calls __exit__ on the span → flushes to the
            # BatchSpanProcessor). Safe to call on an empty ExitStack
            # (the dual-mode None-tracer path). The span end() happens
            # before the response is sent to the client because the
            # ``return body`` inside try evaluates before finally runs
            # (Python finally semantics — finally runs after the return
            # value is computed but before control returns to caller).
            _span_stack.close()

    @app.get("/metrics")
    def prometheus_metrics() -> dict:
        from fastapi import Response

        m: Metrics = state["metrics"]
        state_map = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}
        m.gauge("rto_circuit_state", state_map[state["breaker"].state])
        # Day 2 Track G — drift detector state gauges (0=STABLE,
        # 1=WARNING, 2=DRIFT). Fed by LabelFeedbackService.current_state()
        # which snapshots the in-memory DDM + ADWIN instances. Rendered on
        # Grafana panels 5 + 6 of the RTO dashboard.
        try:
            drift_state = state["feedback"].current_state()
            m.gauge("rto_drift_ddm_state", drift_state["ddm_state_numeric"])
            m.gauge("rto_drift_adwin_state", drift_state["adwin_state_numeric"])
            m.gauge("rto_drift_samples_processed", drift_state["ddm_n"])
            m.gauge("rto_drift_ddm_p", drift_state["ddm_p"])
            m.gauge("rto_drift_adwin_window_len", drift_state["adwin_window_len"])
        except Exception as e:  # pragma: no cover — best-effort metrics
            print(
                f"[metrics] drift state snapshot failed: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
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

    # OpenAPI: this endpoint will appear in docs/openapi.json after next FastAPI
    # app reload (openapi.json is auto-generated from the FastAPI routes — Track
    # H / K will refresh it on Day 2/3 with the full path schema + examples).
    @app.get("/v1/policy/cost-curves")
    def policy_cost_curves(
        n_resamples: int = DEFAULT_COST_CURVE_RESAMPLES,
        confidence: float = DEFAULT_COST_CURVE_CONFIDENCE,
        amount_inr: float | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Drummond-Holte cost-curve explorer (Machine Learning 65:95-130, 2006,
        DOI 10.1007/s10994-006-8199-5) + V3 §11.6 5-way intervention curves
        (Day 4 Track N — Bahnsen Eq.(5) per-amount FN cost).

        Returns a threshold sweep (0.05 → 0.95) over the labeled dataset that
        the in-process model was trained on. For each threshold we report the
        confusion counts, total Bahnsen cost (Eq. 1), precision, recall, plus
        a 90% bootstrap CI on cost (≥500 resamples preserving row marginals —
        per Drummond-Holte ``bootstrap_performance_ci`` capability).

        Day 4 Track N — also returns ``intervention_curves`` (the 5-way
        intervention sweep: ship/otp_verify/partial_cod/address_check/hold
        per-threshold cost breakdown) + ``intervention_crossover`` (the
        threshold(s) where the cost-optimal intervention changes). The
        intervention sweep uses the per-amount FN cost (Bahnsen Eq.(5)):
        ``amount_inr`` param overrides the default representative order value
        so the dashboard can render the cost-optimal intervention for any
        order-amount bracket.

        The dashboard cost-curve explorer consumes this endpoint to render
        real bars (replacing the previously-hardcoded ``COSTS`` array).

        Auth: scorer scope. Returns 503 if the model isn't loaded or no
        labeled data is available — never crashes the dashboard.
        """
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)

        # 1. Guard: model must be loaded (circuit breaker may be OPEN).
        if state.get("model") is None:
            raise HTTPException(
                status_code=503,
                detail="cost curves unavailable — model not loaded",
            )
        # 2. Guard: pre-computed labeled data must exist (startup warmup may
        # have failed silently; surface a clear error to the dashboard).
        cc = state.get("cost_curve")
        if not cc or not cc.get("y_true") or not cc.get("probs"):
            raise HTTPException(
                status_code=503,
                detail="cost curves unavailable — no labeled data available",
            )
        if n_resamples < 1 or n_resamples > 5000:
            raise HTTPException(
                status_code=422,
                detail="n_resamples must be in [1, 5000]",
            )
        if not (0.5 < confidence < 0.999):
            raise HTTPException(
                status_code=422,
                detail="confidence must be in (0.5, 0.999)",
            )
        # Day 4 Track N — validate the optional amount_inr param. None →
        # use the dataset median as the representative order value for the
        # 5-way intervention sweep (Bahnsen Eq.(5): FN cost = amount). The
        # operator can pass any positive amount to render the intervention
        # curves for a specific order-value bracket.
        if amount_inr is not None and (not (1.0 <= float(amount_inr) <= 1_000_000.0)):
            raise HTTPException(
                status_code=422,
                detail="amount_inr must be in [1, 1_000_000]",
            )

        y_true = cc["y_true"]
        probs = cc["probs"]
        weights = DEFAULT_COST_WEIGHTS
        c_fp = float(weights["c_fp"])
        c_fn = float(weights["c_fn"])

        # 3. Threshold sweep → per-threshold confusion + cost + precision +
        #    recall (pure-Python, O(N*T); ~140k ops for 7235 rows × 19 thr).
        curves = cost_curve_sweep(
            y_true=y_true,
            probs=probs,
            c_fp=c_fp,
            c_fn=c_fn,
        )

        # 4. Bootstrap CI per threshold (Drummond-Holte row-marginal-preserving
        #    resampling; ~3-5 sec for 500 resamples × 19 thr × 7235 samples).
        try:
            bootstrap_ci = bootstrap_cost_ci(
                y_true=y_true,
                probs=probs,
                c_fp=c_fp,
                c_fn=c_fn,
                n_resamples=n_resamples,
                confidence=confidence,
                seed=42,
            )
        except Exception as e:  # pragma: no cover — defensive
            # If bootstrap fails for any reason, return degenerate CIs and a
            # warning rather than 500-ing the dashboard.
            bootstrap_ci = {
                str(r["threshold"]): {"low": 0.0, "high": 0.0, "mean": 0.0}
                for r in curves
            }
            print(
                f"cost-curves bootstrap degraded: {type(e).__name__}: {e}",
                file=sys.stderr,
            )

        # 5. Optimal threshold = the one minimizing total cost (this is the
        #    threshold analog of what ``optimal_decision()`` does per order).
        optimal_record = min(curves, key=lambda r: r["cost"])
        optimal_threshold = float(optimal_record["threshold"])

        # 6. Cost crossover: if a challenger model is registered in the model
        #    registry, find the threshold where it beats the incumbent. For
        #    now there is one in-process model so this is a degenerate case —
        #    the incumbent *is* the only model. We still surface the field so
        #    the dashboard wiring is forward-compatible with Day 2 Track E.
        cost_crossover: dict | None = None
        try:
            challenger = None
            champ = current_champion()
            # When more than one model is registered, treat the latest
            # non-champion as the challenger. (For now: single model, None.)
            if champ is not None:
                from src.ml.registry import load_registry
                reg = load_registry()
                non_champ = [m for m in reg.get("models", []) if not m.get("is_champion")]
                challenger = non_champ[-1] if non_champ else None
            if challenger is not None and challenger.get("version") != (champ or {}).get("version"):
                # We don't have challenger probabilities pre-computed yet
                # (single-model registry). Surface a placeholder that the
                # dashboard renders as "single-model — no crossover yet".
                cost_crossover = {
                    "status": "single_model",
                    "incumbent_version": (champ or {}).get("version"),
                    "challenger_version": None,
                    "crossover_threshold": None,
                    "note": (
                        "challenger predictions not pre-computed; "
                        "wire Day 2 Track E model-registry to enable"
                    ),
                }
            else:
                cost_crossover = {
                    "status": "single_model",
                    "incumbent_version": (champ or {}).get("version"),
                    "challenger_version": None,
                    "crossover_threshold": None,
                    "note": "only one model registered; no crossover available",
                }
        except Exception:  # pragma: no cover — defensive
            cost_crossover = {"status": "error", "crossover_threshold": None}

        # 7. Day 4 Track N — V3 §11.6 5-way intervention sweep + crossover.
        #    Per-amount FN cost (Bahnsen Eq.(5)): if ``amount_inr`` is None,
        #    use the dataset median amount as the representative order value.
        #    The dashboard renders the 5-way intervention bands alongside the
        #    3-way threshold sweep so the operator sees both views.
        try:
            # Use the labeled dataset's amount distribution if available; else
            # fall back to a sensible default (₹12400 — the API_SPEC example).
            representative_amount = float(amount_inr) if amount_inr is not None else float(
                cc.get("median_amount_inr") or 12400.0
            )
            intervention_curves = intervention_curve_sweep(representative_amount)
            intervention_crossover = find_intervention_crossover(intervention_curves)
        except Exception as e:  # pragma: no cover — defensive
            intervention_curves = []
            intervention_crossover = {
                "crossover_thresholds": [],
                "per_region_intervention": [],
                "regions": [],
                "error": f"{type(e).__name__}: {e}",
            }

        return {
            "thresholds": [r["threshold"] for r in curves],
            "curves": curves,
            "bootstrap_ci": bootstrap_ci,
            "optimal_threshold": optimal_threshold,
            "cost_crossover": cost_crossover,
            # Day 4 Track N — V3 §11.6 5-way intervention policy fields.
            # ``intervention_curves`` is the 5-way analog of ``curves``:
            # for each probability threshold it reports the cost-optimal
            # intervention (ship/otp_verify/partial_cod/address_check/hold)
            # plus the full cost breakdown across the 5 interventions.
            # ``intervention_crossover`` is the 5-way analog of
            # ``cost_crossover``: the threshold(s) where the cost-optimal
            # intervention changes (e.g. ship → otp_verify).
            "intervention_curves": intervention_curves,
            "intervention_crossover": intervention_crossover,
            "intervention_amount_inr": representative_amount,
            "intervention_weights": DEFAULT_INTERVENTION_WEIGHTS,
            "cost_model": {
                "c_fp": c_fp,
                "c_fn": c_fn,
                "c_otp": float(weights["c_otp"]),
                "c_block": float(weights["c_block"]),
                "otp_effectiveness": float(weights["otp_effectiveness"]),
                "source_paper": "Bahnsen ICMLA 2013, DOI 10.1109/ICMLA.2013.68",
                "curve_paper": "Drummond & Holte 2006, DOI 10.1007/s10994-006-8199-5",
                "intervention_paper": (
                    "Bahnsen 2013 Eq.(5) per-amount FN cost; "
                    "Pragma 2025 RTO-mitigation benchmark for "
                    "per-intervention effectiveness rates"
                ),
            },
            "data_source": cc.get("source", "unknown"),
            "n_samples": int(cc.get("n", 0)),
            "n_pos": int(sum(y_true)),
            "n_neg": int(len(y_true) - sum(y_true)),
        }

    @app.get("/v1/audit/verify-chain")
    def verify_chain(authorization: str | None = Header(default=None)) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        chain_ok, n, bad_id = state["audit"].verify_chain()
        return {"intact": chain_ok, "records_checked": n, "first_bad_audit_id": bad_id}

    @app.post(
        "/v1/mandates",
        # Day 6 Track P (T1.5) — agent allowlist enforcement on the
        # mandate-mint endpoint (money-moving authority — agents can't
        # mint or widen mandates per the existing 403 in the handler;
        # this dependency additionally rejects any caller declaring an
        # out-of-allowlist X-Agent-Action).
        dependencies=[Depends(enforce_agent_action)],
    )
    def create_mandate(
        customer_ref: str,
        max_amount_inr: float,
        ttl_seconds: int = 3600,
        authorization: str | None = Header(default=None),
        # Day 1 Track D — V3 §13 / NPCI OC-201B: optional UPI Circle
        # delegation params. When ``mandate_type=upi_circle_delegation`` the
        # admin mints a bounded UPI Circle mandate carrying the circular's
        # hard caps (₹5,000/txn, ₹15,000/month, ₹5,000 24h cooling) + the
        # device/user identity chain + BH purpose code. Defaults match the
        # OC-201B numeric limits.
        mandate_type: str | None = None,
        device_ids: str | None = None,
        user_id: str | None = None,
        bh_purpose_code: str | None = None,
        max_per_txn_inr: float | None = None,
        max_per_month_inr: float | None = None,
        cooling_24h_inr: float | None = None,
        inactivity_revoke_days: int | None = None,
    ) -> dict:
        """Merchant backend (admin scope) mints bounded agent mandates.

        ``cod_order`` (default): the original HMAC system. ``upi_circle_delegation``
        (Day 1 Track D): OC-201B-compliant UPI Circle delegation mandate with
        per-txn / per-month / 24h-cooling caps + device_id/user_id identity
        chain + BH purpose code tagging.
        """
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        if not (1 <= max_amount_inr <= 1_000_000):
            raise HTTPException(status_code=422, detail="mandate bound out of range")
        if not (30 <= ttl_seconds <= 86_400):
            raise HTTPException(status_code=422, detail="ttl out of range")
        # OC-201B: max 5 devices per delegation. Accept comma-separated list.
        device_id_list: list[str] = []
        if device_ids:
            device_id_list = [d.strip() for d in device_ids.split(",") if d.strip()]
            if len(device_id_list) > 5:
                raise HTTPException(
                    status_code=422,
                    detail="OC-201B: max 5 devices per delegation",
                )
        try:
            mandate = issue_mandate(
                customer_ref,
                max_amount_inr,
                ttl_seconds,
                mandate_type=mandate_type,
                device_ids=device_id_list or None,
                user_id=user_id,
                bh_purpose_code=bh_purpose_code,
                max_per_txn_inr=max_per_txn_inr,
                max_per_month_inr=max_per_month_inr,
                cooling_24h_inr=cooling_24h_inr,
                inactivity_revoke_days=inactivity_revoke_days,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {
            "mandate": mandate,
            "max_amount_inr": max_amount_inr,
            "ttl_seconds": ttl_seconds,
            "mandate_type": mandate_type or "cod_order",
            "device_ids": device_id_list,
            "user_id": user_id,
            "bh_purpose_code": bh_purpose_code,
            "note": "agents cannot mint or widen mandates",
        }

    @app.post(
        "/risk/{prediction_id}/override",
        tags=["override"],
        # Day 6 Track P (T1.5) — agent allowlist enforcement on the
        # dual-control override endpoint (money-moving authority —
        # agents can't self-approve overrides per V3 §12.1; this
        # dependency additionally rejects any caller declaring an
        # out-of-allowlist X-Agent-Action).
        dependencies=[Depends(enforce_agent_action)],
    )
    def override(
        prediction_id: str,
        new_decision: str | None = None,
        payload: OverrideIn | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Dual-control override per V3 §12.1 (Day 2 Track H — closes
        §A item 16 + §C T10).

        Two request shapes supported (auto-detected):

        1. **JSON body (V3-recommended dual-control form)** —
           ``payload`` carries ``admin_signature_1`` + ``admin_signature_2``
           (both must be valid admin-scope API keys + DIFFERENT — no
           self-approval). The override is recorded in the audit hash
           chain with both signature digests so the dual-control trail
           is tamper-evident. Source: SoK Mao 2026 capability
           ``audit_agent_mandate_scoping`` (checks attenuated-task-scoped
           rule vs broad authority — two-admin co-signing is the
           concrete enforcement).

        2. **Query-param ``new_decision`` + ``Authorization: Bearer
           <admin-key>`` (legacy single-admin form)** — Track D's
           ``test_admin_can_override`` + ``test_agent_cannot_self_approve``
           rely on this. Retained for backward-compat / gradual migration;
           the dual-control form is the recommended path for new
           dashboard / ops-console wiring.
        """
        if payload is not None:
            # V3 §12.1 dual-control path.
            # T1.1 — verify admin_signature_1 is a valid admin API key.
            # admin_signature_2 is now an HMAC output (not a raw key) so
            # check_key is no longer called on it; the HMAC chain check
            # below implicitly verifies admin2's key is valid (only a
            # real admin2 key produces the matching HMAC).
            ok1, _ = check_key(
                payload.admin_signature_1, "admin", state["keys"]
            )
            if not ok1:
                # Preserve the existing 403 + "2 valid admin" message so
                # ``test_dual_control_override_requires_two_keys`` (which
                # sends admin_signature_1="invalid-key-1") still passes.
                raise HTTPException(
                    status_code=403,
                    detail="dual-control override requires 2 valid admin API keys",
                )
            if payload.admin_signature_1 == payload.admin_signature_2:
                # Same-key self-approve attempt → 400 (preserves the
                # ``test_dual_control_same_key_rejected`` assertion).
                # NOTE: with the new HMAC-chain design, sig1 (a raw key)
                # and sig2 (an HMAC hex output) can NEVER be equal by
                # accident — the same-key check fires only when the
                # client mistakenly reuses admin1's key as the "second
                # signature" (the legacy pre-T1.1 form), which is
                # exactly the self-approve attempt V3 §12.1 forbids.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "dual-control requires 2 DIFFERENT admin keys "
                        "— cannot self-approve (V3 §12.1)"
                    ),
                )
            decision = payload.decision
            if decision not in {
                "ACCEPT", "REVIEW", "REJECT",
                "APPROVED", "REJECTED", "ESCALATED",
            }:
                raise HTTPException(status_code=422, detail="invalid decision")

            # T1.1 — REAL HMAC CHAIN. signature_2 = HMAC(admin2_key,
            # signature_1 || canonical_body || timestamp). A
            # single-admin compromise cannot forge a dual-control
            # override because the second signature is
            # cryptographically bound to the first — admin1's key
            # alone is useless (no admin2 key to compute the expected
            # HMAC); admin2's key alone is useless (no admin1 signature
            # to chain on). Both must collude OR both must be
            # compromised to forge an override.
            canonical_body = json.dumps(
                {
                    "prediction_id": prediction_id,
                    "decision": decision,
                    "notes": payload.notes,
                },
                sort_keys=True,
            )
            # Client-provided timestamp OR server's current time. We
            # also try ±30 seconds for clock skew when the client didn't
            # send a timestamp (the agent client may compute signature_2
            # a few seconds before the server processes the request).
            base_ts = (
                payload.timestamp
                if payload.timestamp is not None
                else int(time.time())
            )
            ts_candidates = (
                [base_ts]
                if payload.timestamp is not None
                else [base_ts + delta for delta in range(-30, 31)]
            )
            admin2_key_found: str | None = None
            expected_sig_2: str | None = None
            matched_ts: int | None = None
            for ts_candidate in ts_candidates:
                chained_msg = (
                    f"{payload.admin_signature_1}|{canonical_body}|"
                    f"{ts_candidate}"
                )
                # Iterate through admin keys (skipping the one equal to
                # admin_signature_1). The one that produces an HMAC
                # matching admin_signature_2 IS admin2's key.
                for candidate_key in state["keys"]["admin"]:
                    if candidate_key == payload.admin_signature_1:
                        continue  # admin2 must be different from admin1
                    candidate_sig = hmac.new(
                        candidate_key.encode(),
                        chained_msg.encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    if hmac.compare_digest(
                        payload.admin_signature_2, candidate_sig
                    ):
                        admin2_key_found = candidate_key
                        expected_sig_2 = candidate_sig
                        matched_ts = ts_candidate
                        break
                if admin2_key_found is not None:
                    break
            if admin2_key_found is None or expected_sig_2 is None:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "dual_control HMAC chain verification failed — "
                        "signature_2 must be HMAC(key=admin2, "
                        "msg=signature_1 + '|' + canonical_body + '|' + "
                        "timestamp). canonical_body="
                        + canonical_body
                        + f", base_timestamp={base_ts}"
                    ),
                )

            # Record both admin-key digests (NOT the raw keys — same
            # redaction posture as customer_id) in the audit hash chain
            # so a verifier can prove "two different admins co-signed
            # this override" without retaining the raw secrets.
            # admin_signature_1_digest = sha256-truncate-16 of sig1
            # (the raw admin1 key) — same shape as redact_customer().
            # admin_signature_2_hmac_chain = the truncated HMAC output
            # (sig2 IS the HMAC; we store a 16-char display prefix so
            # the audit trail can show "the chain was verified" without
            # retaining the full HMAC). ``dual_control_chain_verified``
            # is the machine-readable flag for the verifier. The Merkle
            # interval sealer (Track H) folds this record into the next
            # sealed root too.
            admin_sig_1_digest = (
                "adm_"
                + hashlib.sha256(
                    payload.admin_signature_1.encode()
                ).hexdigest()[:16]
            )
            admin_sig_2_hmac_chain = "hmac_" + expected_sig_2[:16]
            audit_id = state["audit"].log(
                {
                    "request": {
                        "prediction_id": prediction_id,
                        "override_form": "dual_control_v3_12_1",
                    },
                    "decision": decision,
                    "breach_note": "dual_control_override_by_two_admins",
                    "admin_signature_1_digest": admin_sig_1_digest,
                    # T1.1 — admin_signature_2 is now an HMAC output,
                    # not a raw key. Store the truncated HMAC (display)
                    # + the verified flag instead of a SHA-256 digest of
                    # the raw key (which no longer exists in the payload).
                    "admin_signature_2_hmac_chain": admin_sig_2_hmac_chain,
                    "dual_control_chain_verified": True,
                    "dual_control_timestamp": matched_ts,
                    "notes": payload.notes,
                }
            )
            return {
                "overridden": prediction_id,
                "new_decision": decision,
                "audit_id": audit_id,
                "dual_control": True,
                "signatures_required": 2,
                "signatures_provided": 2,
                # T1.1 — surface the chain-verified flag so the
                # dashboard / ops console can label the override as
                # "HMAC-chained dual-control" (vs the legacy
                # single-admin path which surfaces dual_control=False).
                "dual_control_chain_verified": True,
                "dual_control_timestamp": matched_ts,
            }
        # Legacy single-admin path (Track D backward-compat).
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(
                status_code=403, detail="decision override requires admin scope"
            )
        if new_decision is None or new_decision not in {"ACCEPT", "REVIEW", "REJECT"}:
            raise HTTPException(status_code=422, detail="invalid decision")
        audit_id = state["audit"].log(
            {
                "request": {
                    "prediction_id": prediction_id,
                    "override_form": "legacy_single_admin",
                },
                "decision": new_decision,
                "breach_note": "manual_override_by_admin",
            }
        )
        return {
            "overridden": prediction_id,
            "new_decision": new_decision,
            "audit_id": audit_id,
            "dual_control": False,
            "signatures_required": 1,
            "signatures_provided": 1,
        }

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

    # Day 2 Track G — closes §A item 18 (feedback loop) + §D P3 (formal
    # drift detection) + §D P4 (shadow-retrain trigger) + perceived-gap
    # driver G3 (partial — PSI reference population deferred). Source:
    # Gama et al., "A Survey on Concept Drift Adaptation",
    # ACM Computing Surveys (CSUR) 46(4), Article 44, March 2014,
    # DOI 10.1145/2523813. The endpoint ingests delayed is_returned labels
    # (chargeback-style delay, days-weeks after the prediction), runs DDM
    # + ADWIN on the resulting error stream, and on DRIFT fires a
    # retrain_request notification to the ``notifications`` Redis Stream
    # (consumer-side run-length heuristic via the drift-consumer worker).
    @app.post("/v1/feedback/ingest", tags=["feedback"])
    def ingest_feedback(
        payload: FeedbackIn,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Ingest a delayed ``is_returned`` ground-truth label.

        Auth: admin scope (merchants can't self-report labels — prevents
        label poisoning). Looks up the prediction's recorded ``P(RTO)``
        from the audit log, computes the error indicator (1 if the
        prediction was wrong), updates the in-memory DDM + ADWIN
        detectors, and on DRIFT fires a ``retrain_request`` notification.

        Returns the current detector state + the per-prediction error so
        the dashboard / ops console can surface the live drift status.
        """
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(
                status_code=403,
                detail="feedback ingestion requires admin scope (label poisoning prevention)",
            )
        # Look up the prediction's recorded P(RTO) by scanning recent audit
        # records for the prediction_id we just enriched the audit body with
        # (file mode: tail-scan; Postgres mode: the same tail() goes to
        # ``SELECT body FROM audit_records ORDER BY id DESC LIMIT %s``).
        # Scanning the last 5000 audit records is plenty for the demo —
        # labels arrive days-weeks after the prediction, so the prediction
        # is comfortably within the last 5000 by then. At scale, a
        # jsonb_path_query on audit_records.body->prediction_id would
        # be the right index (deferred — Track H V3 §10.3 work).
        predicted_p: float | None = None
        for rec in _read_audit_tail(state["audit"], limit=5000):
            if rec.get("prediction_id") == payload.prediction_id:
                # The audit body's ``probability`` field is the model's
                # P(RTO) rounded to 5 decimals (see /risk/score).
                p = rec.get("probability")
                if p is not None:
                    try:
                        predicted_p = float(p)
                    except (TypeError, ValueError):
                        predicted_p = None
                break
        # If the prediction can't be found, ingest_label still runs —
        # the error indicator defaults to 0 (no contribution to drift)
        # and the response carries ``prediction_not_found: True`` so the
        # caller can see the lookup missed (e.g. the prediction_id was
        # wrong, or the audit record aged out of the tail window).
        result = state["feedback"].ingest_label(
            prediction_id=payload.prediction_id,
            is_returned=payload.is_returned,
            predicted_p=predicted_p,
        )
        return {"status": "ingested", **result}

    # ================================================================== #
    # Day 2 Track H — V3 §10.3 + §A items 15, 16 + §C T10 + §D P11.    #
    # Source: SoK (Mao 2026) capability ``recommend_layered_defenses``  #
    # layer 5 (market & compliance monitoring with tamper-evident       #
    # audit trails) + ``audit_agent_mandate_scoping`` (the attenuated-  #
    # task-scoped rule vs broad authority check that the dual-control   #
    # override above enforces).                                         #
    # ================================================================== #

    @app.get("/v1/audit/{audit_id}/proof", tags=["audit"])
    def audit_proof(
        audit_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Merkle inclusion proof for an audit record — path from leaf
        to interval root (V3 §10.3).

        Day 6 Track U (T1.7) — accepts the string ``audit_id`` (e.g.
        ``"aud_3f9b8e2c1d4a5b06"``) that ``POST /risk/score`` returns in
        its response body's ``audit_id`` field (or as the suffix of
        ``audit_trail_url``). Looks up the internal SERIAL PK
        (``audit_records.id``) that the Merkle sealer indexes by, then
        delegates to ``AuditLogger.merkle_proof(record_id)``. Previously
        the route took ``record_id: int`` (the internal PK) which an
        external verifier could not drive from what the API returns.

        Auth: admin scope. Returns the Merkle proof path so an external
        verifier can confirm a specific audit record is included in a
        sealed interval without recomputing the entire interval tree
        (O(log N) verification vs O(N) full-chain recompute via
        ``GET /v1/audit/verify-chain``). The response also carries the
        interval's ``prev_interval_root`` + ``sealed_at`` so cross-
        interval chain verification is possible client-side.

        404 if the record doesn't exist OR its interval hasn't been
        sealed yet (run ``seal_interval()`` first via the admin shell
        or wait for the sealer's count/elapsed threshold to trip).
        File mode (no DATABASE_URL) returns 404 — the per-record hash
        chain is the only tamper-evidence layer there, accessible via
        ``GET /v1/audit/verify-chain``.
        """
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        # T1.7 — translate the public audit_id (string) to the internal
        # record_id (int SERIAL PK) the Merkle sealer indexes by.
        record_id = _lookup_record_id_by_audit_id(state["audit"], audit_id)
        if record_id is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"audit record '{audit_id}' not found or no Merkle "
                    "interval sealed for this record (run seal_interval() "
                    "first, or wait for the count/elapsed threshold to "
                    "trip). file-mode audit has no Merkle layer — use "
                    "GET /v1/audit/verify-chain for the per-record hash "
                    "chain."
                ),
            )
        proof = state["audit"].merkle_proof(record_id)
        if proof is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"audit record '{audit_id}' exists but no Merkle "
                    "interval has been sealed yet (run seal_interval() "
                    "first, or wait for the count/elapsed threshold to "
                    "trip). file-mode audit has no Merkle layer — use "
                    "GET /v1/audit/verify-chain for the per-record hash "
                    "chain."
                ),
            )
        return proof

    @app.post("/v1/simulate", tags=["simulation"])
    def simulate(
        payload: SimulateIn,
        authorization: str | None = Header(default=None),
    ) -> dict:
        """Dry-run policy simulation — replay a transaction through the
        pipeline without writing to the audit hash chain, opening a
        case, or publishing to Redis Streams (Day 2 Track H — closes
        §A item 15 + §C T10).

        Auth: scorer scope. Useful for merchant "what-if" tuning: tweak
        the order amount / category / mandate + observe the decision +
        cost_breakdown + rule_trace BEFORE flipping a live policy. The
        ``dry_run=True`` flag is forced server-side so a misconfigured
        client can't accidentally turn this into a live scoring call.

        Returns the same shape as ``POST /risk/score`` minus the
        audit_trail_url / case_id (both None — nothing was persisted).
        Adds a ``rule_trace`` list showing which rules were evaluated
        + whether each fired (the /risk/score endpoint doesn't surface
        this because the rule_engine returns a single fired rule;
        simulate shows the full evaluation for debugging).
        """
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        if not payload.dry_run:
            # The model accepts dry_run but the simulate endpoint forces
            # True server-side — silently coerce rather than 422 (the
            # client's intent was a dry run; we honor it).
            payload.dry_run = True
        # Re-run the decision pipeline WITHOUT the persistence side-effects.
        # The pipeline code is duplicated from /risk/score deliberately —
        # extracting a shared helper would touch Track C's decision
        # section + Track D's mandate section, both of which Track H is
        # NOT supposed to modify. The duplication is shallow + the
        # simulate endpoint is a read-only explorer.
        order = payload.order
        try:
            t0 = time.monotonic()
            mandate_verdict, mandate_payload = verify_mandate(
                payload.mandate,
                order.amount_inr,
                # UPI Circle mandates would carry device_id / user_id
                # in the simulate flow too; we pass None here for
                # simplicity — the dashboard's "what-if" use case
                # rarely simulates a specific device chain.
                device_id=None,
                user_id=None,
            )
            fired = state["rules"].evaluate(order.model_dump())
            rule_fired = fired.rule_id if fired else None
            # Rule trace — which rules were evaluated + which fired.
            # ``RulesEngine.list_active()`` returns dicts (rule_id, name,
            # field, op, value, action, priority), NOT Rule dataclass
            # instances — so dict-key access here.
            rule_trace = [
                {"rule_id": r["rule_id"], "action": r["action"], "fired": False}
                for r in state["rules"].list_active()
            ]
            if fired is not None:
                for entry in rule_trace:
                    if entry["rule_id"] == fired.rule_id:
                        entry["fired"] = True

            proba: float | None = None
            reasons: list[dict] = []
            degraded = False
            breach_note: str | None = None
            cost_breakdown: dict | None = None
            decision: str | None = None
            # Day 4 Track N — 5-way intervention fields mirroring /risk/score.
            intervention: str | None = None
            intervention_costs: dict | None = None
            decision_source: str

            # Mirror the /risk/score decision precedence (Track C/D).
            if fired is not None and fired.action == "BLOCK":
                decision = "REJECT"
                decision_source = "rules_engine_block"
            elif mandate_verdict == MandateVerdict.BREACH:
                decision = "REJECT"
                breach_note = "mandate_amount_breach"
                decision_source = "mandate_breach"
            elif payload.mandate is not None and mandate_verdict == MandateVerdict.REVIEW:
                decision = "REVIEW"
                breach_note = "mandate_review_required"
                decision_source = "mandate_review_required"
            elif payload.mandate is not None and mandate_verdict != MandateVerdict.VALID:
                decision = "REJECT"
                breach_note = f"mandate_{mandate_verdict}"
                decision_source = "mandate_invalid"
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
                    decision = "REVIEW"
                    decision_source = "degraded_review"
                else:
                    # Day 4 Track N — T2.1 + T2.2 (Track R) mirror of
                    # the /risk/score decision path. The 3-way BMR
                    # decision now uses per-amount FN cost (Bahnsen
                    # Eq.(5): c_fn = amount_inr) — same probability
                    # produces different decisions at different order
                    # amounts, the paper's headline property. The
                    # ``optimal_intervention`` 5-way call below ALSO
                    # uses per-amount FN cost (was already wired in
                    # Track N). Track R also recalibrates the
                    # probability via Bahnsen Eq.(6) when the model
                    # registry carries post-resampling priors (no-op
                    # when priors are equal or both-None).
                    _priors = get_priors()
                    if (
                        _priors.get("p_orig") is not None
                        and _priors.get("p_und") is not None
                        and _priors["p_orig"] != _priors["p_und"]
                    ):
                        proba = calibrate_probabilities(
                            [proba], _priors["p_orig"], _priors["p_und"]
                        )[0]
                    decision, costs = optimal_decision(
                        proba, amount_inr=order.amount_inr, **DEFAULT_COST_WEIGHTS
                    )
                    cost_breakdown = costs
                    # 5-way V3 §11.6 intervention recommendation (per-amount
                    # FN cost — Bahnsen Eq.(5)).
                    intervention, intervention_costs = optimal_intervention(
                        proba, order.amount_inr,
                    )
                    if (
                        fired is not None
                        and fired.action == "REVIEW"
                        and decision == "ACCEPT"
                    ):
                        decision = "REVIEW"
                        decision_source = "cost_optimal_bmr_review_rule"
                    else:
                        decision_source = "cost_optimal_bmr"

            # Legacy ``policy_hint`` mirrors the live decision path's
            # per-amount cost model (T2.1) so the dashboard's simulate
            # "what-if" label matches the live decision label.
            policy_hint = None
            if proba is not None:
                policy_hint = optimal_decision(
                    proba, amount_inr=order.amount_inr, **DEFAULT_COST_WEIGHTS
                )[0]

            features_used = (
                {c: float(X.iloc[0][c]) for c in X.columns if str(X[c].dtype) != "category"}
                if proba is not None
                else {}
            )
            # NO audit.log(), NO cases.open_case(), NO stream.publish()
            # — that's the entire point of /v1/simulate.
            body = {
                "dry_run": True,
                "order_id": order.order_id,
                "probability": round(proba, 4) if proba is not None else None,
                "risk_score": round(proba * 100, 1) if proba is not None else None,
                "decision": decision,
                "decision_source": decision_source,
                "cost_breakdown": cost_breakdown,
                # Day 4 Track N — 5-way intervention fields, mirroring
                # /risk/score so the simulate "what-if" explorer surfaces the
                # same operator next-step recommendation. No audit/log/case
                # side-effects (dry_run stays true).
                "intervention": intervention,
                "intervention_costs": intervention_costs,
                "intervention_weights": DEFAULT_INTERVENTION_WEIGHTS,
                "explanation": reasons[:5],
                "rule_fired": rule_fired,
                "rule_trace": rule_trace,
                "degraded": degraded,
                "policy_hint": policy_hint,
                "features_used": features_used,
                "mandate": {
                    "verdict": mandate_verdict,
                    "note": breach_note,
                    "verdict_reason": mandate_payload.get("verdict_reason"),
                    "mandate_type": mandate_payload.get("mandate_type"),
                    "bh_purpose_code": mandate_payload.get("bh_purpose_code"),
                },
                "model_version": (
                    current_champion() or {"version": state["audit"].model_version}
                )["version"],
                "latency_ms": now_ms(t0),
                # audit_trail_url + case_id are None — nothing was persisted.
                "audit_trail_url": None,
                "case_id": None,
                "prediction_id": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return body
        except HTTPException:
            raise
        except Exception as e:
            incident = uuid.uuid4()
            print(
                f"incident={incident} simulate_failed={type(e).__name__}: {e}",
                file=sys.stderr,
            )
            raise HTTPException(
                status_code=500,
                detail=f"internal_error incident={incident}",
            )

    @app.get("/v1/usage", tags=["metering"])
    def usage(
        authorization: str | None = Header(default=None),
        merchant_id: str | None = Query(default=None, max_length=64),
        since_hours: str = "24,168,720",
    ) -> dict:
        """Per-merchant request counts (Day 2 Track H — closes §A item 15 +
        §C T10; metering endpoint for billing / quota enforcement).

        Auth: admin scope. Returns audit-record counts for each window
        in ``since_hours`` (default ``24,168,720`` = 24h / 7d / 30d).
        Per-window = count of audit_records with ``created_at > now() -
        interval '<H> hours'`` in Postgres mode, or a JSONL timestamp
        scan in file mode.

        Day 6 Track U (T2.3) — per-merchant filtering is now implemented.
        When ``?merchant_id=<mid>`` is provided, counts are scoped to
        audit records whose ``body->>'merchant_id'`` matches
        (Postgres: ``WHERE body->>'merchant_id' = %s``; file mode: scan +
        filter by the JSON field). When absent, counts are aggregate
        (all merchants combined) — same as before. The
        ``OrderIn.merchant_id`` field on /risk/score is the producer-side
        wire (T2.3 part 1); this query param is the consumer-side wire
        (T2.3 part 2). Together they close the multi-tenant metering
        gap (V3 §10.4 multi-tenant isolation).

        The response also surfaces the Merkle interval sealing cadence
        (last 100 intervals) so a billing auditor can verify the audit
        trail's tamper-evidence layer is up-to-date alongside the
        metering counts.
        """
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        # Parse + clamp the since_hours CSV. Each value must be a positive
        # int (hours); we cap at 87600 (10 years) so a stray huge value
        # doesn't make the Postgres interval scan pathological.
        try:
            hours = tuple(
                max(1, min(87600, int(h.strip())))
                for h in since_hours.split(",")
                if h.strip()
            )
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="since_hours must be a CSV of positive integers (e.g. '24,168,720')",
            )
        if not hours:
            hours = (24, 168, 720)
        # T2.3 — per-merchant filter when merchant_id is provided;
        # aggregate otherwise (the existing Track H behaviour).
        if merchant_id:
            counts = _usage_counts_per_merchant(
                state["audit"], hours, merchant_id
            )
            scope = f"merchant_id={merchant_id}"
            note = (
                f"per-merchant counts scoped to merchant_id='{merchant_id}' "
                "(audit_records.body->>'merchant_id' = merchant_id filter)"
            )
        else:
            counts = state["audit"].usage_counts(since_hours=hours)
            scope = "aggregate"
            note = (
                "aggregate counts across all merchants (pass ?merchant_id=<mid> "
                "to scope to a single merchant — per-merchant filter is "
                "implemented Day 6 Track U T2.3)"
            )
        # Surface the Merkle interval chain alongside the counts (Track H
        # — V3 §10.3). Cheap query (≤100 rows, PK DESC scan). The
        # response shape is intentionally flat so a billing dashboard
        # can render the metering numbers + the sealing cadence in one
        # panel.
        intervals = state["audit"].merkle_intervals(limit=100)
        # Compute the sealing cadence: intervals sealed in the last
        # ``max(hours)`` window. Cheap — already fetched above.
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - max(hours) * 3600
            recent_intervals = [
                iv for iv in intervals
                if iv.get("sealed_at")
                and datetime.fromisoformat(iv["sealed_at"]).timestamp() >= cutoff
            ]
        except (ValueError, TypeError):
            recent_intervals = []
        return {
            "counts": counts,  # {"24": N, "168": N, "720": N}
            "since_hours": list(hours),
            "scope": scope,  # "aggregate" | "merchant_id=<mid>"
            "merchant_id": merchant_id,  # None when aggregate
            "intervals_sealed_total": len(intervals),
            "intervals_sealed_in_window": len(recent_intervals),
            "latest_interval": intervals[0] if intervals else None,
            "note": note,
        }

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


def enforce_agent_action(
    x_agent_action: str | None = Header(default=None, alias="X-Agent-Action"),
    x_mandate: str | None = Header(default=None, alias="X-Mandate"),
) -> dict:
    """Server-side enforcement of the 7-action agent allowlist (Mission 3).

    Day 6 Track P (T1.5) — production-grade agent-gateway enforcement.
    Per user's 5 Missions: "Agent can only call N APIs. Any other intent
    returns 'Action not permitted.'" Per Mao 2026 SoK D2
    (transaction-authorization): "design mandates as scoped, task-bound,
    attenuating credentials rather than standing broad authority" — the
    allowlist is the operational expression of that attenuation.

    Callers who declare an agent intent via the ``X-Agent-Action`` header
    are gated against the canonical 7-action allowlist
    (``src.api.agent_allowlist.ALLOWED_ACTIONS`` — score_order,
    request_otp, flag_review, block_order, upi_circle_delegated_pay,
    validate_device_id, revoke_delegation_on_inactivity). Callers who
    DON'T declare an agent intent bypass this dependency — the existing
    scorer/admin ``check_key`` auth in the handler is sufficient
    (admin/scoper scopes are not bound by the agent allowlist per the
    task spec; only agent-scope callers — those presenting X-Agent-Action
    — are bound).

    Decision tree:
      * ``X-Agent-Action`` absent → return ``{"action": None,
        "permitted": True}`` (caller is admin/scorer; bypass).
      * ``X-Agent-Action`` present + action in allowlist + NOT
        ``requires_approval`` → return ``{"action": <action>,
        "permitted": True}`` (the handler executes normally).
      * ``X-Agent-Action`` present + action in allowlist +
        ``requires_approval=True`` (e.g. ``block_order``,
        ``upi_circle_delegated_pay``) → raise HTTPException(202,
        "agent action requires human approval — case queued") so the
        handler NEVER executes (Mission 3 demo moment #5: high-cost
        actions don't execute; a case is queued for human approval).
        The ``X-Case-Created: true`` response header signals to the
        client/dashboard that the case must be queued by the upstream
        agent orchestrator (the dependency itself can't open a case
        without state access; the 202 short-circuit is the
        contract-preserving signal).
      * ``X-Agent-Action`` present + action NOT in allowlist → raise
        HTTPException(403, "agent action not permitted: action
        '<action>' not in allowlist") (Mission 3 — "Action not
        permitted.").

    The ``x_mandate`` parameter is accepted for forward-compat with
    per-mandate-scope allowlist policies (future work may allowlist
    different action sets per mandate scope — e.g. ``upi_circle`` vs
    ``cod_order``). The current allowlist is uniform across scopes;
    ``check_agent_action`` accepts the kwarg but ignores it for now.

    Applied to the money-moving endpoints via ``Depends(...)``:
    ``POST /risk/score``, ``POST /risk/{prediction_id}/override``,
    ``POST /v1/mandates``. Non-money-moving endpoints (feedback ingest,
    simulate dry-run, case resolution) are NOT bound — the existing
    admin/scorer auth is sufficient.
    """
    if x_agent_action is None:
        # Caller is not declaring an agent intent — bypass. The handler's
        # own check_key("scorer"|"admin", ...) auth still applies.
        return {
            "action": None,
            "permitted": True,
            "requires_approval": False,
        }
    allowed, reason = check_agent_action(x_agent_action, mandate_scope=x_mandate)
    if not allowed:
        if reason == "requires human approval":
            # Mission 3 demo moment #5 — high-cost actions don't execute;
            # a case is queued for human approval. The 202 + X-Case-Created
            # header signals the case must be queued by the upstream agent
            # orchestrator (the dependency itself can't open a case
            # without state access; the 202 short-circuit is the
            # contract-preserving signal).
            raise HTTPException(
                status_code=202,
                detail=(
                    f"agent action '{x_agent_action}' requires human "
                    "approval — case queued for review (Mission 3 demo "
                    "moment #5: high-cost actions don't execute)"
                ),
                headers={"X-Case-Created": "true"},
            )
        # Mission 3: "Any other intent returns 'Action not permitted.'"
        raise HTTPException(
            status_code=403,
            detail=(
                f"agent action '{x_agent_action}' not permitted: {reason} "
                "(Mission 3: agent allowlist enforcement — "
                "src.api.agent_allowlist.ALLOWED_ACTIONS)"
            ),
        )
    return {
        "action": x_agent_action,
        "permitted": True,
        "requires_approval": False,
    }


def _read_audit_tail(audit_logger, limit: int = 300) -> list[dict]:
    """Return the last ``limit`` audit records (chronological order).

    Day 2 Track E — now uses ``AuditLogger.tail(limit)`` so both file mode
    (JSONL tail) and Postgres mode (``SELECT body FROM audit_records ORDER
    BY id DESC LIMIT %s``) work. The previous direct-read of
    ``audit_logger.path`` only worked in file mode.
    """
    return audit_logger.tail(limit)


def _lookup_record_id_by_audit_id(audit_logger, audit_id: str) -> int | None:
    """Look up the internal SERIAL PK (``audit_records.id``) by the public
    ``audit_id`` string (e.g. ``"aud_3f9b8e2c1d4a5b06"``).

    Day 6 Track U (T1.7) — the ``GET /v1/audit/{audit_id}/proof`` route
    accepts the string ``audit_id`` that ``POST /risk/score`` returns +
    needs to translate it to the internal integer PK the Merkle sealer
    indexes by (``audit_merkle_interval_leaves.record_id`` references
    ``audit_records.id``, NOT ``audit_records.audit_id``).

    Postgres mode: ``SELECT id FROM audit_records WHERE audit_id = %s``.
    File mode: returns None (no DB connection → no Merkle layer active →
    the caller 404s with the existing "file mode has no Merkle layer"
    message; the route handles None + the missing-record case uniformly).

    The helper accesses ``audit_logger._conn`` (the AuditLogger's private
    connection attribute) directly — Subagent 11-routes owns routes.py
    only, and adding a public method on AuditLogger would mean editing
    ``src/audit/logger.py`` (owned by Subagent 11-b). The private-attr
    read is the contract-preserving escape hatch; it stays in routes.py
    as a local helper so 11-b's file (and its Merkle-proof builder) is
    untouched.

    Returns the integer ``record_id`` on hit; ``None`` on miss OR when
    the audit logger is in file mode.
    """
    conn = getattr(audit_logger, "_conn", None)
    if conn is None:
        return None  # file mode — no Merkle layer; caller 404s uniformly
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM audit_records WHERE audit_id = %s",
                (audit_id,),
            )
            row = cur.fetchone()
    except Exception:
        # Defensive — a transient DB error shouldn't 500 the proof
        # endpoint; treat it as a miss so the caller gets the same 404
        # (a real DB outage surfaces elsewhere via the audit write path).
        return None
    return int(row[0]) if row else None


def _usage_counts_per_merchant(
    audit_logger, since_hours: tuple[int, ...], merchant_id: str
) -> dict[str, int]:
    """Per-merchant audit-record counts for the ``/v1/usage`` metering endpoint.

    Day 6 Track U (T2.3) — closes the multi-tenant metering gap. Filters
    audit records by ``body->>'merchant_id' = merchant_id`` (Postgres) or
    by the JSONL record's ``merchant_id`` field (file mode). Returns the
    same ``{str(h): count, ...}`` shape as ``AuditLogger.usage_counts()``.

    Postgres mode: ``SELECT count(*) FROM audit_records WHERE
    body->>'merchant_id' = %s AND created_at > now() - interval '<H>
    hours'``. The ``body`` column is JSONB (migration 001) so the
    ``->>`` operator is index-able (a GIN expression index on
    ``(body->>'merchant_id')`` is the production-scale path; a sequential
    scan is fine for the demo).

    File mode: scan the JSONL, filter records whose top-level
    ``merchant_id`` field matches + whose timestamp is within the window.
    Same pattern as ``AuditLogger._usage_counts_file`` (which Subagent
    11-b owns — we don't touch logger.py).

    The helper is local to routes.py because adding a
    ``usage_counts_per_merchant`` method on AuditLogger would require
    editing ``src/audit/logger.py`` (owned by Subagent 11-b). The
    private-attr read of ``audit_logger._conn`` mirrors the
    ``_lookup_record_id_by_audit_id`` helper above — the same
    contract-preserving escape hatch.
    """
    conn = getattr(audit_logger, "_conn", None)
    if conn is not None:
        out: dict[str, int] = {}
        try:
            with conn.cursor() as cur:
                for h in since_hours:
                    cur.execute(
                        "SELECT count(*) FROM audit_records "
                        "WHERE body->>'merchant_id' = %s "
                        "AND created_at > now() - interval '%s hours'",
                        (merchant_id, h),
                    )
                    out[str(h)] = int(cur.fetchone()[0])
        except Exception:
            # Defensive — a transient DB error shouldn't 500 the usage
            # endpoint; return zeros so the dashboard renders empty
            # rather than crashing (a real DB outage surfaces elsewhere
            # via the audit write path).
            return {str(h): 0 for h in since_hours}
        return out
    # File mode — scan + filter by timestamp + merchant_id.
    path = getattr(audit_logger, "path", None)
    if path is None or not path.exists():
        return {str(h): 0 for h in since_hours}
    now = datetime.now(timezone.utc)
    cutoffs = {h: now.timestamp() - h * 3600 for h in since_hours}
    counts = {h: 0 for h in since_hours}
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("merchant_id") != merchant_id:
                    continue
                ts = rec.get("timestamp")
                if not ts:
                    continue
                parsed = datetime.fromisoformat(ts).timestamp()
                for h in since_hours:
                    if parsed >= cutoffs[h]:
                        counts[h] += 1
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
    except OSError:
        return {str(h): 0 for h in since_hours}
    return {str(h): counts[h] for h in since_hours}


# --------------------------------------------------------------------- #
# Idempotency helpers (Postgres mode only — file mode uses TTLCache)   #
# --------------------------------------------------------------------- #

def _idem_get_conn(state: dict):
    """Lazily-open a dedicated psycopg connection for the idempotency cache.

    Separate from the AuditLogger / CaseService / model-registry connections
    so a slow idempotency lookup can't block an audit write. The connection
    is cached on ``state`` so we don't pay the connect-handshake cost on
    every request.
    """
    conn = state.get("_idem_conn")
    if conn is not None:
        return conn
    import psycopg

    conn = psycopg.connect(state["settings"].database_url, autocommit=False)
    state["_idem_conn"] = conn
    return conn


def _idem_lookup_postgres(state: dict, key: str | None) -> dict | None:
    """Read a cached idempotent response. Returns the body dict (parsed from
    the JSONB-stored response_body TEXT column) or None if not cached /
    expired. ``expires_at > NOW()`` is the TTL gate.
    """
    if not key:
        return None
    import json as _json

    conn = _idem_get_conn(state)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT response_body, status_code
              FROM idempotency_keys
             WHERE key = %s AND expires_at > NOW()
            """,
            (key,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    body, _status = row
    return _json.loads(body)


def _idem_store_postgres(
    state: dict,
    key: str,
    request_body: str,
    response_body: dict,
    status_code: int,
) -> None:
    """INSERT an idempotency cache row. TTL = NOW() + settings.idem_ttl_seconds."""
    import json as _json
    from datetime import datetime, timedelta, timezone

    conn = _idem_get_conn(state)
    expires = datetime.now(timezone.utc) + timedelta(seconds=state["settings"].idem_ttl_seconds)
    with conn.cursor() as cur:
        # ON CONFLICT DO UPDATE so a re-store of the same key (e.g. a slow
        # retry that lands after the original INSERT) refreshes expires_at
        # rather than erroring.
        cur.execute(
            """
            INSERT INTO idempotency_keys
              (key, request_body, response_body, status_code, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
              request_body  = EXCLUDED.request_body,
              response_body = EXCLUDED.response_body,
              status_code   = EXCLUDED.status_code,
              expires_at    = EXCLUDED.expires_at
            """,
            (key, request_body, _json.dumps(response_body), status_code, expires),
        )
        conn.commit()


def _idem_cleanup_postgres(state: dict) -> None:
    """Best-effort TTL cleanup. Called with 1% probability per request so the
    table doesn't grow forever under burst traffic.
    """
    conn = _idem_get_conn(state)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM idempotency_keys WHERE expires_at < NOW()")
            conn.commit()
    except Exception:  # pragma: no cover — best-effort, never fail the request
        try:
            conn.rollback()
        except Exception:
            pass
