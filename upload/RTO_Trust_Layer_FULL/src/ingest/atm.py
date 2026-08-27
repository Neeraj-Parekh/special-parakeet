"""ATM channel simulator — batch CSV ingest from ATM switch logs.

Day 4 Track M — Microsoft Fabric multi-source ingest reference calls
out ATMs as one of the 4 fraud-detection channels. This module
simulates a daily-batch CSV ingest from "ATM switch logs" — the
canonical case for ATM fraud detection (Skandalis 2024 paper:
real-time fraud scoring for card payments — Section 4.3 "batch
ingestion for ATM settlement reconciliation").

In production:
  * The ATM switch (NFS Network/FIS/Disney/Brinks) writes a daily CSV
    dump to an S3 bucket (one row per ATM transaction — card PAN
    hash, ATM ID, amount, location, timestamp, response code)
  * A scheduled Airflow/Dagster job downloads the CSV at 02:00 IST
    (after the bank's end-of-day reconciliation) + calls
    ``AtmChannel.run_batch(csv_path)`` to ingest it
  * Each row is normalized to OrderIn (mapping: ``atm_txn_amount`` →
    ``amount_inr``; ``card_pan_hash`` → ``customer_id``; ``atm_id`` is
    a channel-specific customer_id-formatted string; etc.)
  * The normalized orders are POSTed to /v1/risk/score with the
    ``X-Channel: atm`` header — the channel tag drives per-channel
    drift detection

For the buildathon demo:
  * ``run()`` generates a mock CSV of 100 ATM transactions + ingests
    them via the same code path. The CSV uses realistic Indian ATM
    field names (amount ₹1000-₹50000, atm locations: Mumbai, Delhi,
    Bangalore, etc., card networks: VISA, Mastercard, Rupay).

The ATM channel is the lowest-volume / highest-amount channel (per
RBI ATM statistics — ATM withdrawals are 1-2 orders of magnitude
larger than mobile UPI payments but 2 orders less frequent). The
simulator reflects this — only 100 transactions per daily batch
vs. the mobile channel's 2/sec.

Source: Kandula 2021 paper — Payment_Type as a discriminator feature.
Here we use ``channel=atm`` as the discriminator + the channel-specific
amount distribution would surface as a per-channel PSI shift in
the TFX generate_data_statistics job.
"""
from __future__ import annotations

import csv
import io
import random
from typing import Any

# Channel tag — surfaced in the audit record's ``channel`` field.
CHANNEL_ATM = "atm"

# Realistic Indian ATM locations + card networks.
_ATM_LOCATIONS = [
    ("Mumbai", "tier_1"), ("Delhi", "tier_1"), ("Bangalore", "tier_1"),
    ("Hyderabad", "tier_1"), ("Chennai", "tier_1"), ("Kolkata", "tier_1"),
    ("Pune", "tier_2"), ("Jaipur", "tier_2"), ("Lucknow", "tier_2"),
    ("Patna", "tier_3"), ("Ranchi", "tier_3"), ("Guwahati", "tier_3"),
]
_CARD_NETWORKS = ["VISA", "Mastercard", "RuPay"]


def normalize(row: dict) -> dict:
    """Normalize an ATM-switch-log CSV row to the unified OrderIn schema.

    ATM switch logs use card-network-specific field names. This function
    maps:

      * ``txn_id``            → ``order_id`` (the ATM switch's reference)
      * ``txn_amount``        → ``amount_inr``
      * ``card_pan_hash``     → ``customer_id`` (the salted PAN hash)
      * ``card_network``      → ``category`` (a proxy for the merchant
                                category — ATM is a single-merchant
                                channel; the network is the discriminator)
      * ``atm_location``      → ``city_tier`` (mapped via _ATM_LOCATIONS)
      * ``txn_hour``          → ``order_hour``

    The remaining OrderIn fields (address_quality, prior_orders, etc.)
    get sensible defaults — ATM transactions don't carry address info
    (the ATM IS the address), so address_quality = "complete" (no
    address-quality risk). prior_orders / prior_returns are not available
    at ATM-transaction time (the ATM switch doesn't query the bank's
    customer history); they default to 0 + the cost-optimizer uses the
    base rate as the prior.

    Args:
        row: A dict from csv.DictReader with ATM-switch-log field names.

    Returns:
        A dict conforming to the OrderIn Pydantic model.
    """
    # Map atm_location → city_tier via the _ATM_LOCATIONS table.
    location = row.get("atm_location") or "Mumbai"
    city_tier = "tier_2"  # default if location not in _ATM_LOCATIONS
    for loc, tier in _ATM_LOCATIONS:
        if loc.lower() in str(location).lower():
            city_tier = tier
            break

    return {
        "order_id": str(row.get("txn_id") or row.get("order_id") or ""),
        "amount_inr": float(row.get("txn_amount") or row.get("amount_inr") or 0),
        "category": str(row.get("card_network") or row.get("category") or "RuPay"),
        "customer_id": str(row.get("card_pan_hash") or row.get("customer_id") or ""),
        "address_quality": "complete",  # N/A for ATM
        "city_tier": city_tier,
        "payment_method": "Prepaid",  # ATM = always prepaid (debit card)
        "prior_orders": 0,  # not available at ATM time
        "prior_returns": 0,
        "items": 1,
        "order_hour": int(row.get("txn_hour") or row.get("order_hour") or 12),
        "device": str(row.get("atm_id") or row.get("device") or "ATM"),
    }


