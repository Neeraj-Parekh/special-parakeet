"""E-commerce channel — the existing REST /v1/risk/score path.

Day 4 Track M — Microsoft Fabric multi-source ingest reference
(https://learn.microsoft.com/en-us/fabric/real-time-intelligence/architectures/fraud-detection)
calls out 4 ingest channels: mobile banking, ATM, e-commerce, call
center. The RTO Trust Layer's existing POST /v1/risk/score endpoint
(in ``src/api/routes.py``) IS the e-commerce channel — no simulator
needed. This module documents that mapping so the demo script
(``scripts/run_simulators.py``) can list all 4 channels uniformly.

The e-commerce channel:
  * Source: merchant's web checkout (Node/Next.js/route handler posts
    JSON to ``/v1/risk/score`` with ``Authorization: Bearer <scorer-key>``)
  * Schema: directly the ``OrderIn`` Pydantic model in routes.py —
    no normalization needed (the merchant's web layer already conforms)
  * Channel tag: ``X-Channel: ecommerce`` (the default if header is
    absent — see routes.py where ``x_channel`` defaults to ``"ecommerce"``)
  * Audit record: ``channel`` field set to ``"ecommerce"``

In production, the merchant's web checkout calls this endpoint on the
"Place Order" button click BEFORE the order is dispatched — the API
returns ACCEPT (ship now), REVIEW (queue + OTP verify), or REJECT
(block + refund). The merchant's UI surfaces the decision to the
operator + the customer (e.g. REJECT → "we couldn't process your order
please contact support"; REVIEW → "please complete the OTP we just sent").

For the demo, the run_simulators.py script uses the same flow as the
other 3 simulators — it posts mock e-commerce orders directly to the
endpoint with the ``X-Channel: ecommerce`` header.
"""
from __future__ import annotations

# The e-commerce channel tag — surfaced in the audit record's ``channel``
# field. Used by the TFX generate_data_statistics job to slice per-channel
# drift detection (Kandula 2021 paper: Payment_Type as discriminator →
# here ``channel`` is the discriminator).
CHANNEL_ECOMMERCE = "ecommerce"


def normalize(raw: dict) -> dict:
    """Identity normalize — the e-commerce source already conforms to
    the OrderIn schema (the merchant's web checkout posts the schema
    directly). Kept here so all 4 channel simulators have the same
    ``normalize(raw) -> dict`` surface for the run_simulators.py script.

    Args:
        raw: A dict that already matches the OrderIn Pydantic model
            (order_id, amount_inr, category, customer_id, ...). No
            transformation needed.

    Returns:
        The same dict (the e-commerce channel is the reference schema).
    """
    return dict(raw)
