"""Tests for the multi-source ingest simulators (Track M Day 4).

Verifies each simulator's ``normalize()`` output conforms to the unified
``OrderIn`` Pydantic model in ``src/api/routes.py``. This is the contract
that lets the 4 channels (ecommerce, mobile, atm, callcenter) feed the
same /v1/risk/score endpoint with the same schema.

Per the Microsoft Fabric fraud-detection reference
(https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection),
4 channels: mobile banking, ATM, e-commerce, call center. Each channel
has its own source schema (Kafka events for mobile, CSV rows for ATM,
webhook payloads for call center, the canonical REST JSON for
e-commerce). The simulators normalize each into OrderIn so the API
doesn't need channel-specific handlers.

The Kandula 2021 paper's insight — Payment_Type as a discriminator —
is realized here as the ``channel`` field on the audit record. Each
simulator's run() posts with the appropriate ``X-Channel`` header so
the audit record carries the discriminator → per-channel drift
detection via TFX generate_data_statistics.

Test count: 8 — 2 per channel (normalize validity + mock-data
generation smoke test).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import OrderIn  # noqa: E402
from src.ingest import (  # noqa: E402
    atm,
    callcenter,
    ecommerce,
    mobile,
)

# ---------------------------------------------------------------------------
# Shared validation helper.
# ---------------------------------------------------------------------------


def _assert_is_valid_orderin(normalized: dict) -> None:
    """Assert that the dict validates against the OrderIn Pydantic model.

    OrderIn is the unified schema the /v1/risk/score endpoint accepts.
    Each simulator's ``normalize()`` must produce a dict that
    ``OrderIn(**normalized)`` accepts without raising — this is the
    contract that lets all 4 channels feed the same endpoint.
    """
    obj = OrderIn(**normalized)
    # Verify all the required fields made it through.
    assert obj.order_id == normalized["order_id"]
    assert obj.amount_inr == pytest.approx(normalized["amount_inr"])
    assert obj.category == normalized["category"]
    assert obj.customer_id == normalized["customer_id"]
    # Optional fields with defaults — verify they're set.
    assert obj.address_quality in ("complete", "partial", "vague")
    assert obj.city_tier in ("tier_1", "tier_2", "tier_3")
    assert obj.payment_method in ("COD", "Prepaid")
    assert obj.prior_orders >= 0
    assert obj.prior_returns >= 0
    assert 1 <= obj.items <= 100
    assert 0 <= obj.order_hour <= 23
    assert isinstance(obj.device, str) and len(obj.device) >= 1


# ---------------------------------------------------------------------------
# E-commerce channel.
# ---------------------------------------------------------------------------


def test_ecommerce_normalize_is_identity():
    """The e-commerce channel uses the OrderIn schema directly —
    normalize() is the identity function. Verified here so all 4
    channels have the same ``normalize(raw) -> dict`` surface.
    """
    raw = {
        "order_id": "EC-TEST-1",
        "amount_inr": 1499.0,
        "category": "Fashion",
        "customer_id": "CUST-EC-1",
        "payment_method": "COD",
        "city_tier": "tier_2",
        "address_quality": "complete",
        "prior_orders": 5,
        "prior_returns": 1,
        "items": 2,
        "order_hour": 14,
        "device": "Web",
    }
    result = ecommerce.normalize(raw)
    assert result == raw, "e-commerce normalize should be identity"
    _assert_is_valid_orderin(result)
    assert ecommerce.CHANNEL_ECOMMERCE == "ecommerce"


# ---------------------------------------------------------------------------
# Mobile banking channel.
# ---------------------------------------------------------------------------


def test_mobile_normalize_maps_mobile_banking_fields():
    """mobile.normalize() maps mobile-banking event fields to OrderIn:
    upi_id → order_id, txn_amount → amount_inr, customer_hash →
    customer_id, merchant_category → category, device_id → device.
    """
    raw_event = {
        "upi_id": "UPI-12345678",
        "txn_amount": 4500.00,
        "merchant_category": "Telecom",
        "customer_hash": "CUST-M-000123",
        "device_id": "Android App",
        "payment_method": "Prepaid",
        "txn_hour": 9,
        "city_tier": "tier_1",
        "prior_orders": 3,
        "prior_returns": 0,
    }
    result = mobile.normalize(raw_event)
    # Field mappings are correct.
    assert result["order_id"] == "UPI-12345678"
    assert result["amount_inr"] == 4500.00
    assert result["category"] == "Telecom"
    assert result["customer_id"] == "CUST-M-000123"
    assert result["device"] == "Android App"
    assert result["payment_method"] == "Prepaid"
    assert result["order_hour"] == 9
    # Defaults applied for ATM-specific absent fields.
    assert result["address_quality"] == "complete"  # N/A for mobile
    assert result["items"] == 1  # mobile banking is single-payment
    # The normalized dict is OrderIn-valid.
    _assert_is_valid_orderin(result)
    assert mobile.CHANNEL_MOBILE == "mobile"


def test_mobile_mock_event_generates_valid_orderin():
    """The _generate_mock_event helper produces events that normalize()
    to OrderIn-valid dicts — the demo script's run() uses this.
    """
    for seed in range(10):
        event = mobile._generate_mock_event(seed)
        normalized = mobile.normalize(event)
        _assert_is_valid_orderin(normalized)
        # Mobile-banking-specific: always Prepaid (UPI).
        assert normalized["payment_method"] == "Prepaid"


# ---------------------------------------------------------------------------
# ATM channel.
# ---------------------------------------------------------------------------


def test_atm_normalize_maps_atm_switch_log_fields():
    """atm.normalize() maps ATM-switch-log CSV row fields to OrderIn:
    txn_id → order_id, txn_amount → amount_inr, card_pan_hash →
    customer_id, card_network → category, atm_location → city_tier
    (via the _ATM_LOCATIONS table).
    """
    raw_row = {
        "txn_id": "ATM-TXN-000042",
        "txn_amount": 20000.00,
        "card_pan_hash": "PAN-00000042",
        "card_network": "VISA",
        "atm_id": "ATM-007@Bangalore",
        "atm_location": "Bangalore",
        "txn_hour": 18,
    }
    result = atm.normalize(raw_row)
    # Field mappings are correct.
    assert result["order_id"] == "ATM-TXN-000042"
    assert result["amount_inr"] == 20000.00
    assert result["category"] == "VISA"
    assert result["customer_id"] == "PAN-00000042"
    assert result["device"] == "ATM-007@Bangalore"
    assert result["order_hour"] == 18
    # Bangalore is tier_1 in _ATM_LOCATIONS.
    assert result["city_tier"] == "tier_1"
    # Defaults applied for ATM-specific absent fields.
    assert result["address_quality"] == "complete"  # N/A for ATM
    assert result["payment_method"] == "Prepaid"  # ATM is always prepaid (debit card)
    assert result["prior_orders"] == 0  # not available at ATM time
    assert result["prior_returns"] == 0
    assert result["items"] == 1
    # The normalized dict is OrderIn-valid.
    _assert_is_valid_orderin(result)
    assert atm.CHANNEL_ATM == "atm"


def test_atm_mock_csv_generates_valid_orderin_rows():
    """The generate_mock_csv helper produces rows that normalize() to
    OrderIn-valid dicts — the demo script's run_batch() uses this.
    """
    import csv
    import io

    csv_str = atm.generate_mock_csv(n_rows=20, seed=42)
    rows = list(csv.DictReader(io.StringIO(csv_str)))
    assert len(rows) == 20
    for row in rows:
        normalized = atm.normalize(row)
        _assert_is_valid_orderin(normalized)
        # ATM-specific: always Prepaid (debit card).
        assert normalized["payment_method"] == "Prepaid"
        # city_tier must be one of the 3 valid tiers (the _ATM_LOCATIONS
        # table maps every mock location to a tier).
        assert normalized["city_tier"] in ("tier_1", "tier_2", "tier_3")


# ---------------------------------------------------------------------------
# Call center channel.
# ---------------------------------------------------------------------------


def test_callcenter_normalize_maps_webhook_payload_fields():
    """callcenter.normalize() maps the webhook payload's fields to OrderIn:
    order_id stays, order_amount → amount_inr, reason_code → category,
    customer_id stays. Agent-confirmed address_quality passes through.
    """
    webhook_payload = {
        "order_id": "CC-FLAG-000001",
        "order_amount": 8500.00,
        "customer_id": "CUST-CC-000001",
        "reason_code": "address_mismatch",
        "address_quality": "vague",  # agent-confirmed incomplete address
        "city_tier": "tier_3",
        "payment_method": "COD",
        "prior_orders": 12,
        "prior_returns": 2,
        "items": 3,
        "order_hour": 16,
        "device": "Web",
        "agent_id": "AGENT-123",  # extra field — should be dropped by OrderIn
    }
    result = callcenter.normalize(webhook_payload)
    # Field mappings are correct.
    assert result["order_id"] == "CC-FLAG-000001"
    assert result["amount_inr"] == 8500.00
    assert result["category"] == "address_mismatch"
    assert result["customer_id"] == "CUST-CC-000001"
    assert result["address_quality"] == "vague"
    assert result["order_hour"] == 16
    # The normalized dict is OrderIn-valid (the agent_id extra field is
    # silently dropped by OrderIn's Pydantic validation).
    _assert_is_valid_orderin(result)
    assert callcenter.CHANNEL_CALL_CENTER == "call_center"


def test_callcenter_mock_webhook_generates_valid_orderin():
    """The _generate_mock_webhook helper produces payloads that normalize()
    to OrderIn-valid dicts — the demo script's run() uses this.
    """
    for seed in range(10):
        webhook = callcenter._generate_mock_webhook(seed)
        normalized = callcenter.normalize(webhook)
        _assert_is_valid_orderin(normalized)
        # Call-center-specific: the reason_code becomes the category.
        assert normalized["category"] in (
            "customer_dispute", "address_mismatch", "payment_failure",
            "delivery_refused", "fraud_suspicion", "duplicate_order",
        )