def generate_mock_csv(n_rows: int = 100, seed: int = 42) -> str:
    """Generate a realistic mock ATM-switch-log CSV for the demo.

    Returns the CSV as a string (so the simulator doesn't write a
    file to disk — the demo can run from a read-only container).

    Args:
        n_rows: Number of mock ATM transactions to generate.
        seed: Random seed for reproducibility.

    Returns:
        A CSV string with header + n_rows rows. The columns match
        what the ATM switch logs at the bank's end-of-day reconciliation.
    """
    rng = random.Random(seed)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "txn_id", "txn_amount", "card_pan_hash", "card_network",
        "atm_id", "atm_location", "txn_hour",
    ])
    for i in range(n_rows):
        loc, tier = rng.choice(_ATM_LOCATIONS)
        writer.writerow([
            f"ATM-TXN-{i:06d}",
            round(rng.uniform(1000, 50000), 2),
            f"PAN-{i:08d}",  # mock salted PAN hash
            rng.choice(_CARD_NETWORKS),
            f"ATM-{i % 50:03d}@{loc}",
            loc,
            rng.randint(0, 23),
        ])
    return buf.getvalue()


def run_batch(
    csv_str: str | None = None,
    api_url: str = "http://localhost:8000/risk/score",
    scorer_key: str = "score-demo-key",
    stop_event: Any | None = None,
) -> int:
    """Ingest a batch of ATM transactions from a CSV string.

    Args:
        csv_str: The CSV content as a string. If None, a default mock
            CSV of 100 transactions is generated via ``generate_mock_csv``.
        api_url: The /risk/score endpoint URL.
        scorer_key: The scorer-scope API key for the Authorization header.
        stop_event: A threading.Event for graceful shutdown.

    Returns:
        Number of orders successfully posted (HTTP 200).
    """
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        print(f"[atm] requests not installed — cannot run simulator: {e}")
        return 0

    if csv_str is None:
        csv_str = generate_mock_csv()

    headers = {
        "Authorization": f"Bearer {scorer_key}",
        "X-Channel": CHANNEL_ATM,
        "Content-Type": "application/json",
    }

    posted = 0
    reader = csv.DictReader(io.StringIO(csv_str))
    for row in reader:
        if stop_event is not None and stop_event.is_set():
            break
        normalized = normalize(row)
        try:
            r = requests.post(api_url, json=normalized, headers=headers, timeout=5)
            if r.status_code == 200:
                posted += 1
                body = r.json()
                print(
                    f"[atm] {normalized['order_id']} → "
                    f"{body.get('decision')} (p={body.get('probability')})"
                )
            else:
                print(f"[atm] {normalized['order_id']} → HTTP {r.status_code}")
        except Exception as e:  # pragma: no cover — best-effort, demo-only
            print(f"[atm] {normalized['order_id']} → ERROR {type(e).__name__}: {e}")
    return posted


def run(
    duration_s: float = 60.0,
    api_url: str = "http://localhost:8000/risk/score",
    scorer_key: str = "score-demo-key",
    interval_s: float = 0.0,  # ATM = batch, not stream
    stop_event: Any | None = None,
) -> int:
    """Run the ATM channel simulator for ``duration_s`` seconds.

    Generates a mock ATM switch-log CSV of 100 transactions + ingests
    it as a daily batch. The batch runs once (not in a loop) — the
    remaining duration_s is spent idle (the ATM channel is daily,
    not real-time). For the demo, ``run_simulators.py`` just calls
    this once per simulator cycle.

    Args:
        duration_s: Not used by ATM (batch channel); kept for API
            symmetry with mobile.py / callcenter.py.
        api_url: The /risk/score endpoint URL.
        scorer_key: The scorer-scope API key.
        interval_s: Not used by ATM (batch channel).
        stop_event: A threading.Event for graceful shutdown.

    Returns:
        Number of orders successfully posted (HTTP 200).
    """
    return run_batch(csv_str=None, api_url=api_url, scorer_key=scorer_key, stop_event=stop_event)
