"""OpenTelemetry tracer setup for the RTO Trust Layer.

Day 2 Track E — Dual-mode (mirrors Track E's DATABASE_URL + Track F's REDIS_URL pattern):
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

Day 6 Track 12-bc — extended with two new helpers:
  * ``get_tracer(name)`` — returns a tracer from the global provider
    (NoOpTracer when OTel isn't installed). Used by routes.py to create
    custom sub-spans on the critical path (optimal_decision, audit.log,
    verify_mandate) WITHOUT polluting the outer ``state["tracer"]`` that
    the existing test_otel.py asserts is called exactly once with
    ``"risk.score"``. The sub-spans go through the global provider so a
    Jaeger trace surfaces the full call-tree.
  * ``instrument_app(app)`` — calls FastAPIInstrumentor.instrument_app +
    RequestsInstrumentor().instrument() + PsycopgInstrumentor().instrument()
    (all guarded by try/except ImportError so the API doesn't crash if
    the opentelemetry-instrumentation-* packages aren't installed). Auto-
    creates server spans for every HTTP request + db-query spans for every
    psycopg query.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


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


class _NoOpSpan:
    """Minimal stand-in for ``opentelemetry.trace.Span``.

    Used when the ``opentelemetry-api`` package isn't installed — provides
    the same surface (``set_attribute`` / ``record_exception`` / ``end`` /
    ``__enter__`` / ``__exit__``) so the caller can write
    ``with tracer.start_as_current_span("...") as span: ...`` without
    branching on whether OTel is installed. All methods are no-ops.
    """

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: dict) -> None:
        pass

    def add_event(self, name: str, attributes: dict | None = None) -> None:
        pass

    def record_exception(self, exception: BaseException) -> None:
        pass

    def set_status(self, status: Any, description: str | None = None) -> None:
        pass

    def update_name(self, name: str) -> None:
        pass

    def end(self, end_time: float | None = None) -> None:
        pass

    def is_recording(self) -> bool:
        return False

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False


class _NoOpTracer:
    """Minimal stand-in for ``opentelemetry.trace.Tracer``.

    Matches the surface used by routes.py: ``start_as_current_span(name)``
    returns a context manager that yields a ``_NoOpSpan``. The CM is the
    span itself (per the OTel SDK's pattern where the span IS the CM).
    """

    def start_as_current_span(self, name: str, **kwargs: Any) -> "_NoOpSpan":
        return _NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> "_NoOpSpan":
        return _NoOpSpan()


# Module-level NoOp tracer singleton — every call to get_tracer() when OTel
# isn't installed returns the same instance (avoids allocating a new one
# per request).
_NOOP_TRACER = _NoOpTracer()


def get_tracer(name: str) -> Any:
    """Return a tracer from the global OpenTelemetry provider.

    Used by routes.py to create custom sub-spans on the critical path
    (around ``optimal_decision`` / ``optimal_intervention`` /
    ``audit.log`` / ``verify_mandate``) WITHOUT touching the outer
    ``state["tracer"]`` that the existing test_otel.py asserts is called
    exactly once with ``"risk.score"``. The sub-spans go through the
    GLOBAL provider so a Jaeger trace surfaces the full call-tree under
    the parent ``risk.score`` span.

    Dual-mode (mirrors ``setup_otel()``):
      * If ``opentelemetry-api`` is installed → returns
        ``opentelemetry.trace.get_tracer(name)`` (the real tracer when
        ``setup_otel()`` has configured a provider; the NoOpTracer built
        into the OTel API when no provider is configured — test mode).
      * If ``opentelemetry-api`` is NOT installed → returns the local
        ``_NoOpTracer()`` singleton so the caller's
        ``with tracer.start_as_current_span("...") as span: ...`` is a
        no-op (no exception, no trace exported).

    Args:
        name: The instrumentation scope name. Per OTel convention, this
            is typically ``__name__`` of the module creating the spans
            (e.g. ``"src.api.routes"``). Surfaces in Jaeger's "Instrumentation
            Scope" filter.

    Returns:
        A tracer instance (real, OTel-API-noop, or local _NoOpTracer).
    """
    try:
        from opentelemetry import trace
    except ImportError:
        return _NOOP_TRACER
    try:
        return trace.get_tracer(name)
    except Exception:  # pragma: no cover — defensive
        return _NOOP_TRACER


# Wave 3 (Subagent 15-e — DO BADLY #7) — module-level lazy resolver for
# OpenTelemetry's ``StatusCode`` enum (used by ``optional_span`` below
# to mark spans as ERROR when an exception propagates through the body).
# Lazily imported (in the try/except ImportError gate) so the 93 tests
# that don't have the OTel SDK installed don't pay the import cost +
# don't crash. Returns a sentinel ``_UNSET`` object when unavailable
# so the caller can skip the ``set_status`` call gracefully (the
# NoOp span's ``set_status`` is already a no-op so this is belt-and-
# suspenders defense).
_UNSET = object()


def _resolve_status_code() -> Any:
    """Lazily resolve the OTel ``StatusCode`` enum.

    Returns ``opentelemetry.trace.status.StatusCode`` when the OTel API
    is installed; ``_UNSET`` sentinel otherwise (the caller skips the
    ``set_status`` call when the sentinel is returned).
    """
    try:
        from opentelemetry.trace.status import StatusCode
        return StatusCode
    except ImportError:
        return _UNSET


@contextmanager
def optional_span(
    tracer: Any,
    name: str,
    attributes: dict | None = None,
) -> Iterator[Any]:
    """Context manager that creates a span if tracer is non-None, else no-op.

    Convenience wrapper for the ``with tracer.start_as_current_span(...) as
    span: span.set_attribute(...)`` pattern that appears in routes.py's
    critical-path span annotations. Using this helper keeps the call-site
    concise:

        tracer = get_tracer(__name__)
        with optional_span(tracer, "optimal_decision",
                           attributes={"amount_inr": order.amount_inr}) as span:
            decision, costs = optimal_decision(...)

    Yields the span (real or NoOp) so the caller can call ``set_attribute``
    / ``record_exception`` on it conditionally.

    IMPORTANT: exceptions raised inside the ``with`` body ARE propagated —
    they're passed to the underlying span's ``__exit__`` so the OTel SDK
    can ``record_exception`` + set ``status=ERROR`` on the span, then
    re-raised to the caller. The helper never swallows application
    exceptions.

    Args:
        tracer: A tracer instance (real, OTel-API-noop, or local _NoOpTracer).
            None is also accepted (the function degrades to a no-op).
        name: The span name (e.g. ``"optimal_decision"``).
        attributes: Optional dict of initial span attributes set inside the
            ``with`` block (after ``__enter__``). Defensive — wrapped in
            try/except so a single bad attribute doesn't crash the request.
    """
    # Tracer-None fast path — yield a local NoOp + return. No CM machinery.
    if tracer is None:
        yield _NoOpSpan()
        return

    # Build the span CM. The OTel SDK's ``start_as_current_span`` returns a
    # context manager; the local _NoOpTracer returns a _NoOpSpan instance
    # which is itself a CM (via __enter__/__exit__).
    try:
        span_cm = tracer.start_as_current_span(name)
    except Exception:
        # Failed to even construct the CM (defensive — should never happen
        # with the NoOp path, but if the OTel SDK hits an internal error
        # we degrade gracefully). Yield a NoOp + return.
        yield _NoOpSpan()
        return

    # Enter the CM. If this raises, fall back to NoOp so the body runs.
    try:
        span_obj = span_cm.__enter__()
    except Exception:
        yield _NoOpSpan()
        return

    # Set initial attributes (defensive — best-effort).
    if attributes:
        for k, v in attributes.items():
            try:
                span_obj.set_attribute(k, v)
            except Exception:  # pragma: no cover — best-effort
                pass

    # Yield control to the body. If the body raises, capture the
    # exc_info + pass it to ``__exit__`` so the OTel SDK records the
    # exception on the span (via ``record_exception`` +
    # ``set_status(ERROR)``). Then re-raise so the application code's
    # exception handler runs.
    #
    # Wave 3 (Subagent 15-e — DO BADLY #7) — EXPLICITLY call
    # ``span.record_exception(exc_val)`` + ``span.set_status(
    # StatusCode.ERROR)`` on the span BEFORE delegating to the OTel
    # SDK's CM ``__exit__``. The OTel SDK's ``use_span`` CM __exit__
    # ALSO records the exception (so technically this is redundant when
    # the real SDK is wired), but the spec asks for explicit recording
    # so a Jaeger trace surfaces the exception as a span event even
    # when the SDK's auto-recording path is bypassed (e.g. when the
    # tracer is a ProxyTracer from opentelemetry-api with no provider
    # configured — the test-mode path; the ProxyTracer's span CM
    # doesn't auto-record, so the explicit call below is the ONLY
    # recording path in that case). The NoOp span's
    # ``record_exception`` + ``set_status`` are no-ops so this is safe
    # when the global tracer is NoOp too.
    exc_info: tuple = (None, None, None)
    _status_code = _resolve_status_code()
    try:
        yield span_obj
    except BaseException as e:
        exc_info = (type(e), e, e.__traceback__)
        # Explicit exception recording on the span (best-effort —
        # never let the recording itself raise + mask the original
        # exception).
        try:
            span_obj.record_exception(e)
        except Exception:  # pragma: no cover — best-effort
            pass
        # Mark the span as ERROR per the OTel status convention
        # (https://opentelemetry.io/docs/specs/otel/trace/api/
        # #set-status-code). ``StatusCode.ERROR`` is the only value
        # set here; ``StatusCode.OK`` is NOT set on the success path
        # because the OTel spec says marking a span OK is optional +
        # the absence of an explicit status defaults to UNSET (which
        # Jaeger renders as "unset" — the operator can filter
        # ``status=ERROR`` to find failed operations; the success path
        # doesn't need a positive mark).
        if _status_code is not _UNSET:
            try:
                span_obj.set_status(_status_code.ERROR)
            except Exception:  # pragma: no cover — best-effort
                pass
        raise
    finally:
        try:
            span_cm.__exit__(*exc_info)
        except Exception:  # pragma: no cover — best-effort
            pass


def instrument_app(app: Any) -> bool:
    """Auto-instrument a FastAPI app + outbound HTTP + psycopg.

    Calls (each guarded by try/except ImportError so the API doesn't crash
    if the corresponding instrumentation package isn't installed):
      * ``FastAPIInstrumentor.instrument_app(app)`` — auto-creates server
        spans for every HTTP request, with the route + method + status as
        span attributes. Replaces the manual ``state["tracer"]`` span for
        the OUTER request scope (but NOT the inner ``risk.score`` span —
        the manual one is preserved because test_otel.py asserts on it).
      * ``RequestsInstrumentor().instrument()`` — auto-creates client spans
        for every outbound ``requests.*`` call. The RTO Trust Layer makes
        outbound HTTP calls in src/stream/producer.py's lazy connect + the
        ingest simulators, so this wires their latency into the trace.
      * ``PsycopgInstrumentor().instrument()`` — auto-creates db spans for
        every psycopg query (the audit logger's INSERT, the idempotency
        cache's SELECT/INSERT, the mandate counter's read/write, the
        model-registry's INSERT/SELECT). These auto-spans show up as
        children of the manual ``audit.log`` / ``verify_mandate`` spans
        when those are present.

    Must be called AFTER all routes are registered on the app (i.e. at
    the end of ``create_app`` before ``return app``) — FastAPIInstrumentor
    hooks the route registry at instrumentation time.

    Returns ``True`` if all 3 instrumentations succeeded; ``False`` if any
    failed (ImportError or runtime error). The caller (routes.py) ignores
    the return value — instrumentation is best-effort; the manual spans
    + the OTel SDK's noop tracer cover the gap when instrumentation
    isn't installed.
    """
    success = True

    # FastAPIInstrumentor — auto-creates server spans for each HTTP request.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except ImportError:
        # opentelemetry-instrumentation-fastapi not installed. The manual
        # ``state["tracer"]`` span on /risk/score + the custom sub-spans
        # via get_tracer() still work; we just lose the outer request-
        # scope auto-span + the per-route span attributes.
        success = False
    except Exception as e:  # pragma: no cover — defensive
        # FastAPIInstrumentor can raise if a tracer provider isn't set OR
        # if the app's route table has unexpected shapes. Best-effort:
        # log + continue.
        import sys

        print(
            f"[otel] FastAPIInstrumentor.instrument_app failed: "
            f"{type(e).__name__}: {e} — request-level auto-spans disabled",
            file=sys.stderr,
        )
        success = False

    # RequestsInstrumentor — auto-creates client spans for outbound HTTP.
    # The RTO Trust Layer makes outbound HTTP calls in src/stream/producer.py
    # (lazy Redis connect is HTTP-ish in some configs) + the ingest
    # simulators post to the API.
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
    except ImportError:
        # opentelemetry-instrumentation-requests not installed.
        success = False
    except Exception as e:  # pragma: no cover — defensive
        import sys

        print(
            f"[otel] RequestsInstrumentor.instrument failed: "
            f"{type(e).__name__}: {e} — outbound HTTP auto-spans disabled",
            file=sys.stderr,
        )
        success = False

    # PsycopgInstrumentor — auto-creates db spans for every psycopg query.
    # The audit logger + idempotency cache + mandate counters + model
    # registry all use psycopg directly (no SQLAlchemy ORM per the
    # 04-TECH-STACK-DECISIONS spec).
    try:
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument(enable_commenter=True, commenter_options={})
    except ImportError:
        # Try the older psycopg2 instrumentation as a fallback (the spec
        # pins psycopg3, but a developer may have an older install).
        try:
            from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

            Psycopg2Instrumentor().instrument(enable_commenter=True, commenter_options={})
        except ImportError:
            # Neither psycopg nor psycopg2 instrumentation installed.
            success = False
    except Exception as e:  # pragma: no cover — defensive
        import sys

        print(
            f"[otel] PsycopgInstrumentor.instrument failed: "
            f"{type(e).__name__}: {e} — DB-query auto-spans disabled",
            file=sys.stderr,
        )
        success = False

    return success
