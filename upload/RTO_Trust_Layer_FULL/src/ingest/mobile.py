"""Mobile banking channel simulator.

Day 4 Track M — Microsoft Fabric multi-source ingest reference calls out
mobile banking as one of the 4 fraud-detection channels. This module
simulates a Kafka topic consumer (the "mobile.orders" stream) that
deserializes mobile-banking order events + normalizes them to the unified
``OrderIn`` Pydantic model + posts to /v1/risk/score with the
``X-Channel: mobile`` header.

In production:
  * Kafka topic ``mobile.orders`` receives JSON events from the mobile
    banking app (UPI payment initiation, account-to-account transfer,
    bill payment, etc.)
  * A consumer-group worker (this module's ``MobileChannel.run`` method)
    drains the topic via ``confluent_kafka.Consumer.poll()``
  * Each event is normalized to OrderIn (mapping: ``txn_amount`` →
    ``amount_inr``; ``merchant_category_code`` → ``category``;
    ``customer_hash`` → ``customer_id``; etc.)
  * The normalized order is POSTed to /v1/risk/score with the channel
    header

For the buildathon demo:
  * We don't have a Kafka cluster; instead, ``run()` generates mock
    mobile-banking events + posts them via the same code path
  * The mock events are randomized within realistic bounds (amount
    ₹500-₹50000; categories mobile-banking merchants typically see:
    utilities, telecom, retail, etc.)
  * The post uses the ``requests`` library (synchronous; a real
    consumer would use httpx with connection pooling for throughput)

The simulator's ``run(duration_s, api_url, scorer_key)`` method is
called by ``scripts/run_simulators.py`` in a thread. It posts ~1 order
per 500ms (2/sec) for the duration specified.

Source: Kandula 2021 paper — Payment_Type as a discriminator feature
for fraud detection. Here we use the channel discriminator (mobile)
which is more granular than Payment_Type alone; per-channel drift
detection catches channel-specific fraud pattern shifts (e.g. a sudden
spike in mobile-UPI OTP-bypass fraud wouldn't show up in aggregate
drift detection but would surface as a per-channel shift).
"""
from __future__ import annotations

import random
import time
from typing import Any

# Channel tag — surfaced in the audit record's ``channel`` field.
CHANNEL_MOBILE = "mobile"

# Realistic mobile-banking merchant categories (RBI MCC codes mapped
# to short category strings the OrderIn schema accepts).
_MOBILE_CATEGORIES = [
    "Utilities", "Telecom", "Retail", "Food", "Travel",
    "Recharge", "DTH", "Insurance", "Mutual Fund", "Loan EMI",
]

# Default post interval — 2 orders/sec matches the mobile banking app's
# peak hour throughput per the user's product hypothesis.
_DEFAULT_INTERVAL_S = 0.5


def normalize(raw: dict) -> dict:
    """Normalize a mobile-banking event to the unified OrderIn schema.

    Mobile-banking events use a different field naming convention than
    the e-commerce OrderIn schema. This function maps:

      * ``txn_amount``        → ``amount_inr``
      * ``merchant_category`` → ``category``
      * ``customer_hash``     → ``customer_id`` (already a salted hash
                                  from the mobile banking app)
      * ``upi_id``            → ``order_id`` (the UPI transaction ref)
      * ``device_id``        → ``device`` (mobile device fingerprint)
      * ``payment_method``   → kept (COD is rare in mobile banking;
                                  almost always Prepaid)
      * ``txn_hour``         → ``order_hour``

    The remaining OrderIn fields (prior_orders, prior_returns, items,
    address_quality, city_tier) get sensible defaults — the mobile
    banking channel doesn't have an "address" concept, so
    address_quality defaults to "complete" (a non-issue for mobile).

    Args:
        raw: A dict with mobile-banking-specific field names.

    Returns:
        A dict conforming to the OrderIn Pydantic model.
    """
    return {
        "order_id": str(raw.get("upi_id") or raw.get("order_id") or ""),
        "amount_inr": float(raw.get("txn_amount") or raw.get("amount_inr") or 0),
        "category": str(raw.get("merchant_category") or raw.get("category") or "Retail"),
        "customer_id": str(raw.get("customer_hash") or raw.get("customer_id") or ""),
        "address_quality": "complete",  # N/A for mobile — use the benign default
        "city_tier": str(raw.get("city_tier") or "tier_2"),
        "payment_method": str(raw.get("payment_method") or "Prepaid"),
        "prior_orders": int(raw.get("prior_orders") or 0),
        "prior_returns": int(raw.get("prior_returns") or 0),
        "items": 1,  # mobile banking is single-payment; items=1 always
        "order_hour": int(raw.get("txn_hour") or raw.get("order_hour") or 12),
        "device": str(raw.get("device") or raw.get("device_id") or "Android App"),
    }


