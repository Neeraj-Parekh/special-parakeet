"""FastAPI APIRouter for the 4 multi-source ingest endpoints — Task 12-e.

Provides ``POST /v1/ingest/{source}`` for each of the 4 ingest sources
(ecommerce, mobile, callcenter, atm). Each endpoint:

  1. Accepts the source's native event dict (the schema the source's
     ``normalize()`` function expects).
  2. Calls the source's ``normalize()`` to convert to the unified
     ``OrderIn`` schema (the same schema the existing ``/risk/score``
     endpoint accepts).
  3. Returns the normalized OrderIn dict — so the caller (the
     simulator, or an external producer) can then turn around + POST
     to ``/risk/score`` with the normalized body + the appropriate
     ``X-Channel`` header.

This router is **standalone** — Task 12-e does NOT mount it into the
existing ``create_app`` factory in ``src/api/routes.py`` (that file
is owned by Task 12-bc). Operators who want the ingest endpoints
live in their deployment can mount it manually::

    from src.api.routes import create_app
    from src.api.ingest_routes import router as ingest_router

    app = create_app()
    app.include_router(ingest_router, prefix="/v1")

Or programmatically via ``include_router`` after ``create_app()`` is
called. The simulator itself does NOT depend on these endpoints being
mounted — it publishes to Redis Streams directly OR POSTs to the
existing ``/risk/score`` endpoint (which already handles OrderIn).

These endpoints are useful for:
  * Programmatic ingest from external producers (Kafka consumers,
    CRM webhooks, ATM switch batch jobs) that want the server-side
    normalization layer rather than re-implementing it client-side.
  * Testing — ``tests/test_simulator.py`` mounts this router on a
    bare FastAPI instance + round-trips a source event through it to
    verify the normalization pipeline produces valid OrderIn dicts.
  * The future "ingest-worker" service that drains the
    ``ingest.{source}`` Redis Streams this simulator publishes to —
    that worker would call these endpoints to normalize + forward
    to ``/risk/score`` (the worker doesn't exist yet; this router is
    the API surface it would use).

Source: Kandula 2021 (Payment_Type as a discriminator). The
``X-Channel`` header is set on the response so a downstream proxy or
the ingest-worker can pass it through to ``/risk/score``'s audit
record.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.ingest import atm as atm_channel
from src.ingest import callcenter as callcenter_channel
from src.ingest import ecommerce as ecommerce_channel
from src.ingest import mobile as mobile_channel

router = APIRouter(
    prefix="/v1/ingest",
    tags=["ingest"],
    responses={422: {"description": "validation error"}},
)


# ---------------------------------------------------------------------------
# Request models — one per source, accepting the source's native event
# schema (the dict shape the source's ``normalize()`` expects). Using
# ``model_config = ConfigDict(extra="allow")`` so source-specific extra
# fields (e.g. the callcenter's agent_id, call_id, call_duration_sec)
# pass through to ``normalize()`` without raising Pydantic's
# "extra field not permitted" validation error.
# ---------------------------------------------------------------------------

class EcommerceEventIn(BaseModel):
    """E-commerce order event — must match the OrderIn schema directly
    (the e-commerce channel's ``normalize()`` is the identity function).

    All fields are required since the e-commerce channel doesn't have
    any "default-applied" fields — the merchant's web checkout is
    expected to post a fully-formed OrderIn-conformant body.
    """

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
    merchant_id: str | None = Field(default=None, max_length=64)


class MobileEventIn(BaseModel):
    """Mobile-banking event — the schema ``mobile.normalize()`` expects.

    The mobile-banking channel's native event is the Kafka-topic
    payload from the mobile banking app. ``model_config`` allows extra
    fields (event_type, event_id, timestamp, etc.) so the source-native
    envelope passes through ``normalize()`` untouched (those extras
    are dropped by OrderIn's Pydantic validation downstream).
    """

    model_config = {"extra": "allow"}

    upi_id: str = Field(min_length=3, max_length=128)
    txn_amount: float = Field(gt=0, le=1_000_000)
    merchant_category: str = Field(min_length=2, max_length=32)
    customer_hash: str = Field(min_length=3, max_length=64)
    device_id: str = Field(default="Android App", max_length=64)
    payment_method: str = Field(default="Prepaid", pattern="^(COD|Prepaid)$")
    txn_hour: int = Field(default=12, ge=0, le=23)
    city_tier: str = Field(default="tier_2", pattern="^tier_[123]$")
    prior_orders: int = Field(default=0, ge=0, le=10_000)
    prior_returns: int = Field(default=0, ge=0, le=10_000)


class CallcenterEventIn(BaseModel):
    """Call-center webhook payload — the schema
    ``callcenter.normalize()`` expects. Extra fields (agent_id, call_id,
    call_duration_sec, call_outcome, timestamp) pass through unchanged.
    """

    model_config = {"extra": "allow"}

    order_id: str = Field(min_length=3, max_length=64)
    order_amount: float = Field(gt=0, le=1_000_000)
    customer_id: str = Field(min_length=3, max_length=64)
    reason_code: str = Field(min_length=2, max_length=64)
    address_quality: str = Field(default="complete", pattern="^(complete|partial|vague)$")
    city_tier: str = Field(default="tier_2", pattern="^tier_[123]$")
    payment_method: str = Field(default="COD", pattern="^(COD|Prepaid)$")
    prior_orders: int = Field(default=0, ge=0, le=10_000)
    prior_returns: int = Field(default=0, ge=0, le=10_000)
    items: int = Field(default=1, ge=1, le=100)
    order_hour: int = Field(default=12, ge=0, le=23)
    device: str = Field(default="Web", max_length=32)


class AtmEventIn(BaseModel):
    """ATM-switch-log CSV row — the schema ``atm.normalize()`` expects.

    Note: ATM events use the same ``normalize()`` as the CSV-row dict
    reader, so the field names match the CSV column headers exactly
    (``txn_id``, ``txn_amount``, ``card_pan_hash``, etc.). Extra fields
    (txn_type, card_last4, timestamp) pass through unchanged.
    """

    model_config = {"extra": "allow"}

    txn_id: str = Field(min_length=3, max_length=64)
    txn_amount: float = Field(gt=0, le=1_000_000)
    card_pan_hash: str = Field(min_length=3, max_length=64)
    card_network: str = Field(min_length=2, max_length=32)
    atm_id: str = Field(default="ATM", max_length=64)
    atm_location: str = Field(default="Mumbai", max_length=64)
    txn_hour: int = Field(default=12, ge=0, le=23)


# ---------------------------------------------------------------------------
# Endpoints.
# ---------------------------------------------------------------------------

@router.post("/ecommerce", response_model=dict)
def ingest_ecommerce(event: EcommerceEventIn) -> dict[str, Any]:
    """Accept an e-commerce order event + normalize to OrderIn.

    The e-commerce channel's ``normalize()`` is the identity function,
    so this endpoint effectively validates that the body conforms to
    OrderIn + returns it (useful for clients that want server-side
    validation before forwarding to ``/risk/score``).
    """
    normalized = ecommerce_channel.normalize(event.model_dump())
    return {"source": "ecommerce", "channel": ecommerce_channel.CHANNEL_ECOMMERCE,
            "orderin": normalized, "order_id": normalized["order_id"]}


@router.post("/mobile", response_model=dict)
def ingest_mobile(event: MobileEventIn) -> dict[str, Any]:
    """Accept a mobile-banking event + normalize to OrderIn via
    ``mobile.normalize()``.
    """
    normalized = mobile_channel.normalize(event.model_dump())
    return {"source": "mobile", "channel": mobile_channel.CHANNEL_MOBILE,
            "orderin": normalized, "order_id": normalized["order_id"]}


@router.post("/callcenter", response_model=dict)
def ingest_callcenter(event: CallcenterEventIn) -> dict[str, Any]:
    """Accept a call-center webhook payload + normalize to OrderIn via
    ``callcenter.normalize()``.
    """
    normalized = callcenter_channel.normalize(event.model_dump())
    return {"source": "callcenter", "channel": callcenter_channel.CHANNEL_CALL_CENTER,
            "orderin": normalized, "order_id": normalized["order_id"]}


@router.post("/atm", response_model=dict)
def ingest_atm(event: AtmEventIn) -> dict[str, Any]:
    """Accept an ATM-switch-log row + normalize to OrderIn via
    ``atm.normalize()``.
    """
    normalized = atm_channel.normalize(event.model_dump())
    return {"source": "atm", "channel": atm_channel.CHANNEL_ATM,
            "orderin": normalized, "order_id": normalized["order_id"]}


@router.get("/", response_model=dict)
def ingest_index() -> dict[str, Any]:
    """List the available ingest endpoints — useful for sanity checks
    after mounting (``GET /v1/ingest/`` returns the source list).
    """
    return {
        "sources": ["ecommerce", "mobile", "callcenter", "atm"],
        "endpoints": {
            "ecommerce": "POST /v1/ingest/ecommerce",
            "mobile": "POST /v1/ingest/mobile",
            "callcenter": "POST /v1/ingest/callcenter",
            "atm": "POST /v1/ingest/atm",
        },
        "note": (
            "Each endpoint accepts the source's native event schema + "
            "returns the normalized OrderIn dict. Forward the returned "
            "OrderIn to /risk/score with the X-Channel header set to "
            "the channel field."
        ),
    }
