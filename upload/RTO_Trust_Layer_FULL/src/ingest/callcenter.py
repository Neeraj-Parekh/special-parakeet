"""Call center channel simulator — webhook receiver pattern.

Day 4 Track M — Microsoft Fabric multi-source ingest reference calls
out the call center as one of the 4 fraud-detection channels. This
module simulates a call center agent's "flag this order" workflow:

  1. Customer calls the call center with a dispute / question about
     a recent order.
  2. The agent pulls up the order in their CRM (Zendesk/Freshdesk/
     Salesforce Service Cloud).
  3. The agent clicks "Flag for Risk Review" — the CRM fires a
     webhook to this simulator's receiver.
  4. The simulator normalizes the webhook payload to OrderIn + posts
     to /v1/risk/score with the ``X-Channel: call_center`` header.

In production:
  * The webhook receiver is a FastAPI app on a separate port (e.g.
    :5002) — the call center's CRM calls it on each "flag" event
  * The webhook payload includes the order_id, the agent_id, the
    reason_code, and a free-text notes field (which the agent types)
  * This module's ``run()`` doesn't expose a webhook server (the
    run_simulators.py demo calls ``post_one(...)`` directly with a
    mock webhook payload — the webhook server pattern is documented
    for the user's prod wiring)

For the buildathon demo:
  * ``run()`` generates mock call-center "flag" events + posts them
    at a low rate (1 per 5s — call center flag volume is much lower
    than mobile/atm volume; this matches the realistic ~720 flags/day
    for a mid-size e-commerce merchant).

The call center channel is uniquely valuable for the fraud-detection
feedback loop: the agent's flag is a high-precision positive label
(agent-confirmed fraud, no false-positive ambiguity). Track G's
``LabelFeedbackService`` consumes these as a high-confidence positive
label stream — the prequential drift detection weight is higher for
call-center-flagged orders than for delayed chargeback labels.

Source: Kandula 2021 paper — Payment_Type as a discriminator feature.
Here ``channel=call_center`` is the discriminator; the per-channel
PSI on this channel surfaces call-center-driven drift shifts
(e.g. a sudden spike in "address_quality=vague" flags from the call
center would indicate an upstream data-quality regression in the
merchant's address collection form).
"""
from __future__ import annotations

import random
import time
from typing import Any

# Channel tag — surfaced in the audit record's ``channel`` field.
CHANNEL_CALL_CENTER = "call_center"

# Realistic call-center "flag" reason codes (what the agent selected
# from the CRM dropdown). Mapped to OrderIn fields where applicable.
_REASON_CODES = [
    "customer_dispute", "address_mismatch", "payment_failure",
    "delivery_refused", "fraud_suspicion", "duplicate_order",
]

# Default post interval — 1 order per 5s matches ~720 flags/day.
_DEFAULT_INTERVAL_S = 5.0


def normalize(webhook_payload: dict) -> dict:
    """Normalize a call-center webhook payload to the unified OrderIn schema.

    Call center webhook payloads include agent-side context (agent_id,
    reason_code, notes) that doesn't fit the OrderIn schema directly.
    This function maps:

      * ``order_id``        → ``order_id`` (the order the agent flagged)
      * ``order_amount``    → ``amount_inr``
      * ``customer_id``     → ``customer_id`` (the merchant's customer id)
      * ``reason_code``     → ``category`` (a proxy — the reason code IS
                              the category for call-center flags)
      * ``address_quality`` → ``address_quality`` (agent-confirmed;
                              "vague" if the agent noted the address
                              was incomplete)
      * ``prior_orders``    → ``prior_orders`` (the agent pulled the
                              customer's order history from the CRM)

    The remaining OrderIn fields (city_tier, payment_method, items,
    order_hour, device) get sensible defaults from the webhook
    payload when present, otherwise the OrderIn defaults.

    Args:
        webhook_payload: A dict with call-center-specific fields.

    Returns:
        A dict conforming to the OrderIn Pydantic model.
    """
    return {
        "order_id": str(webhook_payload.get("order_id") or ""),
        "amount_inr": float(
            webhook_payload.get("order_amount")
            or webhook_payload.get("amount_inr")
            or 0
        ),
        "category": str(
            webhook_payload.get("reason_code")
            or webhook_payload.get("category")
            or "customer_dispute"
        ),
        "customer_id": str(webhook_payload.get("customer_id") or ""),
        "address_quality": str(webhook_payload.get("address_quality") or "complete"),
        "city_tier": str(webhook_payload.get("city_tier") or "tier_2"),
        "payment_method": str(webhook_payload.get("payment_method") or "COD"),
        "prior_orders": int(webhook_payload.get("prior_orders") or 0),
        "prior_returns": int(webhook_payload.get("prior_returns") or 0),
        "items": int(webhook_payload.get("items") or 1),
        "order_hour": int(webhook_payload.get("order_hour") or 12),
        "device": str(webhook_payload.get("device") or "Web"),
    }


