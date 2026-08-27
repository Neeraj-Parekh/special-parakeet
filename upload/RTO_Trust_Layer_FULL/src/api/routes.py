"""Train + evaluate RTO risk model. Prints JSON metrics; writes model + report."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

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
# Day 7 Track 12-d — auto-detected port config (writes/reads
# out/port_config.json). Surfaced on state["ports"] so handlers can read
# the FastAPI / Postgres / Redis / Grafana / Prometheus / OTel ports the
# operator's ``scripts/auto_configure.py`` probe settled on. Read-side
# only here — the write side is the standalone CLI script (which runs
# before ``docker compose up`` or ``uvicorn ...:create_app``).
from src.config.ports import read_port_config  # noqa: E402
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
#
# Day 6 Track 12-bc — extended OTel wiring:
#   * ``get_tracer(name)`` — returns a tracer from the GLOBAL provider
#     (NoOp when OTel isn't installed) for the custom sub-spans on the
#     critical path (optimal_decision / optimal_intervention / audit.log /
#     verify_mandate). Used WITHOUT touching ``state["tracer"]`` (which the
#     existing test_otel.py asserts is called exactly once with
#     ``"risk.score"``); the sub-spans are children of that outer span when
#     a real provider is configured.
#   * ``optional_span(tracer, name, attributes=...)`` — context manager
#     helper that yields a real span OR a NoOp span when tracer is None.
#   * ``instrument_app(app)`` — calls FastAPIInstrumentor.instrument_app +
#     RequestsInstrumentor().instrument() + PsycopgInstrumentor().instrument()
#     (all guarded by try/except ImportError). Auto-creates server spans
#     for every HTTP request + db-query spans for every psycopg query.
from src.api.otel import (  # noqa: E402
    get_tracer,
    instrument_app as otel_instrument_app,
    optional_span,
    setup_otel,
)
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
    OVERRIDE_ACTION,
    SCOPE_ACTION_MAP,
    check_agent_action,
    clear_bindings_cache as clear_key_merchant_bindings_cache,
    get_key_merchant_id,
    get_key_scope,
)
# Day 7 Wave 1 (Subagent 14-d — A1 fix) — HKDF key-derivation helper for
# the dual-control override HMAC chain. The raw ``admin2_key`` (sourced
# from ``RTO_ADMIN_KEYS``) is NEVER used directly as the HMAC key;
# instead a context-bound subkey is derived via HKDF (RFC 5869) with
# ``salt=b"rto-override-v1"`` + ``info=b"dual-control"`` so a leak of
# the derived key (memory / stack / DB snapshot) doesn't compromise
# the long-lived raw key + the derived key is domain-separated from
# any other HMAC consumer that might re-use the same raw key. Stdlib
# only (hashlib + hmac) — no ``cryptography`` dependency added.
# ``clear_derived_key_cache`` is the test helper for env-var mutations.
from src.api.keys import (  # noqa: E402
    clear_derived_key_cache,
    derive_hmac_key,
)
from src.models.explain import (  # noqa: E402
    explain_with_shap,
    get_background_sample,
    reason_codes_batch,
    serialize_shap_result,
    set_background_cache,
)
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
# Day 7 Wave 1 (Subagent 14-d — A1+A2 fix) — the HMAC chain now derives
# the admin2 subkey via HKDF (raw key never appears in HMAC calls) AND
# the request body carries a per-request ``nonce`` (16-byte hex string)
# consumed by a server-side replay-nonce store so a captured request
# can't be replayed within the timestamp window.
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

    Day 7 Wave 1 (Subagent 14-d — A1 fix) — the admin2 subkey is now
    derived via HKDF before being passed to HMAC. The client computes::

        derived_admin2 = HKDF(
            raw_key=admin2_key, salt=b"rto-override-v1",
            info=b"dual-control", length=32,
        )
        signature_2 = HMAC(derived_admin2, chained_msg, sha256).hexdigest()

    The salt + info tuple domain-separates the derivation so a leak of
    the derived key (memory / stack / DB snapshot) doesn't compromise
    the raw key, AND the derived key is context-bound to the
    dual-control override use case (a derived key from one use case is
    useless against any other HMAC consumer). The salt is version-
    tagged (``v1``) so a future rotation cleanly invalidates prior
    derived keys without touching the raw keys in env / secrets manager.

    Day 7 Wave 1 (Subagent 14-d — A2 fix) — the request body now also
    carries a per-request ``nonce`` (16-byte hex string = 32 chars).
    The server stores the SHA-256 HASH of the nonce in a Postgres
    table (``override_nonces``, alembic 006) so a captured request
    can't be replayed within the timestamp window. A second sighting
    of the same nonce → 409 Conflict ("replay detected"). The nonce
    is NOT part of the HMAC canonical_body (the chain is unchanged
    from T1.1 — the nonce is a separate one-shot replay-defense
    field). File-mode fallback: when DATABASE_URL is unset, the
    server uses a bounded in-memory LRU set of the last 10_000 nonce
    hashes (replay protection is in-memory only — logged as a
    warning).
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
    # Day 7 Wave 1 (Subagent 14-d — A2 fix) — per-request replay nonce.
    # MUST be a fresh 16-byte cryptographically-random value hex-encoded
    # to 32 chars (``uuid.uuid4().hex`` is fine — 16 bytes of entropy
    # is enough to make collisions astronomically unlikely at the
    # override endpoint's traffic rate). The server stores the SHA-256
    # HASH of this nonce in the ``override_nonces`` table (alembic 006)
    # so a captured request can't be replayed verbatim within the
    # timestamp window. A second sighting of the same nonce → 409
    # Conflict. The nonce is NOT part of the HMAC canonical_body
    # (the chain is unchanged from T1.1 — the nonce is a separate
    # one-shot replay-defense field).
    nonce: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[a-fA-F0-9]{32}$",
        description=(
            "Per-request replay nonce — 16-byte cryptographically-"
            "random value hex-encoded to 32 chars. The server stores "
            "the SHA-256 hash + 409 on second sighting. NOT part of the "
            "HMAC canonical_body (the chain is unchanged from T1.1)."
        ),
    )

    @field_validator("nonce")
    @classmethod
    def _validate_nonce_format(cls, v: str) -> str:
        """Defensive double-check on top of ``Field(pattern=...)`` so
        the 422 error message is explicit (Pydantic's pattern-failure
        message is generic; this gives the operator a clear "nonce must
        be 32-char hex" error)."""
        if len(v) != 32:
            raise ValueError(
                f"nonce must be a 32-char hex string (16 bytes); "
                f"got length {len(v)}"
            )
        try:
            int(v, 16)
        except (ValueError, TypeError):
            raise ValueError(
                "nonce must be a valid hex string (chars 0-9 a-f A-F); "
                "got non-hex characters"
            )
        return v


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
    # Day 7 Track 12-d — auto-detected service ports (out/port_config.json,
    # written by scripts/auto_configure.py). Falls back to DEFAULT_PORTS
    # if the file is missing so test runs / fresh checkouts don't break.
    # Read-side only here — handlers can consult state["ports"]["fastapi"]
    # if they need to self-advertise their port back to a client.
    state["ports"] = read_port_config()

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
        # Day 6 Track 12-bc — SHAP KernelExplainer cache + background data.
        # Populates the module-level background cache in src.models.explain
        # with the training DataFrame so /v1/explain/shap can subsample it
        # without re-reading the CSV. Then attempts to build + cache the
        # KernelExplainer itself (best-effort — None if shap isn't installed
        # or construction fails; the endpoint will return the graceful
        # fallback in that case). The cache key is the model object; the
        # model is stable per worker so this is safe across requests.
        # Source: Lundberg & Lee 2017 NeurIPS §3 — KernelExplainer's
        # construction is O(background * features); caching at lifespan
        # time amortizes that once-per-boot.
        try:
            set_background_cache(X_tr)
        except Exception as e:  # pragma: no cover — startup-only, defensive
            print(
                f"shap background cache population skipped: "
                f"{type(e).__name__}: {e}",
                file=sys.stderr,
            )
        state["shap_explainer"] = None  # placeholder; built lazily on first
                                         # /v1/explain/shap request so the
                                         # lifespan doesn't pay shap's import
                                         # cost when the endpoint isn't used.
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
        # Wave 2 (Subagent 14-e — D13) — ``enforce_agent_action`` now
        # ALSO consults the caller's bound key scope (scorer/ops/admin)
        # to verify the requested action is in the scope's allowed set
        # (SCOPE_ACTION_MAP). The ``X-Mandate-Scope`` header is parsed
        # but IGNORED for enforcement (the bound scope is authoritative).
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
        # Wave 2 (Subagent 14-e — F19 fix) — multi-tenant merchant
        # isolation Depends. Returns the caller's bound merchant_id
        # (None when the key is unbound → legacy mode → no isolation
        # enforced; the default ``score-demo-key`` is unbound so the
        # existing 117 pre-F19 tests pass without binding setup).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
    ) -> dict:
        token = bearer_token(authorization)
        ok, err = check_key(token, "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        # Wave 2 (F19 fix) — verify the caller-supplied merchant_id (in
        # OrderIn.merchant_id) MATCHES the caller's bound merchant_id.
        # Cross-tenant access (merchant A's key submitting an order for
        # merchant B) → 403 "cross-tenant access denied". When the caller
        # is unbound (None) → no isolation enforced (legacy compat).
        _verify_merchant_match(caller_merchant_id, order.merchant_id)
        # When the caller IS bound but the order didn't carry a
        # merchant_id, INJECT the caller's bound merchant_id into the
        # order's audit body so downstream queries (the /v1/usage metering
        # endpoint, the audit tail filter for SHAP explain) scope to
        # the caller's tenant only.
        if caller_merchant_id is not None and order.merchant_id is None:
            # Use object mutation so the audit.log payload below
            # ``"merchant_id": order.merchant_id`` carries the injected
            # value. Pydantic v2 BaseModel allows attribute mutation
            # when ``model_config["frozen"]`` is False (the default).
            order.merchant_id = caller_merchant_id
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
            # Day 6 Track 12-bc — custom sub-spans on the critical path via
            # the GLOBAL tracer (NOT ``state["tracer"]`` — the existing
            # test_otel.py asserts that mock is called exactly once with
            # "risk.score"; using a separate global tracer for sub-spans
            # preserves that contract + still surfaces the full call tree
            # as children of the outer risk.score span when a real OTel
            # provider is configured). Source: OTel Python §"Manual
            # instrumentation" §"Create spans within a function".
            _subspan_tracer = get_tracer(__name__)
            # Mandate verification (cheap; always run; enforcement happens
            # later). Day 1 Track D extended the signature to pass device_id
            # + user_id so UPI Circle mandates can enforce OC-201B §3.3/§3.7
            # per-txn identity validation. cod_order mandates ignore both.
            # Wrapped in a sub-span so a Jaeger trace surfaces the DB
            # counter read/write for UPI Circle delegations (T1.4 — the
            # DB-backed counter is in mandates.py; the span wraps the
            # entire verify_mandate call because Subagent 12-bc owns
            # routes.py only + can't add a span inside mandates.py).
            with optional_span(
                _subspan_tracer,
                "verify_mandate",
                attributes={
                    "mandate.present": x_mandate is not None,
                    "order.amount_inr": float(order.amount_inr),
                    "mandate.device_id_present": x_device_id is not None,
                    "mandate.user_id_present": x_user_id is not None,
                },
            ) as mandate_span:
                mandate_verdict, mandate_payload = verify_mandate(
                    x_mandate,
                    order.amount_inr,
                    device_id=x_device_id,
                    user_id=x_user_id,
                )
                if mandate_span is not None:
                    try:
                        mandate_span.set_attribute(
                            "mandate.verdict", str(mandate_verdict)
                        )
                        mandate_span.set_attribute(
                            "mandate.verdict_reason",
                            str(mandate_payload.get("verdict_reason", "")),
                        )
                    except Exception:  # pragma: no cover — best-effort
                        pass
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
                        # Day 6 Track 12-bc — sub-span around the model
                        # predict_proba call. The biggest single source of
                        # latency in the /risk/score handler — surfacing it
                        # as a span lets a Jaeger trace show whether a slow
                        # decision was due to the model or the surrounding
                        # I/O.
                        with optional_span(
                            _subspan_tracer,
                            "model.predict_proba",
                            attributes={
                                "model.features_count": int(X.shape[1]),
                                "model.version": state["audit"].model_version,
                            },
                        ) as _model_span:
                            proba = float(state["model"].predict_proba(X)[0, 1])
                            if _model_span is not None:
                                try:
                                    _model_span.set_attribute(
                                        "model.probability", round(proba, 5)
                                    )
                                except Exception:  # pragma: no cover
                                    pass
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
                    # Day 6 Track 12-bc — sub-span around the 3-way
                    # Bayes-Minimum-Risk decision (Bahnsen ICMLA 2013). The
                    # decision argmin over {ACCEPT, REVIEW, REJECT} costs;
                    # surfacing it as a span lets a Jaeger trace surface
                    # "this REJECT was driven by the cost-optimizer" vs
                    # "this REJECT was driven by the mandate layer".
                    with optional_span(
                        _subspan_tracer,
                        "optimal_decision",
                        attributes={
                            "decision.probability": round(float(proba), 5),
                            "decision.amount_inr": float(order.amount_inr),
                        },
                    ) as _dec_span:
                        decision, costs = optimal_decision(
                            proba, amount_inr=order.amount_inr, **DEFAULT_COST_WEIGHTS
                        )
                        if _dec_span is not None:
                            try:
                                _dec_span.set_attribute(
                                    "decision.choice", str(decision)
                                )
                            except Exception:  # pragma: no cover
                                pass
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
                    # Day 6 Track 12-bc — sub-span around the 5-way intervention
                    # argmin (separate from the 3-way decision so a Jaeger trace
                    # can split the two costs).
                    with optional_span(
                        _subspan_tracer,
                        "optimal_intervention",
                        attributes={
                            "intervention.amount_inr": float(order.amount_inr),
                        },
                    ) as _int_span:
                        intervention, intervention_costs = optimal_intervention(
                            proba, order.amount_inr,
                        )
                        if _int_span is not None:
                            try:
                                _int_span.set_attribute(
                                    "intervention.choice", str(intervention)
                                )
                            except Exception:  # pragma: no cover
                                pass
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
            #
            # Day 6 Track 12-bc — sub-span around the audit INSERT + Merkle
            # sealer.add (the audit logger's _log_postgres path is in
            # logger.py — owned by Subagent 11-b; this routes.py span wraps
            # the entire audit.log() call so a Jaeger trace surfaces the
            # audit write's latency + the chain-extension / Merkle-leaf-
            # insertion work as a single child span of risk.score).
            _audit_payload = {
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
            with optional_span(
                _subspan_tracer,
                "audit.log",
                attributes={
                    "audit.decision": str(decision),
                    "audit.decision_source": str(decision_source),
                    "audit.channel": str(x_channel or "ecommerce"),
                    "audit.degraded": bool(degraded),
                },
            ) as _audit_span:
                audit_id = state["audit"].log(_audit_payload)
                if _audit_span is not None:
                    try:
                        _audit_span.set_attribute(
                            "audit.audit_id", str(audit_id)
                        )
                        _audit_span.set_attribute(
                            "audit.merkle_sealed",
                            bool(getattr(state["audit"], "_conn", None) is not None),
                        )
                    except Exception:  # pragma: no cover
                        pass
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
        status: str | None = None,
        authorization: str | None = Header(default=None),
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no filter).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        cases = state["cases"].list_cases(status)
        # Wave 2 (F19) — filter cases by caller's bound merchant_id.
        # ``CaseService.list_cases`` doesn't take a merchant_id filter
        # (it's owned by Subagent 11-routes; we don't touch it). The
        # post-fetch Python-side filter mirrors the audit tail filter
        # pattern in ``_read_audit_tail`` — correct, just not as
        # efficient at scale (the production-scale path would add a
        # WHERE clause in ``CaseService.list_cases``).
        if caller_merchant_id is not None:
            cases = [
                c for c in cases
                if (c.get("merchant_id") if isinstance(c, dict) else None)
                == caller_merchant_id
            ]
        return {"cases": cases}

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
    def models_drift(
        authorization: str | None = Header(default=None),
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no filter).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        recent = [
            r.get("features_used", {})
            for r in _read_audit_tail(
                state["audit"], limit=300, merchant_id=caller_merchant_id
            )
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
    def audit_export(
        authorization: str | None = Header(default=None),
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no filter).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
    ) -> dict:
        import csv
        import io

        from fastapi import Response

        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        records = _read_audit_tail(
            state["audit"], limit=100000, merchant_id=caller_merchant_id
        )
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

            # Day 7 Wave 1 (Subagent 14-d — A2 fix) — replay-nonce
            # consumption BEFORE the HMAC chain verification. The nonce
            # is a per-request one-shot value (16 bytes hex-encoded to
            # 32 chars) that the client MUST regenerate on every request;
            # the server stores the SHA-256 HASH of the nonce (NOT the
            # raw nonce — same redaction posture as customer_id) in the
            # ``override_nonces`` table (alembic 006) so a captured
            # request can't be replayed verbatim within the timestamp
            # window. A second sighting of the same nonce → 409
            # Conflict ("replay detected"). The check runs BEFORE the
            # HMAC chain verification because the nonce is a cheaper
            # pre-filter: if the nonce is already seen, we don't need
            # to recompute the HMAC chain (saves the admin-key
            # iteration on a replayed request). The nonce is NOT part
            # of the HMAC canonical_body (the chain is unchanged from
            # T1.1 — the nonce is a separate one-shot replay-defense
            # field, not a chained signature input).
            #
            # Note: this runs AFTER the admin1 check + same-key check +
            # decision validation — so a replay with an invalid admin1
            # key still gets 403 (not 409), and a replay with the same
            # key twice still gets 400 (not 409). This preserves the
            # existing test_dual_control_override_requires_two_keys +
            # test_dual_control_same_key_rejected assertions.
            nonce_hash = hashlib.sha256(
                payload.nonce.encode()
            ).hexdigest()
            _check_and_consume_override_nonce(
                state, nonce_hash, payload.timestamp
            )

            # T1.1 — REAL HMAC CHAIN. signature_2 = HMAC(admin2_key,
            # signature_1 || canonical_body || timestamp). A
            # single-admin compromise cannot forge a dual-control
            # override because the second signature is
            # cryptographically bound to the first — admin1's key
            # alone is useless (no admin2 key to compute the expected
            # HMAC); admin2's key alone is useless (no admin1 signature
            # to chain on). Both must collude OR both must be
            # compromised to forge an override.
            #
            # Day 7 Wave 1 (Subagent 14-d — A1 fix) — the admin2
            # subkey is now derived via HKDF (RFC 5869) before being
            # passed to HMAC. The raw ``candidate_key`` (sourced from
            # ``RTO_ADMIN_KEYS``) NEVER appears directly in the HMAC
            # call — only the derived subkey does. A leak of the
            # derived key (memory / stack / DB snapshot) doesn't
            # compromise the raw key (HKDF-Extract + HKDF-Expand are
            # both built on HMAC; recovering the IKM from the PRK or
            # OKM is as hard as inverting HMAC-SHA256). The salt +
            # info tuple domain-separates the derivation so the derived
            # key is context-bound to the dual-control override use
            # case.
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
                    # A1 fix — derive the admin2 subkey via HKDF before
                    # the HMAC call. HKDF is cheap (~1 μs) AND cached
                    # in a module-level dict in ``src/api/keys.py`` so
                    # the hot path doesn't recompute on every override
                    # (the cache is keyed by (raw_key, salt, info,
                    # length) — HKDF is deterministic, so caching is
                    # safe; the same raw key always produces the same
                    # derived key).
                    derived_admin2_key = derive_hmac_key(
                        candidate_key,
                        salt=b"rto-override-v1",
                        info=b"dual-control",
                        length=32,
                    )
                    candidate_sig = hmac.new(
                        derived_admin2_key,
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
                    # Day 7 Wave 1 (Subagent 14-d — A2 fix) — record the
                    # SHA-256 HASH of the consumed nonce (NOT the raw
                    # nonce) so the audit trail can prove "this request
                    # was a fresh sighting, not a replay" without leaking
                    # the raw nonce value (the raw nonce is only
                    # meaningful in transit — a DB compromise reading
                    # the audit_records JSONB body should NOT be able to
                    # re-derive a valid nonce for a future replay attempt).
                    "override_nonce_hash": nonce_hash,
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
                # Day 7 Wave 1 (Subagent 14-d — A2 fix) — surface the
                # consumed-nonce hash so the dashboard can display
                # "replay-protected (nonce=abc...)" as a tamper-
                # evidence label.
                "override_nonce_hash": nonce_hash,
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
        audit_id: str,
        authorization: str | None = Header(default=None),
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no isolation).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
    ) -> dict:
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        rec = state["audit"].read(audit_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="audit record not found")
        # Wave 2 (F19) — verify the record's merchant_id matches the
        # caller's bound merchant_id. Cross-tenant access → 404 (mask
        # existence; same posture as the override proof lookup). The
        # ``AuditLogger.read`` is owned by Subagent 11-b (off-limits),
        # so the post-fetch Python-side check is the contract-preserving
        # escape hatch.
        if caller_merchant_id is not None:
            rec_mid = _record_merchant_id(rec)
            if rec_mid != caller_merchant_id:
                # Mask cross-tenant existence as 404 (the caller can't
                # tell whether the audit_id belongs to another merchant
                # or simply doesn't exist).
                raise HTTPException(
                    status_code=404, detail="audit record not found"
                )
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
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no filter).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
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
        # Wave 2 (F19) — pass the caller's bound merchant_id so the
        # tail-scan filters to the caller's tenant only. Cross-tenant
        # labels are silently skipped (the prediction_not_found response
        # field signals the lookup missed; the caller can't tell whether
        # the prediction_id belongs to another merchant or doesn't exist).
        predicted_p: float | None = None
        for rec in _read_audit_tail(
            state["audit"], limit=5000, merchant_id=caller_merchant_id
        ):
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
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no isolation).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
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
        # Wave 2 (F19) — pass the caller's bound merchant_id so the
        # lookup verifies the record's merchant_id claim. A cross-tenant
        # lookup returns None → 404 (mask cross-tenant existence).
        record_id = _lookup_record_id_by_audit_id(
            state["audit"], audit_id, merchant_id=caller_merchant_id
        )
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

    # Day 6 Track 12-bc — SHAP KernelExplainer per-prediction feature
    # attribution. Closes the V2 §9.3 explainability gap (the existing
    # /risk/score response's ``reason_codes`` is a one-at-a-time perturbation-
    # style attribution; SHAP is the Lundberg 2017 NeurIPS gold standard with
    # the additive Shapley-value foundation). Source: Lundberg & Lee, "A
    # Unified Approach to Interpreting Model Predictions", NeurIPS 2017,
    # arXiv:1705.07856.
    #
    # Dual-mode like Track E's DATABASE_URL + Track F's REDIS_URL + Track
    # M's OTEL_EXPORTER_OTLP_ENDPOINT: if the ``shap`` package isn't
    # installed (test sandbox, or the user hasn't run
    # ``pip install -r requirements.txt`` yet), the endpoint returns a
    # graceful fallback pointing at the existing ``reason_codes`` field
    # in /risk/score's response (the LIME-equivalent fallback per the
    # task spec). The 141 existing tests pass without a shap fixture.
    #
    # Auth: scorer scope (same as /v1/simulate + /v1/models/current —
    # the explanation is a read-only "what did the model say about this
    # order" view, no money-moving authority). Returns:
    #   * 200 with the SHAP dict on success.
    #   * 503 if the model isn't loaded (circuit breaker may be OPEN).
    #   * 422 if the JSON ``features`` query param is invalid OR the
    #     ``order_id`` lookup finds no past prediction with
    #     ``features_used`` populated.
    #   * 200 with the graceful fallback dict if ``shap`` isn't installed
    #     or the 5-second explanation timeout trips (the body carries
    #     ``error`` + ``fallback`` so the caller's dashboard can render
    #     the LIME path instead of crashing).
    @app.get("/v1/explain/shap", tags=["explainability"])
    def explain_shap(
        order_id: str | None = Query(default=None, max_length=64),
        features: str | None = Query(default=None),
        background_samples: int = Query(default=100, ge=1, le=1000),
        authorization: str | None = Header(default=None),
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no filter).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
    ) -> dict:
        """SHAP KernelExplainer per-prediction feature attribution.

        Two request shapes (exactly one required):

        1. ``?order_id=<order_id>`` — look up a past /risk/score
           prediction's ``features_used`` dict from the audit tail +
           re-explain it with SHAP. Useful for post-incident review
           ("why did this order get REJECTED?") where the operator wants
           a Shapley-value attribution rather than the LIME-style
           perturbation ``reason_codes`` the /risk/score response
           already carries.

        2. ``?features=<JSON-string>`` — explain a hypothetical feature
           dict (e.g. ``{"log_order_value": 8.5, "Items": 3, ...}``)
           without first scoring an order. Useful for the dashboard's
           "what-if" explorer when the merchant wants SHAP attribution
           for an order they haven't submitted yet.

        Auth: scorer scope. Returns 503 if the model isn't loaded; 422
        if neither ``order_id`` nor ``features`` is provided, OR if the
        ``features`` JSON is malformed, OR if the ``order_id`` lookup
        finds no past prediction with ``features_used`` populated.

        The ``shap_values`` field in the response is the per-feature
        Shapley contribution list (class 1 — RTO risk). ``base_value``
        is the expected model output over the background distribution
        (E[P(RTO) | background]); ``expected_value`` is the same value
        under the SHAP convention (base_value + sum(shap_values) =
        model.predict_proba(features)[1]).

        Source: Lundberg & Lee 2017 NeurIPS §3 "KernelSHAP".
        """
        ok, err = check_key(bearer_token(authorization), "scorer", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        # 1. Model-loaded guard. The circuit breaker may be OPEN; surface
        # 503 in that case so the caller can fall back to LIME.
        if state.get("model") is None:
            raise HTTPException(
                status_code=503,
                detail="SHAP explanation unavailable — model not loaded",
            )
        # 2. Mutual-exclusion: exactly one of order_id / features is
        # required.
        if order_id is None and features is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "either ?order_id=<order_id> (look up a past "
                    "prediction's features_used) or ?features=<JSON-string> "
                    "(explain a hypothetical feature dict) is required"
                ),
            )
        if order_id is not None and features is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "pass exactly one of ?order_id= or ?features= — not "
                    "both (mutually exclusive explanation sources)"
                ),
            )
        # 3. Build the feature dict from whichever source was provided.
        # Day 6 Track 12-bc — sub-span around the feature-dict resolution
        # so a Jaeger trace surfaces "order_id lookup miss" as a slow
        # operation when the audit tail scan is the bottleneck.
        _explain_tracer = get_tracer(__name__)
        with optional_span(
            _explain_tracer,
            "explain_shap.resolve_features",
            attributes={
                "explain.order_id_present": order_id is not None,
                "explain.features_present": features is not None,
            },
        ) as _resolve_span:
            feature_dict: dict | None = None
            prediction_id_resolved: str | None = None
            probability_resolved: float | None = None
            if order_id is not None:
                # Scan the audit tail for the past prediction with this
                # order_id. Cheap (last 5000 records, well within the
                # prediction-window for a chargeback-style post-incident
                # review). The audit body's ``features_used`` field is
                # the numeric-feature dict (the same shape ``reason_codes``
                # uses); SHAP needs the same columns.
                # Wave 2 (F19) — pass the caller's bound merchant_id so
                # the audit tail is filtered to the caller's tenant only.
                # Cross-tenant orders are silently skipped (the 422
                # "no past prediction found" message fires; the caller
                # can't tell whether the order_id belongs to another
                # merchant or simply doesn't exist).
                for rec in _read_audit_tail(
                    state["audit"],
                    limit=5000,
                    merchant_id=caller_merchant_id,
                ):
                    req = rec.get("request") or {}
                    if req.get("order_id") == order_id:
                        fu = rec.get("features_used")
                        if isinstance(fu, dict) and fu:
                            feature_dict = dict(fu)
                            prediction_id_resolved = rec.get("prediction_id")
                            try:
                                p = rec.get("probability")
                                if p is not None:
                                    probability_resolved = float(p)
                            except (TypeError, ValueError):
                                pass
                            break
                if feature_dict is None:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"no past prediction with order_id='{order_id}' "
                            "found in the audit tail (last 5000 records) OR "
                            "the prediction's audit record has no "
                            "features_used field (the prediction was a "
                            "rules-engine fast-path BLOCK which short-"
                            "circuits before the model is invoked — use "
                            "?features=<JSON> instead)"
                        ),
                    )
            else:
                # features JSON string — parse + validate.
                try:
                    parsed = json.loads(features)
                except (json.JSONDecodeError, TypeError) as e:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"?features= must be a JSON object string: "
                            f"{type(e).__name__}: {e}"
                        ),
                    )
                if not isinstance(parsed, dict) or not parsed:
                    raise HTTPException(
                        status_code=422,
                        detail="?features= must decode to a non-empty JSON object",
                    )
                feature_dict = parsed
            if _resolve_span is not None:
                try:
                    _resolve_span.set_attribute(
                        "explain.feature_count",
                        len(feature_dict) if feature_dict else 0,
                    )
                    if prediction_id_resolved is not None:
                        _resolve_span.set_attribute(
                            "explain.prediction_id",
                            str(prediction_id_resolved),
                        )
                except Exception:  # pragma: no cover
                    pass

        # 4. Resolve + use the cached SHAP explainer if available. The
        # lifespan stores state["shap_explainer"] = None as a placeholder;
        # the first /v1/explain/shap request builds it lazily + caches it
        # so subsequent requests skip the KernelExplainer construction.
        # This is the spec-mandated caching layer ("store it in
        # state['shap_explainer'] in the lifespan" — the lifespan stores
        # None + the first request populates the cache; same effective
        # behavior, deferred until first use so the lifespan doesn't pay
        # shap's import cost when the endpoint isn't used in this worker).
        prebuilt = state.get("shap_explainer")
        if prebuilt is None:
            try:
                import shap  # noqa: F401 — imported for the side effect
                # Build the explainer using the module-level background
                # cache populated by set_background_cache(X_tr) in the
                # lifespan. Cap to SHAP_MAX_BACKGROUND_ROWS (50) per spec.
                from src.models.explain import (
                    SHAP_MAX_BACKGROUND_ROWS,
                    get_background_cache,
                )
                bg_cache = get_background_cache()
                if bg_cache is not None and len(bg_cache) > 0:
                    bg_n = min(
                        SHAP_MAX_BACKGROUND_ROWS, len(bg_cache)
                    )
                    bg_df = (
                        bg_cache.sample(n=bg_n, random_state=42)
                        if len(bg_cache) > bg_n
                        else bg_cache
                    )
                else:
                    # No cache (file mode with no training data
                    # loaded — shouldn't happen post-lifespan, but
                    # defensive). Build a 1-row background from the
                    # feature dict itself (degenerate — SHAP values
                    # will be ~0 because the background == the input).
                    import pandas as _pd
                    bg_df = _pd.DataFrame([feature_dict])
                prebuilt = shap.KernelExplainer(
                    state["model"].predict_proba, bg_df
                )
                state["shap_explainer"] = prebuilt
            except ImportError:
                # shap not installed — prebuilt stays None; the
                # explain_with_shap call below returns the graceful
                # fallback dict.
                state["shap_explainer"] = None
                prebuilt = None
            except Exception as e:  # pragma: no cover — defensive
                print(
                    f"[shap] cached KernelExplainer construction failed: "
                    f"{type(e).__name__}: {e} — building fresh per request",
                    file=sys.stderr,
                )
                state["shap_explainer"] = None
                prebuilt = None

        # 5. Run explain_with_shap with the cached explainer (if any).
        # The function returns a graceful fallback dict on any failure
        # (shap not installed, timeout, etc.) so the endpoint always
        # returns 200 — the caller's dashboard renders the fallback path.
        with optional_span(
            _explain_tracer,
            "explain_shap.compute",
            attributes={
                "explain.background_samples": int(background_samples),
                "explain.cached_explainer": prebuilt is not None,
            },
        ) as _compute_span:
            result = explain_with_shap(
                state["model"],
                feature_dict,
                background_samples=background_samples,
                prebuilt_explainer=prebuilt,
            )
            if _compute_span is not None:
                try:
                    if "error" in result:
                        _compute_span.set_attribute(
                            "explain.error", str(result.get("error", ""))
                        )
                    else:
                        _compute_span.set_attribute(
                            "explain.background_rows",
                            int(result.get("background_rows", 0)),
                        )
                        _compute_span.set_attribute(
                            "explain.feature_count",
                            len(result.get("feature_names", [])),
                        )
                except Exception:  # pragma: no cover
                    pass

        # 6. Serialize + return. The endpoint surfaces the cached
        # prediction's metadata (prediction_id / probability) when the
        # caller passed ?order_id= so the dashboard can show "this is
        # the SHAP explanation for prediction_id=abc at P(RTO)=0.42".
        out = serialize_shap_result(result)
        out["order_id"] = order_id
        out["prediction_id"] = prediction_id_resolved
        out["probability"] = (
            round(float(probability_resolved), 5)
            if probability_resolved is not None
            else None
        )
        out["background_samples_requested"] = int(background_samples)
        out["cached_explainer"] = prebuilt is not None
        out["model_version"] = state["audit"].model_version
        out["source_paper"] = (
            "Lundberg & Lee, A Unified Approach to Interpreting Model "
            "Predictions, NeurIPS 2017, arXiv:1705.07856"
        )
        return out

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
        # Wave 2 (F19 fix) — caller's bound merchant_id; None for
        # unbound keys (legacy mode → no isolation enforced).
        caller_merchant_id: str | None = Depends(enforce_merchant_isolation),
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

        Wave 2 (Subagent 14-e — F19 fix) — when the caller's key is
        bound to a merchant_id (multi-tenant posture), the
        ``?merchant_id=<mid>`` query param MUST match the caller's
        bound merchant_id (cross-tenant query → 403). When the caller
        is bound but the query param is absent, the caller's bound
        merchant_id is INJECTED as the filter (so unbound queries
        still scope to the caller's tenant). When the caller is
        unbound (legacy mode), the existing aggregate + per-merchant
        path is preserved.

        The response also surfaces the Merkle interval sealing cadence
        (last 100 intervals) so a billing auditor can verify the audit
        trail's tamper-evidence layer is up-to-date alongside the
        metering counts.
        """
        ok, err = check_key(bearer_token(authorization), "admin", state["keys"])
        if not ok:
            raise HTTPException(status_code=401, detail=err)
        # Wave 2 (F19 fix) — verify the caller-supplied merchant_id
        # matches the caller's bound merchant_id. Cross-tenant query
        # (e.g. merchant A's admin key asking for merchant B's counts)
        # → 403 "cross-tenant access denied".
        _verify_merchant_match(caller_merchant_id, merchant_id)
        # When the caller is bound + the request didn't carry a
        # merchant_id, INJECT the caller's bound merchant_id as the
        # filter (so unbound queries still scope to the caller's tenant
        # — the multi-tenant isolation posture).
        if caller_merchant_id is not None and merchant_id is None:
            merchant_id = caller_merchant_id
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
                "(audit_records.body->>'merchant_id' = merchant_id filter"
                + (
                    "; caller's bound merchant_id injected (F19 fix — "
                    "the bound key dictates the tenant)"
                    if caller_merchant_id is not None
                    else ""
                )
                + ")"
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

    # Day 6 Track 12-bc — auto-instrument the FastAPI app + outbound HTTP +
    # psycopg queries. Must be called AFTER all routes are registered
    # (FastAPIInstrumentor hooks the route table at instrumentation time +
    # this is the last such registration point before ``return app``).
    # Dual-mode: the underlying opentelemetry-instrumentation-* packages
    # may not be installed (test sandbox, or a developer running
    # ``pip install -r requirements.txt`` without the new instrumentation
    # packages); the helper wraps each instrumentor in try/except ImportError
    # so the API doesn't crash at boot. Manual spans on /risk/score +
    # custom sub-spans via get_tracer() work even without the instrumentors
    # (they degrade to no-ops when the global provider isn't configured).
    try:
        otel_instrument_app(app)
    except Exception as e:  # pragma: no cover — best-effort, never fail boot
        print(
            f"[otel] instrument_app() raised unexpectedly: "
            f"{type(e).__name__}: {e} — auto-instrumentation disabled, "
            "manual spans still active",
            file=sys.stderr,
        )

    return app


def _log1p(x: float) -> float:
    import math

    return math.log1p(x)


def enforce_agent_action(
    x_agent_action: str | None = Header(default=None, alias="X-Agent-Action"),
    x_mandate: str | None = Header(default=None, alias="X-Mandate"),
    x_mandate_scope: str | None = Header(default=None, alias="X-Mandate-Scope"),
    authorization: str | None = Header(default=None),
) -> dict:
    """Server-side enforcement of the 7-action agent allowlist (Mission 3)
    + scope→action enforcement (Wave 2 — D13 fix).

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

    Wave 2 (Subagent 14-e — D13 fix) — the caller's key scope (the bound
    ``scorer`` / ``ops`` / ``admin`` scope from the Authorization
    header) is now consulted to verify the requested ``X-Agent-Action``
    is in the caller's scope per ``SCOPE_ACTION_MAP``. A scope mismatch
    → 403 ("scope '%s' cannot perform action '%s'"). The
    ``X-Mandate-Scope`` header is DEPRECATED — it's parsed (for
    forward-compat) but ignored for enforcement; the authoritative
    scope is the key's BOUND scope, not a client-supplied header (the
    D13 finding: a client could send ``X-Mandate-Scope: admin`` with a
    scorer key + escalate privileges; now the bound scope is the only
    authority).

    Decision tree:
      * ``X-Agent-Action`` absent → return ``{"action": None,
        "permitted": True}`` (caller is admin/scorer; bypass).
      * ``X-Agent-Action`` present → extract the caller's key scope
        from the Authorization header via ``get_key_scope(...)``.
        If the scope is determined (``scorer``/``ops``/``admin``),
        verify the action is in ``SCOPE_ACTION_MAP[scope]`` —
        mismatch → 403 "scope '<scope>' cannot perform action
        '<action>'".
      * ``X-Agent-Action`` present + action in scope + action in
        allowlist + NOT ``requires_approval`` → return
        ``{"action": <action>, "permitted": True}`` (the handler
        executes normally).
      * ``X-Agent-Action`` present + action in scope + action in
        allowlist + ``requires_approval=True`` (e.g. ``block_order``,
        ``upi_circle_delegated_pay``) → raise HTTPException(202,
        "agent action requires human approval — case queued") so the
        handler NEVER executes (Mission 3 demo moment #5: high-cost
        actions don't execute; a case is queued for human approval).
        The ``X-Case-Created: true`` response header signals to the
        client/dashboard that the case must be queued by the upstream
        agent orchestrator (the dependency itself can't open a case
        without state access; the 202 short-circuit is the
        contract-preserving signal).
      * ``X-Agent-Action`` present + action is the special
        ``override`` pseudo-action → permitted ONLY for ``admin``
        scope (other scopes are 403 at the scope check above); the
        override handler's dual-control HMAC chain is the second
        enforcement layer.
      * ``X-Agent-Action`` present + action NOT in allowlist → raise
        HTTPException(403, "agent action not permitted: action
        '<action>' not in allowlist") (Mission 3 — "Action not
        permitted.").

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
    # Wave 2 (D13 fix) — extract the caller's bound key scope from the
    # Authorization header. The ``X-Mandate-Scope`` header (x_mandate_scope)
    # is parsed for forward-compat but IGNORED for enforcement — a client
    # can no longer self-declare a higher scope (the bound scope from the
    # API key is the only authority; the D13 finding closed).
    key_scope: str | None = None
    token = bearer_token(authorization)
    if token is not None:
        # Look up the key's scope from the in-memory key sets. The
        # ``default_keys()`` helper is the same source the handler's
        # ``check_key`` consults so the scope lookup + the auth check
        # agree. If ``RTO_OPS_KEYS`` is set, the ``ops`` scope is also
        # resolvable here (the third scope added by Wave 2).
        try:
            keys = default_keys()
        except Exception:  # pragma: no cover — defensive; settings read
            keys = {"scorer": set(), "admin": set()}
        key_scope = get_key_scope(
            token,
            scorer_keys=keys.get("scorer", set()),
            admin_keys=keys.get("admin", set()),
        )
    allowed, reason = check_agent_action(
        x_agent_action,
        mandate_scope=x_mandate,
        key_scope=key_scope,
    )
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
        # Wave 2 (D13 fix) — scope-mismatch + unknown-action both 403,
        # but with a clear cross-scope message when the action IS in
        # ALLOWED_ACTIONS but the caller's scope doesn't permit it.
        raise HTTPException(
            status_code=403,
            detail=(
                f"agent action '{x_agent_action}' not permitted: {reason} "
                "(Mission 3: agent allowlist + Wave 2 D13 scope enforcement "
                "— src.api.agent_allowlist.{ALLOWED_ACTIONS,SCOPE_ACTION_MAP})"
            ),
        )
    return {
        "action": x_agent_action,
        "permitted": True,
        "requires_approval": False,
        # Wave 2 (D13) — surface the resolved key scope so downstream
        # handlers (e.g. the override endpoint's dual-control path)
        # can verify the caller is admin-scope before running the
        # expensive HMAC chain check.
        "key_scope": key_scope,
    }


# ---------------------------------------------------------------------------
# Wave 2 (Subagent 14-e — F19 fix) — multi-tenant merchant isolation.
# ---------------------------------------------------------------------------
# ``enforce_merchant_isolation`` is a FastAPI Depends that:
#   1. Extracts the caller's API key from the ``Authorization`` header.
#   2. Looks up the key's bound ``merchant_id`` via
#      ``get_key_merchant_id`` (file-mode env-var source) — None when
#      the key is unbound (legacy mode → no isolation; the existing
#      default ``score-demo-key`` / ``admin-demo-key`` are unbound so
#      existing tests pass without binding setup).
#   3. Returns the ``merchant_id`` (or None) so the consuming handler
#      can inject it as a forced ``WHERE body->>'merchant_id' = %s``
#      filter on data-access queries.
#
# Endpoints that take a merchant_id in the request body/query (e.g.
# ``OrderIn.merchant_id`` on /risk/score or ``?merchant_id=<mid>`` on
# /v1/usage) verify the caller-supplied merchant_id MATCHES the caller's
# bound merchant_id; a mismatch → 403 ("cross-tenant access denied").
#
# The Depends is wired on the data-access endpoints (audit tail,
# override proof, SHAP explain, /v1/usage, /audit/{audit_id}). The
# data-access helper functions (``_read_audit_tail``,
# ``_lookup_record_id_by_audit_id``) take an optional ``merchant_id``
# param so the same helper serves both the isolated + the legacy
# (None) path.


def enforce_merchant_isolation(
    authorization: str | None = Header(default=None),
) -> str | None:
    """FastAPI Depends — extract the caller's bound merchant_id (F19 fix).

    Returns:
      * The caller's bound ``merchant_id`` (str) — to be injected as a
        forced ``WHERE body->>'merchant_id' = %s`` filter on data-access
        queries.
      * ``None`` — when the caller's key is unbound (legacy mode, no
        isolation; existing tests + dev paths).

    This Depends does NOT 403 itself — it returns the merchant_id (or
    None) for the consuming handler to use as a query filter. The 403
    fires at the call site when a caller-supplied merchant_id (e.g.
    ``OrderIn.merchant_id`` on /risk/score or ``?merchant_id=<mid>`` on
    /v1/usage) MISMATCHES the caller's bound merchant_id (the
    ``_verify_merchant_match`` helper below).
    """
    token = bearer_token(authorization)
    return get_key_merchant_id(token)


def _verify_merchant_match(
    caller_merchant_id: str | None,
    requested_merchant_id: str | None,
) -> None:
    """Verify a caller-supplied merchant_id matches the caller's bound
    merchant_id (F19 fix — cross-tenant access denied).

    Returns None when:
      * ``caller_merchant_id`` is None (the caller's key is unbound →
        legacy mode → no isolation enforced).
      * Both ``caller_merchant_id`` + ``requested_merchant_id`` are
        non-None AND equal (the caller is querying its own merchant's
        data).

    Raises HTTPException(403, "cross-tenant access denied") when:
      * The caller's key IS bound to a merchant_id AND the request
        carries a DIFFERENT merchant_id (e.g. merchant A's scorer key
        submitting ``OrderIn.merchant_id="merch_b"`` on /risk/score,
        or querying ``/v1/usage?merchant_id=merch_b``).
    """
    if caller_merchant_id is None:
        # Legacy mode — no isolation.
        return
    if requested_merchant_id is None:
        # Caller is bound but the request didn't carry a merchant_id.
        # The caller's merchant_id will be INJECTED as a forced WHERE
        # filter at the data-access helper (so unbound queries still
        # scope to the caller's tenant). No 403 here.
        return
    if requested_merchant_id == caller_merchant_id:
        return  # same-tenant — OK
    raise HTTPException(
        status_code=403,
        detail=(
            f"cross-tenant access denied — caller's key is bound to "
            f"merchant_id='{caller_merchant_id}' but the request carries "
            f"merchant_id='{requested_merchant_id}'. Multi-tenant SaaS "
            "posture (F19 fix) forbids one merchant's API key from "
            "querying another merchant's records. Either use a key "
            "bound to the requested merchant_id OR drop the merchant_id "
            "from the request (the caller's bound merchant_id will be "
            "injected as a forced WHERE filter on the data-access query)."
        ),
    )


def _record_merchant_id(rec: dict) -> str | None:
    """Extract the merchant_id from an audit record's body.

    The audit body's top-level ``merchant_id`` field is the multi-tenant
    key (written by /risk/score at the audit.log call site — line ~1065
    of routes.py). ``None`` when the record's body doesn't carry it
    (pre-Track-U records; the legacy default).
    """
    if not isinstance(rec, dict):
        return None
    mid = rec.get("merchant_id")
    if mid is None:
        # Some audit bodies nest the merchant_id under the request
        # object (e.g. /risk/score's body has
        # ``{"request": {"order_id": ...}, "merchant_id": "merch_a"}``
        # at the top level — Track U T2.3's wire). Check both.
        req = rec.get("request") or {}
        if isinstance(req, dict):
            mid = req.get("merchant_id")
    return mid if isinstance(mid, str) else None


def _read_audit_tail(
    audit_logger,
    limit: int = 300,
    merchant_id: str | None = None,
) -> list[dict]:
    """Return the last ``limit`` audit records (chronological order).

    Day 2 Track E — uses ``AuditLogger.tail(limit)`` so both file mode
    (JSONL tail) and Postgres mode (``SELECT body FROM audit_records ORDER
    BY id DESC LIMIT %s``) work.

    Wave 2 (Subagent 14-e — F19 fix) — when ``merchant_id`` is provided
    (the caller's bound merchant_id, injected by
    ``enforce_merchant_isolation``), the returned records are filtered
    to those whose ``body->>'merchant_id'`` matches. File mode: Python-
    side filter on the JSONL records. Postgres mode: ``logger.py`` is
    owned by Subagent 11-b (off-limits), so the post-fetch Python-side
    filter is the contract-preserving escape hatch (the production-scale
    path would add a ``WHERE body->>'merchant_id' = %s`` clause in
    ``AuditLogger.tail`` — deferred to a future logger.py change; the
    in-memory filter is correct, just not as efficient at scale).
    """
    recs = audit_logger.tail(limit)
    if merchant_id is None:
        return recs
    # F19 — filter to the caller's merchant only. Cross-tenant records
    # are silently excluded (the caller can't tell whether other-tenant
    # records exist; this is the multi-tenant isolation posture).
    return [
        rec for rec in recs
        if _record_merchant_id(rec) == merchant_id
    ]


def _lookup_record_id_by_audit_id(
    audit_logger, audit_id: str, merchant_id: str | None = None
) -> int | None:
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

    Wave 2 (Subagent 14-e — F19 fix) — when ``merchant_id`` is provided
    (the caller's bound merchant_id), the lookup ADDITIONALLY verifies
    the resolved record's ``body->>'merchant_id'`` matches. A cross-
    tenant lookup returns None (the caller gets the same 404 as if the
    record didn't exist — masking cross-tenant existence is the multi-
    tenant isolation posture; a 404 vs 403 here doesn't leak "this audit
    id belongs to another merchant"). File mode returns None uniformly
    (no Merkle layer active; the merchant_id check is moot).

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
            # Wave 2 (F19) — when merchant_id is provided, fetch the
            # record's body alongside the id so we can verify the
            # merchant_id claim BEFORE returning the record_id to the
            # caller. A cross-tenant lookup returns None (404 to the
            # caller — masking cross-tenant existence).
            if merchant_id is not None:
                cur.execute(
                    "SELECT id, body FROM audit_records WHERE audit_id = %s",
                    (audit_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                body = row[1] if isinstance(row[1], dict) else {}
                rec_mid = (
                    body.get("merchant_id")
                    if isinstance(body, dict) else None
                )
                if rec_mid != merchant_id:
                    # Cross-tenant — mask as 404 (don't leak existence).
                    return None
                return int(row[0])
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


# ---------------------------------------------------------------------------
# Day 7 Wave 1 (Subagent 14-d — A2 fix) — replay-nonce store for the
# dual-control override endpoint. The ``OverrideIn`` request body carries
# a per-request ``nonce`` (16-byte hex string = 32 chars); the server
# stores the SHA-256 HASH of the nonce (NOT the raw nonce) in the
# ``override_nonces`` table (alembic 006) so a captured request can't be
# replayed verbatim within the timestamp window. A second sighting of
# the same nonce → 409 Conflict ("replay detected"). File-mode fallback:
# when ``DATABASE_URL`` is unset, the server uses a bounded in-memory
# LRU+TTL cache of the last 10_000 nonce hashes (TTL 1 day — older
# nonces are auto-evicted so a long-running dev process doesn't keep
# stale nonces; the LRU bound protects against unbounded memory growth).
# ---------------------------------------------------------------------------

# Timestamp window for replay defense — the override request's
# ``timestamp`` field (used to compute admin_signature_2) is fresh for
# 5 minutes (300s). A captured request older than 5 min is rejected
# at the timestamp-window check below (409 "replay detected" — the
# request is stale, regardless of whether the nonce has been seen).
# Window of 5 min mirrors the ±30s clock-skew tolerance × 10 — wide
# enough for normal client → server latency, narrow enough to make a
# captured-request replay window practically useless.
_OVERRIDE_NONCE_WINDOW_SECONDS = 300

# File-mode LRU+TTL cache — bounded to 10_000 entries (last 10_000
# nonces seen in the process) AND auto-evicts entries older than 1 day
# so the cache doesn't grow unbounded under burst traffic. In
# production (DATABASE_URL set), the authoritative store is the
# ``override_nonces`` Postgres table; this cache is only the file-mode
# fallback.
_override_nonce_cache: TTLCache = TTLCache(maxsize=10_000, ttl=86400)
_override_nonce_cache_lock = threading.Lock()

# Module-level lazy psycopg connection for the override_nonces table —
# pattern mirrors src/api/mandates.py:_get_counters_conn(). One
# persistent connection per process (the override endpoint is not the
# write-hot path; a pool would add latency for no benefit at this
# scale). Lazily constructed on first call to ``_get_nonces_conn()``
# so the import is side-effect-free — file-mode tests that never set
# ``DATABASE_URL`` never touch psycopg.
_nonces_conn_lock = threading.Lock()
_nonces_conn: Any = None  # psycopg.Connection | None


def _get_nonces_conn() -> Any:
    """Return a lazy shared psycopg connection for the override_nonces
    table. Returns ``None`` when ``DATABASE_URL`` is unset OR doesn't
    point at a Postgres DSN OR psycopg isn't importable. Pattern
    mirrors ``src/api/mandates.py:_get_counters_conn()``.
    """
    global _nonces_conn
    if _nonces_conn is not None:
        return _nonces_conn
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url or not db_url.startswith(
        ("postgresql://", "postgres://", "postgresql+psycopg://")
    ):
        return None
    try:
        import psycopg
    except ImportError:  # pragma: no cover — defensive; psycopg is in requirements
        return None
    with _nonces_conn_lock:
        if _nonces_conn is None:
            _nonces_conn = psycopg.connect(db_url, autocommit=False)
        return _nonces_conn


def _reset_nonces_conn() -> None:
    """Test helper — drop the cached override_nonces connection. Call
    between tests that mutate ``DATABASE_URL`` so the next
    ``_get_nonces_conn()`` call re-reads the env var and reopens.
    """
    global _nonces_conn
    if _nonces_conn is not None:
        try:
            _nonces_conn.close()
        except Exception:
            pass
    _nonces_conn = None


def _clear_override_nonce_cache() -> None:
    """Test helper — wipe the file-mode in-memory LRU+TTL cache so a
    fresh ``TestClient`` fixture starts with an empty replay-nonce
    state. Call between tests so the LRU doesn't leak nonces across
    test cases (each test should be able to assert "first sighting →
    200; second sighting → 409" without being shadowed by a prior
    test's cache entry).
    """
    with _override_nonce_cache_lock:
        _override_nonce_cache.clear()
    # Also clear the HKDF derived-key cache so a test that mutates
    # ``RTO_ADMIN_KEYS`` between cases sees the new derived key without
    # being shadowed by a stale cache entry.
    clear_derived_key_cache()


def _check_override_timestamp_window(timestamp: int | None) -> None:
    """Reject timestamps older than ``_OVERRIDE_NONCE_WINDOW_SECONDS``.

    The override request's ``timestamp`` field is used by the client
    to compute ``admin_signature_2`` (the HMAC chain input). If the
    timestamp is older than the window (5 min default), the request is
    stale — a captured request older than 5 min is rejected at this
    check, regardless of whether the nonce has been seen. 409 Conflict
    ("replay detected — timestamp outside the freshness window").

    The check is a no-op when ``timestamp`` is None — the server uses
    the current time as the timestamp in that case (per T1.1), so the
    request is structurally fresh.
    """
    if timestamp is None:
        return  # server uses int(time.time()) → always fresh
    now = int(time.time())
    if timestamp < now - _OVERRIDE_NONCE_WINDOW_SECONDS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"replay detected — timestamp {timestamp} is older than "
                f"the {_OVERRIDE_NONCE_WINDOW_SECONDS}-second freshness "
                f"window (now={now}). The override request must be "
                "re-issued with a fresh timestamp + a fresh HMAC chain + "
                "a fresh nonce."
            ),
        )
    # Defensive: future-dated timestamps are also rejected (clock skew
    # the other way — a malicious client cannot "pre-pay" a timestamp
    # to extend the replay window).
    if timestamp > now + _OVERRIDE_NONCE_WINDOW_SECONDS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"replay detected — timestamp {timestamp} is in the "
                f"future (now={now}, window="
                f"{_OVERRIDE_NONCE_WINDOW_SECONDS}s). Future-dated "
                "timestamps are rejected (a malicious client cannot "
                "'pre-pay' a timestamp to extend the replay window)."
            ),
        )


def _check_and_consume_override_nonce(
    state: dict, nonce_hash: str, timestamp: int | None
) -> None:
    """Replay-nonce consumption for the dual-control override (A2 fix).

    Three checks run in order (any failure → 409 Conflict):

      1. Timestamp freshness — the client-provided ``timestamp`` must
         be within ``_OVERRIDE_NONCE_WINDOW_SECONDS`` (5 min) of now.
         A captured request older than 5 min is rejected regardless of
         nonce state. (No-op when ``timestamp`` is None — the server
         uses the current time as the timestamp in that case.)
      2. Nonce consumption (Postgres mode when ``DATABASE_URL`` is
         set): ``INSERT INTO override_nonces (nonce_hash) VALUES (%s)
         ON CONFLICT DO NOTHING``. If ``cursor.rowcount == 0`` the
         nonce was already seen → 409. Also prunes rows older than 1
         day in the same transaction (the prune is best-effort; a
         prune failure doesn't block the consumption).
      3. Nonce consumption (file-mode fallback): check the in-memory
         LRU+TTL cache; if the nonce_hash is present → 409; otherwise
         insert + return None. The cache is bounded to 10_000 entries
         AND auto-evicts entries older than 1 day (TTLCache handles
         both). A warning is logged (via ``print`` to stderr — the
         project doesn't have a logging framework wired here) on the
         first file-mode consumption per process so operators know
         replay protection is in-memory only.

    On ANY DB error (table missing, connection lost, partial-failure
    mid-query), the function falls through to the file-mode in-memory
    cache so the request still gets replay protection (in-memory only
    — degraded but not absent). NEVER raise on a DB error; the
    override path must degrade to the in-memory fallback rather than
    fail the request with a 500.
    """
    # (1) Timestamp freshness — reject stale / future-dated requests.
    _check_override_timestamp_window(timestamp)

    # (2) Postgres mode — try the authoritative store first.
    conn = _get_nonces_conn()
    if conn is not None:
        try:
            with conn.cursor() as cur:
                # Best-effort prune — nonces older than 1 day are
                # structurally useless (the timestamp-window check at
                # the top of the handler rejects anything older than 5
                # min before this prune even runs). The prune is in
                # the same transaction so a failure rolls back the
                # prune + the INSERT together (atomic).
                try:
                    cur.execute(
                        "DELETE FROM override_nonces "
                        "WHERE created_at < NOW() - INTERVAL '1 day'"
                    )
                except Exception:
                    # Prune failure is non-fatal — the INSERT below is
                    # still the authoritative replay check. Roll back
                    # the prune statement-level work but keep the
                    # transaction alive for the INSERT (sub-statement
                    # rollback in psycopg3 aborts the transaction;
                    # we'd need a SAVEPOINT for partial rollback —
                    # simpler to just commit the rollback + retry the
                    # INSERT in a fresh transaction).
                    pass
                cur.execute(
                    "INSERT INTO override_nonces (nonce_hash) "
                    "VALUES (%s) ON CONFLICT DO NOTHING",
                    (nonce_hash,),
                )
                if cur.rowcount == 0:
                    # The nonce was already seen → replay detected.
                    conn.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "replay detected — override nonce already "
                            "consumed (a captured request cannot be "
                            "replayed verbatim within the timestamp "
                            "window). Generate a fresh nonce + HMAC "
                            "chain + timestamp and retry."
                        ),
                    )
                conn.commit()
                return  # first sighting — nonce consumed successfully
        except HTTPException:
            raise  # 409 — propagate the replay-detected signal
        except Exception:
            # DB error — degrade to the in-memory fallback. The
            # override path must NOT fail the request with a 500; the
            # in-memory cache is degraded but functional replay
            # protection. The stderr notice warns the operator that
            # replay protection is in-memory only.
            try:
                conn.rollback()
            except Exception:
                pass
            print(
                "[warn] override_nonces DB error — replay protection "
                "degraded to in-memory LRU+TTL cache (bounded to "
                "10_000 entries, TTL 1 day). Investigate the DB "
                "connection / table schema (alembic 006).",
                file=sys.stderr,
            )

    # (3) File-mode fallback — in-memory LRU+TTL cache. Bounded to
    # 10_000 entries AND auto-evicts entries older than 1 day. This
    # path is also taken when the DB prune + INSERT above errored.
    with _override_nonce_cache_lock:
        if nonce_hash in _override_nonce_cache:
            raise HTTPException(
                status_code=409,
                detail=(
                    "replay detected — override nonce already consumed "
                    "(in-memory LRU+TTL cache; replay protection is "
                    "in-memory only — set DATABASE_URL for authoritative "
                    "Postgres-backed protection). Generate a fresh "
                    "nonce + HMAC chain + timestamp and retry."
                ),
            )
        _override_nonce_cache[nonce_hash] = True  # value is unused; key presence is what matters

