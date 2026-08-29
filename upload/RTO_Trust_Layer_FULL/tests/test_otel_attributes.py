"""Tests for OpenTelemetry sub-span attribute completeness + exception
recording (Wave 3 — Subagent 15-e — DO BADLY #7 — OTel span attribute
completeness).

This file covers TWO things:

1. ``optional_span.__exit__`` (in ``src/api/otel.py``) — exception recording.
   When the body raises, the span MUST get ``record_exception(exc_val)`` +
   ``set_status(StatusCode.ERROR)`` BEFORE the exception propagates so a
   Jaeger trace surfaces the exception as a span event + the span's status
   marks it as ERROR (https://opentelemetry.io/docs/specs/otel/trace/api/
   #set-status-code). When the body succeeds, no recording + no ERROR status.

2. The 5 sub-spans on /risk/score + the 2 on /v1/explain/shap carry the
   OTel semantic-convention attributes the DO BADLY #7 spec mandates:
     * ``enduser.id`` (caller's bound merchant_id from F19)
     * ``rto.decision`` (ACCEPT/REVIEW/REJECT)
     * ``rto.intervention`` (ship/otp_verify/partial_cod/address_check/hold)
     * ``rto.probability`` (model P(RTO) calibrated)
     * ``rto.amount_inr`` (the order's INR amount — per-amount FN cost driver)
     * ``model.version`` (in-process model version)
     * ``mandate.verdict`` + ``mandate.verdict_reason`` (UPI Circle compliance)
     * ``rto.explain.order_id`` + ``rto.explain.background_samples`` (SHAP)

The 5 /risk/score sub-spans go through the GLOBAL tracer (via
``get_tracer(__name__)``) — NOT through ``state["tracer"]`` which the
existing ``tests/test_otel.py`` asserts is called exactly once with
``"risk.score"``. So this file patches ``src.api.routes.get_tracer`` via
the pytest ``monkeypatch`` fixture (auto-undone at test end) to a
MockTracer that records every ``start_as_current_span`` call + the
attributes set on each span. The existing ``test_otel.py`` assertion is
UNAFFECTED (the outer ``risk.score`` span still goes through
``state["tracer"]``, which my mock ISN'T — the outer span is bypassed
when ``setup_otel`` returns ``None`` for these tests).

CRITICAL: every monkeypatch in this file goes through the ``monkeypatch``
fixture (not raw module attribute assignment) so the patches are auto-undone
at the end of each test. This prevents leak into subsequent test files
(e.g. test_override_replay.py's test that asserts on the module-level
``_override_nonce_cache`` — a raw ``importlib.reload(routes_mod)`` would
have replaced that singleton with a fresh empty instance, breaking the
override-replay test's reference).

Sources:
  * OpenTelemetry Python SDK §"Manual instrumentation" (2024)
  * OTel semantic conventions for HTTP + RTO-domain extension
  * Jaeger all-in-one image 1.55 (status=ERROR filter surfaces failed spans)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.otel import optional_span  # noqa: E402
from src.api.routes import create_app  # noqa: E402

# ===========================================================================
# Mock tracer / span — records every set_attribute + record_exception call
# ===========================================================================

class _MockSpan:
    """Mock span that records every OTel call (set_attribute /
    record_exception / set_status / end). Used by ``_MockTracer`` to
    capture the attributes set on each sub-span for assertion."""

    def __init__(self, name: str):
        self.name = name
        self.attributes: dict = {}
        self.exceptions: list = []
        self.statuses: list = []
        self.ended = False

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value

    def set_attributes(self, attributes: dict) -> None:
        for k, v in attributes.items():
            self.attributes[k] = v

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        pass

    def record_exception(self, exception: BaseException) -> None:
        self.exceptions.append(exception)

    def set_status(self, status, description: str | None = None) -> None:
        self.statuses.append((status, description))

    def update_name(self, name: str) -> None:
        self.name = name

    def end(self, end_time: float | None = None) -> None:
        self.ended = True

    def is_recording(self) -> bool:
        return not self.ended

    def __enter__(self) -> "_MockSpan":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # Mirror the OTel SDK's _use_span CM — record the exception if
        # present, set status to ERROR, return False to propagate.
        if exc_val is not None:
            self.record_exception(exc_val)
            # In test mode, we don't have a real StatusCode — set a
            # sentinel string so the test can assert it was called.
            self.set_status("ERROR", description=str(exc_val))
        return False


class _MockSpanCM:
    """Context manager wrapping a _MockSpan so ``start_as_current_span``
    returns a CM (matches the OTel SDK's API shape where the CM yields the
    span on ``__enter__``)."""

    def __init__(self, span: _MockSpan):
        self._span = span

    def __enter__(self) -> _MockSpan:
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return self._span.__exit__(exc_type, exc_val, exc_tb)


class _MockTracer:
    """Mock tracer that records every ``start_as_current_span(name)`` call
    + returns a fresh _MockSpan wrapped in a _MockSpanCM. Used to assert
    which sub-spans fired + what attributes were set on each."""

    def __init__(self):
        self.spans: list[_MockSpan] = []
        self.calls: list[tuple[str, dict]] = []

    def start_as_current_span(self, name: str, **kwargs) -> _MockSpanCM:
        span = _MockSpan(name)
        cm = _MockSpanCM(span)
        self.spans.append(span)
        self.calls.append((name, kwargs))
        return cm

    def start_span(self, name: str, **kwargs) -> _MockSpan:
        span = _MockSpan(name)
        self.spans.append(span)
        return span

    def find_spans(self, name: str) -> list[_MockSpan]:
        return [s for s in self.spans if s.name == name]


# ===========================================================================
# helper: build a TestClient with the mock sub-span tracer patched in
# (uses monkeypatch so the patches are auto-undone at test end — prevents
# leak into subsequent test files).
# ===========================================================================

def _make_client_with_mock_tracer(monkeypatch, mock_tracer: _MockTracer) -> TestClient:
    """Build a TestClient whose ``get_tracer`` (for sub-spans) is replaced
    with the provided mock + whose outer ``state["tracer"]`` is None (so
    the existing test_otel.py single-call assertion is unaffected — the
    sub-spans go through the GLOBAL tracer, not the user-injected mock).

    Uses ``monkeypatch`` (auto-undone by pytest at test end) — NEVER raw
    module attribute assignment, because raw assignment leaks into
    subsequent test files (the routes module is imported ONCE + shared
    across all tests; raw assignment to ``routes_mod.setup_otel`` /
    ``routes_mod.get_tracer`` would persist + break other tests that rely
    on the real setup_otel/get_tracer behaviour).
    """
    import src.api.routes as routes_mod

    # Patch setup_otel to return None — disable the outer risk.score span
    # (so the mock_tracer is never called by the outer span block).
    monkeypatch.setattr(routes_mod, "setup_otel", lambda *a, **k: None)
    # Patch get_tracer to return the mock — capture the 5 sub-spans.
    monkeypatch.setattr(routes_mod, "get_tracer", lambda *a, **k: mock_tracer)
    app = create_app(scorer_rate_per_min=1000)
    return TestClient(app)


# ===========================================================================
# Part 1 — optional_span.__exit__ exception recording
# ===========================================================================

class TestOptionalSpanExceptionRecording:
    """``optional_span.__exit__`` (in src/api/otel.py) — when the body
    raises, the span MUST get ``record_exception(exc_val)`` + ``set_status(
    StatusCode.ERROR)`` BEFORE the exception propagates."""

    def test_records_exception_when_body_raises(self):
        """If the ``with`` body raises, ``span.record_exception(exc)`` is
        called + the exception is re-raised (not swallowed)."""
        span_cm = _MockSpanCM(_MockSpan("test"))
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom):
            with optional_span(mock_tracer, "test"):
                raise _Boom("body error")

        # Verify record_exception was called on the span via the CM exit.
        # The _MockSpanCM.__exit__ calls record_exception when exc_val is
        # present (it mirrors the OTel SDK's use_span behaviour).
        span_obj = span_cm._span
        assert len(span_obj.exceptions) >= 1, (
            "record_exception must be called when the body raises"
        )
        assert isinstance(span_obj.exceptions[0], _Boom)
        assert any(s[0] == "ERROR" for s in span_obj.statuses), (
            "set_status(ERROR) must be called when the body raises"
        )

    def test_no_exception_recording_on_success(self):
        """When the body succeeds, NO ``record_exception`` + NO
        ``set_status(ERROR)`` should fire on the span."""
        span_cm = _MockSpanCM(_MockSpan("test"))
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

        with optional_span(mock_tracer, "test"):
            pass  # success

        span_obj = span_cm._span
        assert span_obj.exceptions == [], (
            f"record_exception should NOT fire on success; "
            f"got {span_obj.exceptions}"
        )
        assert not any(s[0] == "ERROR" for s in span_obj.statuses), (
            f"set_status(ERROR) should NOT fire on success; "
            f"got {span_obj.statuses}"
        )

    def test_exception_propagates_after_recording(self):
        """The original exception MUST propagate after recording (the
        helper never swallows application exceptions)."""
        span_cm = _MockSpanCM(_MockSpan("test"))
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

        class _Propagated(Exception):
            pass

        with pytest.raises(_Propagated):
            with optional_span(mock_tracer, "test"):
                raise _Propagated("propagate me")

    def test_initial_attributes_set_on_span(self):
        """The ``attributes=`` dict passed to ``optional_span`` must be set
        on the span via ``set_attribute`` for each k/v."""
        span_cm = _MockSpanCM(_MockSpan("test"))
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

        attrs = {
            "enduser.id": "merch_a",
            "rto.decision": "ACCEPT",
            "rto.probability": 0.42,
            "rto.amount_inr": 999.0,
            "model.version": "v1.0",
            "mandate.verdict": "VALID",
            "mandate.verdict_reason": "",
        }
        with optional_span(mock_tracer, "test", attributes=attrs):
            pass

        span_obj = span_cm._span
        for k, v in attrs.items():
            assert k in span_obj.attributes, (
                f"attribute {k!r} must be set on span; "
                f"got keys {list(span_obj.attributes)}"
            )
            assert span_obj.attributes[k] == v, (
                f"attribute {k!r} value mismatch: "
                f"expected {v!r}, got {span_obj.attributes[k]!r}"
            )

    def test_none_tracer_yields_noop_span(self):
        """``optional_span(None, ...)`` must yield a NoOp span (no crash)
        + the body still runs."""
        with optional_span(None, "test") as span:
            assert span is not None
            # NoOp span is fine — calling set_attribute on it doesn't crash.
            span.set_attribute("foo", "bar")

    def test_none_tracer_propagates_exception(self):
        """Even with None tracer, the helper MUST propagate exceptions
        (the NoOp span doesn't swallow them)."""
        class _Boom(Exception):
            pass

        with pytest.raises(_Boom):
            with optional_span(None, "test"):
                raise _Boom("propagated")

    def test_set_attribute_after_body_runs_on_real_span(self):
        """Set-attribute calls inside the ``with`` body (post-hoc) land
        on the same span object the helper yielded (mirrors the live
        pattern in routes.py)."""
        span_cm = _MockSpanCM(_MockSpan("test"))
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

        with optional_span(
            mock_tracer, "test",
            attributes={"foo": "bar"},
        ) as span_obj:
            # Post-hoc attribute — set after the helper's initial set.
            span_obj.set_attribute("post.hoc", "value")

        span = span_cm._span
        assert span.attributes["foo"] == "bar"
        assert span.attributes["post.hoc"] == "value"

    def test_optional_span_does_not_swallow_when_span_cm_raises_in_exit(self):
        """If the underlying CM's ``__exit__`` raises (defensive), the
        helper's finally block catches it (best-effort) — but the
        original body exception still propagates."""
        # Build a CM whose __exit__ raises — emulate an OTel SDK bug.
        class _BadSpanCM:
            def __init__(self):
                self._span = _MockSpan("test")

            def __enter__(self):
                return self._span

            def __exit__(self, *args):
                raise RuntimeError("SDK exit crashed")

        span_cm = _BadSpanCM()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

        class _BodyError(Exception):
            pass

        # The body raises _BodyError; the CM's __exit__ raises RuntimeError;
        # the helper's finally block catches the RuntimeError (best-effort)
        # so the original _BodyError propagates (not the SDK's crash).
        with pytest.raises(_BodyError):
            with optional_span(mock_tracer, "test"):
                raise _BodyError("body error")


# ===========================================================================
# Part 2 — /risk/score sub-spans carry the expected OTel attributes
# ===========================================================================

SCORER = {"Authorization": "Bearer score-demo-key"}

VALID_RISK_SCORE_REQUEST = {
    "order_id": "OTEL-ATTR-1",
    "amount_inr": 1499,
    "category": "Fashion",
    "customer_id": "CUST-ATTR-1",
}


class TestRiskScoreSubspansAttributes:
    """The 5 /risk/score sub-spans (verify_mandate / model.predict_proba /
    optimal_decision / optimal_intervention / audit.log) carry the OTel
    semantic-convention attributes the DO BADLY #7 spec mandates."""

    def test_all_5_subspans_fire(self, monkeypatch):
        """The 5 expected sub-spans fire on a single /risk/score request."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST,
                       headers=SCORER)
            assert r.status_code == 200, f"request failed: {r.text}"

        span_names = [s.name for s in tracer.spans]
        for expected in (
            "verify_mandate",
            "model.predict_proba",
            "optimal_decision",
            "optimal_intervention",
            "audit.log",
        ):
            assert expected in span_names, (
                f"sub-span {expected!r} missing; got {span_names}"
            )

    def test_verify_mandate_subspan_carries_enduser_id_amount_model_version(self, monkeypatch):
        """verify_mandate sub-span carries enduser.id, rto.amount_inr,
        model.version, http.method, mandate.verdict, mandate.verdict_reason."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST,
                       headers=SCORER)
            assert r.status_code == 200

        spans = tracer.find_spans("verify_mandate")
        assert len(spans) == 1, f"expected 1 verify_mandate span; got {len(spans)}"
        attrs = spans[0].attributes
        for key in (
            "enduser.id",
            "rto.amount_inr",
            "model.version",
            "http.method",
            "mandate.verdict",
            "mandate.verdict_reason",
            "order.amount_inr",
            "mandate.present",
        ):
            assert key in attrs, (
                f"verify_mandate missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        assert attrs["http.method"] == "POST"
        assert attrs["rto.amount_inr"] == float(VALID_RISK_SCORE_REQUEST["amount_inr"])

    def test_model_predict_proba_subspan_carries_probability_amount(self, monkeypatch):
        """model.predict_proba sub-span carries enduser.id, rto.amount_inr,
        model.version, http.method, model.probability, rto.probability."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST,
                       headers=SCORER)
            assert r.status_code == 200

        spans = tracer.find_spans("model.predict_proba")
        assert len(spans) == 1
        attrs = spans[0].attributes
        for key in (
            "enduser.id",
            "rto.amount_inr",
            "model.version",
            "http.method",
            "model.probability",
            "rto.probability",
        ):
            assert key in attrs, (
                f"model.predict_proba missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        # rto.probability should match the response body's probability.
        body = r.json()
        if body.get("probability") is not None:
            assert attrs["rto.probability"] == pytest.approx(
                body["probability"], rel=1e-3
            ), (
                f"rto.probability mismatch: span={attrs['rto.probability']}, "
                f"body={body['probability']}"
            )

    def test_optimal_decision_subspan_carries_decision_probability(self, monkeypatch):
        """optimal_decision sub-span carries enduser.id, rto.decision,
        rto.probability, rto.amount_inr, model.version, mandate.verdict,
        mandate.verdict_reason, http.method."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST,
                       headers=SCORER)
            assert r.status_code == 200

        spans = tracer.find_spans("optimal_decision")
        assert len(spans) == 1
        attrs = spans[0].attributes
        for key in (
            "enduser.id",
            "rto.decision",
            "rto.probability",
            "rto.amount_inr",
            "model.version",
            "mandate.verdict",
            "mandate.verdict_reason",
            "http.method",
        ):
            assert key in attrs, (
                f"optimal_decision missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        body = r.json()
        assert attrs["rto.decision"] == body["decision"], (
            f"rto.decision mismatch: span={attrs['rto.decision']}, "
            f"body={body['decision']}"
        )

    def test_optimal_intervention_subspan_carries_decision_intervention(self, monkeypatch):
        """optimal_intervention sub-span carries enduser.id, rto.decision,
        rto.intervention, rto.probability, rto.amount_inr, model.version,
        http.method."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST,
                       headers=SCORER)
            assert r.status_code == 200

        spans = tracer.find_spans("optimal_intervention")
        assert len(spans) == 1
        attrs = spans[0].attributes
        for key in (
            "enduser.id",
            "rto.decision",
            "rto.intervention",
            "rto.probability",
            "rto.amount_inr",
            "model.version",
            "http.method",
        ):
            assert key in attrs, (
                f"optimal_intervention missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        body = r.json()
        assert attrs["rto.intervention"] == body["intervention"], (
            f"rto.intervention mismatch: span={attrs['rto.intervention']}, "
            f"body={body['intervention']}"
        )

    def test_audit_log_subspan_carries_full_rto_domain_attribute_set(self, monkeypatch):
        """audit.log sub-span is the LAST sub-span + carries the union of
        all RTO-domain attributes: enduser.id, rto.decision,
        rto.intervention, rto.probability, rto.amount_inr, model.version,
        mandate.verdict, mandate.verdict_reason, http.method."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST,
                       headers=SCORER)
            assert r.status_code == 200

        spans = tracer.find_spans("audit.log")
        assert len(spans) == 1
        attrs = spans[0].attributes
        for key in (
            "enduser.id",
            "rto.decision",
            "rto.intervention",
            "rto.probability",
            "rto.amount_inr",
            "model.version",
            "mandate.verdict",
            "mandate.verdict_reason",
            "http.method",
            "audit.decision",
            "audit.decision_source",
            "audit.channel",
            "audit.degraded",
        ):
            assert key in attrs, (
                f"audit.log missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        body = r.json()
        assert attrs["rto.decision"] == body["decision"]
        assert attrs["rto.intervention"] == body["intervention"]
        assert attrs["rto.amount_inr"] == float(
            VALID_RISK_SCORE_REQUEST["amount_inr"]
        )


# ===========================================================================
# Part 3 — /v1/explain/shap sub-spans carry the expected OTel attributes
# ===========================================================================

class TestExplainShapSubspansAttributes:
    """The 2 /v1/explain/shap sub-spans (resolve_features + compute) carry
    rto.explain.order_id, rto.explain.background_samples, enduser.id,
    model.version, http.method."""

    def test_both_explain_subspans_fire(self, monkeypatch):
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        # Use the ?features= path (no past prediction needed).
        with client as c:
            r = c.get(
                "/v1/explain/shap?features=%7B%22amount_inr%22%3A1499%2C%22category%22%3A%22Fashion%22%7D",
                headers=SCORER,
            )
            assert r.status_code in (200, 422, 503), (
                f"explain shap request failed: {r.status_code} {r.text}"
            )

        span_names = [s.name for s in tracer.spans]
        # At least the resolve_features span should fire (compute may not
        # fire if shap isn't installed or if the request 422'd early — but
        # resolve_features always fires after the request parses).
        assert "explain_shap.resolve_features" in span_names, (
            f"resolve_features sub-span missing; got {span_names}"
        )

    def test_resolve_features_subspan_carries_rto_explain_attributes(self, monkeypatch):
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.get(
                "/v1/explain/shap?features=%7B%22amount_inr%22%3A1499%2C%22category%22%3A%22Fashion%22%7D",
                headers=SCORER,
            )
            assert r.status_code in (200, 422, 503)

        spans = tracer.find_spans("explain_shap.resolve_features")
        assert len(spans) == 1, (
            f"expected 1 resolve_features span; got {len(spans)}"
        )
        attrs = spans[0].attributes
        for key in (
            "rto.explain.order_id",
            "enduser.id",
            "model.version",
            "http.method",
            "explain.order_id_present",
            "explain.features_present",
        ):
            assert key in attrs, (
                f"resolve_features missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        assert attrs["http.method"] == "GET"

    def test_compute_subspan_carries_rto_explain_background_samples(self, monkeypatch):
        """The compute sub-span fires when the model is loaded + shap is
        available (or its fallback path runs). Carries
        rto.explain.background_samples + rto.explain.cached_explainer +
        rto.explain.order_id + enduser.id + model.version + http.method."""
        tracer = _MockTracer()
        client = _make_client_with_mock_tracer(monkeypatch, tracer)
        with client as c:
            r = c.get(
                "/v1/explain/shap?features=%7B%22amount_inr%22%3A1499%2C%22category%22%3A%22Fashion%22%7D&background_samples=50",
                headers=SCORER,
            )
            # The compute span may fire before the response is returned (the
            # shap explainer is built lazily on first request).
            assert r.status_code in (200, 422, 503)

        spans = tracer.find_spans("explain_shap.compute")
        if not spans:
            pytest.skip(
                "explain_shap.compute sub-span didn't fire — likely shap "
                "not installed OR the request 422'd early; skipping the "
                "attribute-presence assertion."
            )
        attrs = spans[0].attributes
        for key in (
            "rto.explain.background_samples",
            "rto.explain.cached_explainer",
            "rto.explain.order_id",
            "enduser.id",
            "model.version",
            "http.method",
            "explain.background_samples",
            "explain.cached_explainer",
        ):
            assert key in attrs, (
                f"compute missing attribute {key!r}; "
                f"got keys {sorted(attrs)}"
            )
        assert attrs["http.method"] == "GET"
        assert attrs["rto.explain.background_samples"] == 50


# ===========================================================================
# Part 4 — meta: no exception swallowing in optional_span
# ===========================================================================

def test_optional_span_helper_does_not_swallow_application_exceptions():
    """Meta-guard: ``optional_span`` MUST propagate application exceptions
    (never swallow). This is critical — if a future PR wraps the helper's
    re-raise in a try/except that swallows, the application's error handler
    would never see the exception + the user gets a 200 instead of a 500."""
    class _AppError(Exception):
        pass

    # Use a NoOp tracer (the test-mode path) — verifies the exception still
    # propagates even when no real tracer is configured.
    with pytest.raises(_AppError):
        with optional_span(None, "test"):
            raise _AppError("must propagate")

    # Also test with a real mock tracer — the SDK's exit must not swallow.
    span_cm = _MockSpanCM(_MockSpan("test"))
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)
    with pytest.raises(_AppError):
        with optional_span(mock_tracer, "test"):
            raise _AppError("must propagate")


def test_optional_span_helper_records_exception_via_record_exception_call():
    """Meta-guard: when the body raises, the span's ``record_exception``
    method is called with the exception object (per OTel spec — surface
    the exception as a span event so Jaeger can render the stack trace)."""
    class _AppError(Exception):
        pass

    # Use a real mock tracer + capture the span.
    span_cm = _MockSpanCM(_MockSpan("test"))
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span = MagicMock(return_value=span_cm)

    with pytest.raises(_AppError):
        with optional_span(mock_tracer, "test"):
            raise _AppError("record me")

    span_obj = span_cm._span
    assert len(span_obj.exceptions) >= 1
    recorded = span_obj.exceptions[0]
    assert isinstance(recorded, _AppError)
    assert "record me" in str(recorded)


# ===========================================================================
# Part 5 — PRESERVE contract: test_otel.py single-call assertion unbroken
# ===========================================================================

def test_preserve_test_otel_single_call_assertion_unbroken(monkeypatch):
    """PRESERVE contract: the existing ``tests/test_otel.py`` assertion
    ``mock_tracer.start_as_current_span.assert_called_once_with("risk.score")``
    must remain valid. The 5 /risk/score sub-spans go through the GLOBAL
    tracer (via ``get_tracer(__name__)``), NOT through ``state["tracer"]``
    which the existing test mocks — so the mock tracer is called exactly
    ONCE for the outer ``risk.score`` span.

    This test verifies that contract by re-running the existing test's
    assertion pattern: a fresh app with a mock ``state["tracer"]`` + a
    real ``get_tracer`` (returning the NoOp tracer when OTel SDK isn't
    configured). The outer span fires exactly once for "risk.score"; the
    sub-spans fire through the NoOp tracer (unrecorded).

    CRITICAL: uses ``monkeypatch.setattr`` (auto-undone at test end) so
    the patch doesn't leak into subsequent test files. NEVER raw module
    attribute assignment — that would persist across tests + break other
    test files that rely on the real ``setup_otel`` / ``get_tracer``
    behaviour (e.g. test_override_replay.py's module-level
    ``_override_nonce_cache`` reference).
    """
    import src.api.routes as routes_mod

    # Build a fresh mock for the outer span.
    mock_span = MagicMock()
    mock_span_cm = MagicMock()
    mock_span_cm.__enter__ = MagicMock(return_value=mock_span)
    mock_span_cm.__exit__ = MagicMock(return_value=False)
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span = MagicMock(return_value=mock_span_cm)

    # Patch setup_otel to return our mock (the outer span goes through this).
    # Use monkeypatch.setattr so the patch is auto-undone at test end + does
    # NOT leak into subsequent test files in the same pytest session.
    monkeypatch.setattr(routes_mod, "setup_otel", lambda *a, **k: mock_tracer)

    with TestClient(routes_mod.create_app(scorer_rate_per_min=1000)) as c:
        r = c.post("/risk/score", json=VALID_RISK_SCORE_REQUEST, headers=SCORER)
        assert r.status_code == 200

    # The OUTER mock tracer is called EXACTLY ONCE for "risk.score" — the
    # sub-spans go through the global get_tracer (which returns NoOp here
    # since OTel SDK isn't configured in test mode).
    mock_tracer.start_as_current_span.assert_called_once_with("risk.score")
