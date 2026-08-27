"""OpenTelemetry tracer setup for the RTO Trust Layer.

Day 4 Track M — closes the V2 §9.2 observability gap by wiring OTel into
the FastAPI /risk/score handler so the decision pipeline shows up as a
distributed trace in Jaeger (UI on :16686, OTLP gRPC ingestion on :4317).

Dual-mode (mirrors Track E's DATABASE_URL + Track F's REDIS_URL pattern):
  * If ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var is set → real TracerProvider
    with a BatchSpanProcessor + OTLPSpanExporter pushes spans to Jaeger.
  * If unset → return ``None`` so the handler can short-circuit
    (``state["tracer"] = setup_otel()`` becomes ``None``; the
    ``if state["tracer"] is not None`` gate around the span block
    skips the OTel calls). The 93 existing tests pass without a Jaeger
    fixture, same way they pass without Postgres + without Redis.

The trace span on /risk/score carries these attributes per the task spec:

  * ``order_id`` — the input order's id (string)
  * ``amount`` — the input order's amount in INR (float)
  * ``decision`` — ACCEPT | REVIEW | REJECT (string)
  * ``score`` — model P(RTO) (float, None if degraded)
  * ``decision_source`` — which layer chose the decision (string — one
    of ``rules_engine_block`` | ``mandate_breach`` | ``mandate_invalid``
    | ``mandate_review_required`` | ``degraded_review`` |
    ``cost_optimal_bmr`` | ``cost_optimal_bmr_review_rule``)

These attributes let a Jaeger query surface "all REJECT decisions where
the cost-optimizer drove the call" — useful for both explainability +
the post-incident review (Track D's V3 §13 mandate action-class
expansion surfaces the mandate verdict_reason on the same span).

Source: OpenTelemetry Python SDK docs §"Manual instrumentation" (2024)
+ Jaeger all-in-one image 1.55 (OTLP gRPC on :4317, UI on :16686).
"""
from __future__ import annotations

import os
from typing import Any


def setup_otel(
    service_name: str = "rto-trust-layer",
    otlp_endpoint: str = "http://jaeger:4317",
) -> Any | None:
    """Configure the global TracerProvider + return a tracer.

    Returns ``None`` if ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var is unset
    (dual-mode: tests + local dev without Jaeger run this path).

    Args:
        service_name: ``service.name`` resource attribute. Defaults to
            ``rto-trust-layer``; override via ``OTEL_SERVICE_NAME`` env var
            (the docker-compose service wires this for prod).
        otlp_endpoint: The OTLP gRPC endpoint. Defaults to
            ``http://jaeger:4317`` (the docker-compose Jaeger service).
            Override via the ``OTEL_EXPORTER_OTLP_ENDPOINT`` env var.

    Returns:
        A ``Trace`` instance (the result of ``trace.get_tracer()``) —
        the caller stores it in ``state["tracer"]`` + uses
        ``tracer.start_as_current_span("risk.score")`` in the
        ``/risk/score`` handler. ``None`` if OTel is disabled.
    """
    # Env var gate — dual-mode like Track E's DATABASE_URL + Track F's
    # REDIS_URL. If the user hasn't wired Jaeger (or the docker-compose
    # env block doesn't include OTEL_EXPORTER_OTLP_ENDPOINT), OTel is
    # disabled + the /risk/score handler short-circuits past the span
    # block. The 93 existing tests pass this way.
    env_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not env_endpoint:
        return None

    # Lazy import — so the 93 existing tests don't pay the opentelemetry
    # import cost + don't crash if the dep isn't installed (test_otel.py
    # uses a mock tracer; the OTel SDK is only imported when the env var
    # is set, i.e. in the docker-compose stack).
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # The OTel SDK isn't installed. Print a notice + fall back to
        # disabled mode so the API doesn't crash at boot — but this is
        # NOT a silent failure; the log line surfaces in the api container
        # stderr so the operator notices.
        import sys

        print(
            "[otel] OTEL_EXPORTER_OTLP_ENDPOINT is set but the opentelemetry-sdk "
            "package isn't installed — tracing disabled. Run "
            "`pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-exporter-otlp` to enable.",
            file=sys.stderr,
        )
        return None

    # Allow OTEL_SERVICE_NAME to override the default service_name arg.
    env_service = os.environ.get("OTEL_SERVICE_NAME")
    if env_service:
        service_name = env_service

    # Resource — the static attributes attached to every span. The
    # ``service.name`` is the top-level Jaeger filter; ``service.version``
    # lets the dashboard group spans by API version (Track L's retrain
    # flow ships new model versions — surfacing the version here lets the
    # operator diff traces between vN and vN+1 in Jaeger).
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "rto",
            "service.version": os.environ.get("RTO_MODEL_VERSION", "0.4.0"),
        }
    )

    # TracerProvider — owns the span queue + the export pipeline.
    provider = TracerProvider(resource=resource)
    # BatchSpanProcessor — batches spans to the exporter every ~5s by
    # default (configurable via OTEL_BSP_SCHEDULE_DELAY). Reduces the
    # per-span network round-trip vs. the simple span processor.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))

    # set_tracer_provider is global + idempotent — calling it twice
    # raises a warning. setup_otel() is called once from create_app() so
    # this is safe under hot-reload (uvicorn --reload reloads the module
    # but the FastAPI app factory re-runs create_app() → setup_otel()).
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


# ---------------------------------------------------------------------------
# Noop tracer — for tests + for the dual-mode path when OTel is disabled.
# Returning a real NoopTracer (instead of None) would let the handler skip
# the ``if state["tracer"] is not None`` gate entirely, but the spec said
# dual-mode like Track E so we keep None + the gate. The tests in
# tests/test_otel.py exercise both paths.
# ---------------------------------------------------------------------------
