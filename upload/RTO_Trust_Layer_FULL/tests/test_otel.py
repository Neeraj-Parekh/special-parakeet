"""Tests for OpenTelemetry instrumentation (Track M Day 4).

Verifies the dual-mode contract:
  * ``setup_otel()`` returns ``None`` when ``OTEL_EXPORTER_OTLP_ENDPOINT``
    is unset (the test-mode path — the 93 existing tests + the new
    ingest tests run this way without a Jaeger fixture).
  * When the env var IS set + the OTel SDK IS installed, the /risk/score
    handler creates a span with the expected attributes (order_id,
    amount, decision, score, decision_source) by mocking the tracer.

The mock-tracer path is important because the buildathon sandbox
doesn't have Jaeger running + the opentelemetry-sdk package may not be
installed; the test must pass without requiring either.

Test count: 4 (all pass without a Jaeger fixture).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.otel import setup_otel  # noqa: E402
from src.api.routes import create_app  # noqa: E402

VALID = {
    "order_id": "OTEL-T1",
    "amount_inr": 1499,
    "category": "Fashion",
    "customer_id": "CUST-OTEL-1",
}
SCORER = {"Authorization": "Bearer score-demo-key"}


# ---------------------------------------------------------------------------
# Test 1 — dual-mode: setup_otel() returns None when env var is unset.
# ---------------------------------------------------------------------------


def test_setup_otel_returns_none_when_env_var_unset(monkeypatch):
    """setup_otel() must return None when OTEL_EXPORTER_OTLP_ENDPOINT is unset.

    This is the dual-mode contract that lets the 93 existing tests +
    the new ingest tests pass without a Jaeger fixture. Mirrors Track E's
    DATABASE_URL + Track F's REDIS_URL patterns.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    result = setup_otel()
    assert result is None, (
        "setup_otel() must return None when OTEL_EXPORTER_OTLP_ENDPOINT is unset — "
        "the dual-mode contract that lets existing tests pass without Jaeger"
    )