def _generate_mock_event(seed: int) -> dict:
    """Generate a realistic mock mobile-banking event for the demo."""
    rng = random.Random(seed)
    return {
        "upi_id": f"UPI-{seed:08d}",
        "txn_amount": round(rng.uniform(500, 50000), 2),
        "merchant_category": rng.choice(_MOBILE_CATEGORIES),
        "customer_hash": f"CUST-M{seed:06d}",
        "device_id": rng.choice(["Android App", "iOS App", "Android Web"]),
        "payment_method": "Prepaid",  # mobile is always Prepaid (UPI)
        "txn_hour": rng.randint(7, 22),  # banking hours (mobile still active late)
        "city_tier": rng.choice(["tier_1", "tier_2", "tier_3"]),
        "prior_orders": rng.randint(0, 50),
        "prior_returns": rng.randint(0, 5),
    }


def run(
    duration_s: float = 60.0,
    api_url: str = "http://localhost:8000/risk/score",
    scorer_key: str = "score-demo-key",
    interval_s: float = _DEFAULT_INTERVAL_S,
    stop_event: Any | None = None,
) -> int:
    """Run the mobile banking simulator for ``duration_s`` seconds.

    Generates mock mobile-banking events + posts them to /v1/risk/score
    with the ``X-Channel: mobile`` header. Each post carries the OrderIn
    JSON body normalized via ``normalize()``.

    Args:
        duration_s: How long to run the simulator (seconds).
        api_url: The /risk/score endpoint URL (default: localhost:8000).
        scorer_key: The scorer-scope API key for Authorization header.
        interval_s: Time between posts (default 0.5s = 2/sec).
        stop_event: A threading.Event for graceful shutdown — if set,
            the loop exits early (used by run_simulators.py's signal
            handler so Ctrl-C terminates cleanly).

    Returns:
        The number of orders successfully posted (HTTP 200). Posts that
        failed (network error, 4xx/5xx) are counted as 0.
    """
    # Lazy import — so the module is import-safe in CI environments
    # without ``requests`` installed (the test suite mocks the post).
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        print(f"[mobile] requests not installed — cannot run simulator: {e}")
        return 0

    headers = {
        "Authorization": f"Bearer {scorer_key}",
        "X-Channel": CHANNEL_MOBILE,
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    posted = 0
    i = 0
    while time.monotonic() - start < duration_s:
        if stop_event is not None and stop_event.is_set():
            break
        event = _generate_mock_event(i)
        normalized = normalize(event)
        try:
            r = requests.post(api_url, json=normalized, headers=headers, timeout=5)
            if r.status_code == 200:
                posted += 1
                body = r.json()
                print(
                    f"[mobile] {normalized['order_id']} → "
                    f"{body.get('decision')} (p={body.get('probability')})"
                )
            else:
                print(f"[mobile] {normalized['order_id']} → HTTP {r.status_code}")
        except Exception as e:  # pragma: no cover — best-effort, demo-only
            print(f"[mobile] {normalized['order_id']} → ERROR {type(e).__name__}: {e}")
        i += 1
        time.sleep(interval_s)
    return posted