def _generate_mock_webhook(seed: int) -> dict:
    """Generate a realistic mock call-center webhook payload for the demo."""
    rng = random.Random(seed)
    return {
        "order_id": f"CC-FLAG-{seed:06d}",
        "order_amount": round(rng.uniform(2000, 30000), 2),
        "customer_id": f"CUST-CC-{seed:06d}",
        "reason_code": rng.choice(_REASON_CODES),
        "address_quality": rng.choice(["complete", "partial", "vague"]),
        "city_tier": rng.choice(["tier_1", "tier_2", "tier_3"]),
        "payment_method": rng.choice(["COD", "Prepaid"]),
        "prior_orders": rng.randint(1, 20),  # call center: usually existing customer
        "prior_returns": rng.randint(0, 3),
        "items": rng.randint(1, 5),
        "order_hour": rng.randint(8, 22),
        "device": rng.choice(["Web", "Android App", "iOS App"]),
        "agent_id": f"AGENT-{rng.randint(100, 999)}",
    }


def run(
    duration_s: float = 60.0,
    api_url: str = "http://localhost:8000/risk/score",
    scorer_key: str = "score-demo-key",
    interval_s: float = _DEFAULT_INTERVAL_S,
    stop_event: Any | None = None,
) -> int:
    """Run the call center simulator for ``duration_s`` seconds.

    Generates mock call-center webhook payloads + posts them to
    /v1/risk/score with the ``X-Channel: call_center`` header.

    Args:
        duration_s: How long to run the simulator (seconds).
        api_url: The /risk/score endpoint URL.
        scorer_key: The scorer-scope API key for Authorization header.
        interval_s: Time between posts (default 5s = 1/5sec; the call
            center channel is lower-volume than mobile/atm).
        stop_event: A threading.Event for graceful shutdown.

    Returns:
        Number of orders successfully posted (HTTP 200).
    """
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        print(f"[callcenter] requests not installed — cannot run simulator: {e}")
        return 0

    headers = {
        "Authorization": f"Bearer {scorer_key}",
        "X-Channel": CHANNEL_CALL_CENTER,
        "Content-Type": "application/json",
    }

    start = time.monotonic()
    posted = 0
    i = 0
    while time.monotonic() - start < duration_s:
        if stop_event is not None and stop_event.is_set():
            break
        webhook = _generate_mock_webhook(i)
        normalized = normalize(webhook)
        try:
            r = requests.post(api_url, json=normalized, headers=headers, timeout=5)
            if r.status_code == 200:
                posted += 1
                body = r.json()
                print(
                    f"[callcenter] {normalized['order_id']} → "
                    f"{body.get('decision')} (p={body.get('probability')})"
                )
            else:
                print(f"[callcenter] {normalized['order_id']} → HTTP {r.status_code}")
        except Exception as e:  # pragma: no cover — best-effort, demo-only
            print(f"[callcenter] {normalized['order_id']} → ERROR {type(e).__name__}: {e}")
        i += 1
        time.sleep(interval_s)
    return posted