def test_setup_otel_returns_none_when_sdk_not_installed(monkeypatch):
    """setup_otel() returns None if the OTel SDK import fails.

    The function catches ImportError + prints a notice + returns None so
    the API doesn't crash at boot if the user sets OTEL_EXPORTER_OTLP_ENDPOINT
    but hasn't installed the opentelemetry-sdk package yet. This is the
    defensive path.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")

    # Patch the lazy import inside setup_otel to raise ImportError. The
    # import happens inside the function (after the env var check), so
    # we patch the ``__import__`` builtin via sys.modules manipulation.
    # Simpler: patch the opentelemetry modules to raise ImportError.
    import builtins

    orig_import = builtins.__import__

    def _fail_opentelemetry(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError(f"mocked: {name} not installed")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail_opentelemetry)
    # Also remove any cached opentelemetry modules from sys.modules so
    # the next import attempt triggers our patched __import__.
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    result = setup_otel()
    assert result is None, (
        "setup_otel() must return None when the OTel SDK import fails — "
        "the defensive path so the API doesn't crash at boot"
    )


# ---------------------------------------------------------------------------
# Test 2 — /risk/score handler creates a span when tracer is set.
# ---------------------------------------------------------------------------


def test_risk_score_handler_creates_span_with_attributes(monkeypatch):
    """When state["tracer"] is a mock, the /risk/score handler must:
    1. Call ``tracer.start_as_current_span("risk.score")`` once.
    2. Set the 5 required attributes (order_id, amount, decision, score,
       decision_source) on the span.
    3. Call ``span.end()`` (or exit the context manager) before returning.
    """
    # Build a mock tracer + span. The span is a MagicMock that records
    # every set_attribute call.
    mock_span = MagicMock()
    mock_span_cm = MagicMock()
    mock_span_cm.__enter__ = MagicMock(return_value=mock_span)
    mock_span_cm.__exit__ = MagicMock(return_value=False)

    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span = MagicMock(return_value=mock_span_cm)

    # Patch setup_otel to return our mock tracer (the env-var path is
    # bypassed — we control the tracer directly).
    monkeypatch.setattr("src.api.routes.setup_otel", lambda: mock_tracer)

    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.post("/risk/score", json=VALID, headers=SCORER)
        assert r.status_code == 200
        body = r.json()

    # Assert the tracer was called with the right span name.
    mock_tracer.start_as_current_span.assert_called_once_with("risk.score")

    # The span context was entered + exited.
    mock_span_cm.__enter__.assert_called_once()
    mock_span_cm.__exit__.assert_called_once()

    # Collect all set_attribute calls into a dict.
    attrs = {}
    for call in mock_span.set_attribute.call_args_list:
        args, _ = call
        if len(args) >= 2:
            attrs[args[0]] = args[1]

    # Assert the 5 required attributes per the task spec.
    assert "order_id" in attrs, "span must have order_id attribute"
    assert attrs["order_id"] == VALID["order_id"]
    assert "amount" in attrs, "span must have amount attribute"
    assert attrs["amount"] == float(VALID["amount_inr"])
    assert "decision" in attrs, "span must have decision attribute"
    assert attrs["decision"] == body["decision"]
    assert "score" in attrs, "span must have score attribute"
    # score = float(proba) or 0.0 — must match the body's probability
    # (rounded) or be 0 if degraded.
    if body.get("probability") is not None:
        assert attrs["score"] == pytest.approx(body["probability"], rel=1e-3)
    else:
        assert attrs["score"] == 0.0
    assert "decision_source" in attrs, "span must have decision_source attribute"
    assert attrs["decision_source"] == body["decision_source"]


def test_risk_score_handler_skips_span_when_tracer_is_none(monkeypatch):
    """When state["tracer"] is None (env var unset, test mode), the
    /risk/score handler must NOT call any span methods + still return 200.

    This is the dual-mode disabled path — verifies the inline
    ``if span is not None:`` gates work + the existing tests pass.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("src.api.routes.setup_otel", lambda: None)

    with TestClient(create_app(scorer_rate_per_min=1000)) as client:
        r = client.post("/risk/score", json=VALID, headers=SCORER)
        assert r.status_code == 200
        body = r.json()
        # The handler still returns a valid response — OTel is a no-op.
        assert "decision" in body
        assert "decision_source" in body


# ---------------------------------------------------------------------------
# Test 3 — channel tag flows into the audit record.
# ---------------------------------------------------------------------------


def test_channel_header_flows_into_audit_record(monkeypatch, tmp_path):
    """The X-Channel header from the multi-source ingest simulators
    must surface in the audit record's ``channel`` field so per-channel
    drift detection (TFX generate_data_statistics) can slice on it.

    Default is "ecommerce" when no header is sent — that's the existing
    merchant web-checkout path.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("src.api.routes.setup_otel", lambda: None)

    # Use a tmp_path audit so we don't pollute the real audit.jsonl.
    audit_path = str(tmp_path / "audit.jsonl")
    with TestClient(create_app(scorer_rate_per_min=1000, audit_path=audit_path)) as client:
        # Post with explicit X-Channel: mobile header.
        mobile_headers = {**SCORER, "X-Channel": "mobile"}
        r1 = client.post("/risk/score", json=VALID, headers=mobile_headers)
        assert r1.status_code == 200

        # Post with no X-Channel header — should default to "ecommerce".
        r2 = client.post("/risk/score", json={
            **VALID, "order_id": "OTEL-T2",
        }, headers=SCORER)
        assert r2.status_code == 200

    # Read the audit log + verify the channel field is recorded.
    from src.audit.logger import AuditLogger

    logger = AuditLogger(audit_path)
    tail = logger.tail(limit=10)
    assert len(tail) >= 2, "expected at least 2 audit records"
    # The most recent records should be the two we just posted (in order).
    channels = [r.get("channel") for r in tail]
    assert "mobile" in channels, f"expected 'mobile' in channels: {channels}"
    assert "ecommerce" in channels, f"expected 'ecommerce' in channels: {channels}"
