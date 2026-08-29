"""Ingest a real Kaggle e-commerce CSV into our unified schema.

Usage
-----
1. Download a Kaggle CSV manually into ``data/raw/`` (e.g. the Amazon India
   Sale Report — ~129k orders). The default expected file is::

       data/raw/amazon_sale_report.csv

2. Run::

       python scripts/ingest_kaggle.py                       # default --source amazon
       python scripts/ingest_kaggle.py --source indian_ecom # smaller alt dataset
       python scripts/ingest_kaggle.py --source online_retail
       python scripts/ingest_kaggle.py --source amazon --out data/raw/ingested_real.csv

The script:
  * Maps Kaggle source columns to the unified schema
    (``order_id``, ``amount_inr``, ``payment_method``, ``category``, ``items``,
    ``city``, ``state``, ``pincode``, ``order_date``, ``is_cod``,
    ``is_returned``).
  * Normalises RTO-style statuses into ``Returned`` / ``Delivered`` (drops
    ``Cancelled`` rows — they are merchant-initiated cancellations, not
    delivery failures, so they don't carry RTO signal).
  * Infers ``is_cod`` when no explicit payment-method column is present
    (default for the Amazon India Sale Report — assume COD for the RTO
    problem; B2B orders are typically prepaid, so we use the ``B2B`` flag
    as a proxy when available).
  * Writes ``data/raw/ingested_real.csv`` (default; override with ``--out``)
    ready for ``src.features.cleaning.load_ingested_real`` to read.

Sources
-------
* `command/05-PAPER-SKILLS-MAP.md` gap #6 + C4 (Kandula 2021 —
  ``Payment_Type``, ``Service_Tier``, ``Qty``, address-level POI counts).
* `command/06-PROMPT-RAZOR-EXTRACTION.md` §5 — Amazon India Sale Report is
  the primary dataset (~129k orders), Indian E-commerce Dataset (~50k,
  explicit COD flag), Online Retail (UCI, UK, no RTO labels).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# --------------------------------------------------------------------- #
# Per-source column maps (target -> list of candidate source columns;   #
# first match wins).                                                    #
# --------------------------------------------------------------------- #
COLUMN_MAPS: dict[str, dict[str, list[str]]] = {
    "amazon": {
        # Amazon India Sale Report (Kaggle) — primary dataset, ~129k orders.
        "order_id": ["Order ID", "order_id", "Order#", "invoice_id"],
        "amount_inr": ["Amount", "amount", "Order Value", "order_value", "Cur. Sales Value"],
        "category": ["Category", "category", "Product Category"],
        "items": ["Qty", "Quantity", "items", "qty"],
        "city": ["ship-city", "ship_city", "City"],
        "state": ["ship-state", "ship_state", "State", "state"],
        "pincode": ["ship-postal-code", "ship_postal_code", "postal-code", "Pincode", "pincode"],
        "order_date": ["Date", "date", "Order Date", "order_date"],
        # Status is the source of `is_returned` (see normalize_labels below).
        "status": ["Status", "status", "Delivery Status", "Order Status"],
        # COD / payment inference — see infer_is_cod() below. The Amazon
        # India Sale Report has no explicit `payment_method` column; the
        # spec directive is "if no explicit COD flag, assume COD for the
        # RTO problem" — we use `B2B` (B2B orders are typically prepaid) as
        # a proxy when available, falling back to is_cod=1 (COD).
        "fulfilment": ["Fulfilment", "fulfilment", "Fulfillment"],
        "b2b": ["B2B", "b2b"],
        "ship_service_tier": ["ship-service-tier", "ship_service_tier", "Service Tier"],
    },
    "indian_ecom": {
        # Smaller alt dataset (~50k orders) — has explicit COD flag +
        # return status. Column names are guessed; fix on first ingestion.
        "order_id": ["Order ID", "order_id", "order_id_1"],
        "amount_inr": ["Amount", "amount", "Order Value", "Total"],
        "category": ["Category", "category", "product_category"],
        "items": ["Qty", "Quantity", "items", "qty"],
        "city": ["City", "ship-city", "city"],
        "state": ["State", "ship-state", "state"],
        "pincode": ["Pincode", "ship-postal-code", "pincode", "Zipcode"],
        "order_date": ["Order Date", "Date", "order_date", "date"],
        "status": ["Return Status", "Status", "Delivery Status"],
        "payment_method": ["Payment Method", "payment_method", "PaymentType"],
    },
    "online_retail": {
        # UCI Online Retail — UK, ~541k transactions, no RTO labels. Useful
        # only for RFM-style feature engineering patterns; ingested but the
        # downstream model can't train on it (no `is_returned` target).
        "order_id": ["InvoiceNo", "order_id", "Order ID"],
        "amount_inr": ["UnitPrice", "amount", "Amount"],  # placeholder; multiply by Qty
        "category": ["Description", "Category", "category"],
        "items": ["Quantity", "Qty", "items", "qty"],
        "order_date": ["InvoiceDate", "Date", "order_date"],
        "customer_id": ["CustomerID", "customer_id", "Customer ID"],
        "country": ["Country", "country"],
    },
}

# Amazon India Sale Report `Status` value distribution (sampled):
#   - "Shipped - Delivered to Buyer" → Delivered
#   - "Shipped" / "Pending" / "Shipped - Picked Up" → Delivered (still in
#     transit at extraction; treat as non-returned)
#   - "Shipped - Returned to Seller" / "Shipped - Returned to Seller" / "Returned" → Returned
#   - "Shipped - Rejected by Buyer" → Returned (buyer refused delivery = RTO)
#   - "Cancelled" → DROP (merchant-initiated; not an RTO outcome)
STATUS_LABEL_MAP = {
    # Returned outcomes (RTO positive class)
    "returned": "Returned",
    "return to seller": "Returned",
    "returned to seller": "Returned",
    "shipped - returned to seller": "Returned",
    "shipped - returned to buyer": "Returned",
    "rto": "Returned",
    "rto - returned to seller": "Returned",
    "rejected by buyer": "Returned",
    "shipped - rejected by buyer": "Returned",
    "undeliverable": "Returned",
    # Cancelled → DROP (handled in normalize_labels, not mapped here)
}

# Status values that mean "merchant cancelled" — dropped from the
# training set because they're not an RTO delivery outcome.
CANCELLED_TOKENS = {"cancelled", "canceled", "shipped - cancelled"}


def map_columns(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Project source columns into the unified schema (target -> source).

    Unmapped required columns are filled with pd.NA + a stderr warning so
    the user sees what's missing before training. The downstream loader
    (`load_ingested_real`) handles nulls.
    """
    spec = COLUMN_MAPS.get(source)
    if spec is None:
        raise ValueError(
            f"unknown --source '{source}'. Pick from: {sorted(COLUMN_MAPS)}"
        )
    out = pd.DataFrame(index=df.index)
    for target, candidates in spec.items():
        for c in candidates:
            if c in df.columns:
                out[target] = df[c]
                break
        else:
            print(f"[warn] no source for '{target}' — filling nulls", file=sys.stderr)
            out[target] = pd.NA
    return out


def normalize_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Map `status` → `DeliveryStatus` (Returned/Delivered) + `is_returned`.

    Rows whose status tokenises to "cancelled" are dropped (merchant-cancelled
    orders are not an RTO outcome — the order never shipped, so there's no
    delivery to fail).
    """
    df = df.copy()
    status_norm = df["status"].astype(str).str.strip().str.lower()

    # Drop cancelled rows BEFORE mapping so they don't get treated as Returned.
    keep_mask = ~status_norm.isin(CANCELLED_TOKENS)
    n_cancelled = int((~keep_mask).sum())
    df = df.loc[keep_mask].reset_index(drop=True)
    status_norm = status_norm.loc[keep_mask].reset_index(drop=True)

    df["DeliveryStatus"] = status_norm.map(
        lambda s: STATUS_LABEL_MAP.get(s, "Delivered")
    )
    df["is_returned"] = (df["DeliveryStatus"] == "Returned").astype(int)
    if n_cancelled:
        print(
            f"[info] dropped {n_cancelled:,} cancelled rows (merchant-initiated; "
            f"not an RTO delivery outcome)",
            file=sys.stderr,
        )
    return df


def infer_is_cod(df: pd.DataFrame) -> pd.DataFrame:
    """Infer `is_cod` (1 = COD, 0 = prepaid) from any signal we have.

    The Amazon India Sale Report has no explicit payment-method column, so
    per the spec directive ("if no explicit COD flag, assume COD for the
    RTO problem") we fall back to is_cod=1. As a heuristic, B2B (business-
    to-business) orders are typically prepaid, so if the B2B flag is present
    we use it as a proxy: is_cod=0 for B2B orders, is_cod=1 otherwise.

    For the `indian_ecom` source where `payment_method` exists, we use the
    explicit signal (COD/cash → 1; otherwise 0).
    """
    df = df.copy()
    if "payment_method" in df.columns and df["payment_method"].notna().any():
        pm = df["payment_method"].astype(str).str.lower()
        df["is_cod"] = (
            pm.str.contains("cod|cash|pay on delivery|pod")
            | (pm == "cash on delivery")
        ).astype(int)
        df["payment_method"] = pm.where(df["is_cod"] == 1, "Prepaid")
        return df
    if "b2b" in df.columns and df["b2b"].notna().any():
        # B2B=True ⇒ business order ⇒ typically prepaid; treat as is_cod=0.
        b2b = df["b2b"].astype(str).str.strip().str.lower()
        is_b2b = b2b.isin({"true", "yes", "1", "t"})
        df["is_cod"] = (~is_b2b).astype(int)
        df["payment_method"] = df["is_cod"].map({1: "COD", 0: "Prepaid"})
        return df
    # No signal — assume COD per spec (the RTO problem is COD-only).
    df["is_cod"] = 1
    df["payment_method"] = "COD"
    return df


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a real Kaggle e-commerce CSV into our unified schema.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument(
        "csv",
        nargs="?",
        default=None,
        help="Path to the source CSV. If omitted, auto-detects by --source: "
        "amazon → data/raw/amazon_sale_report.csv; indian_ecom → "
        "data/raw/indian_ecom.csv; online_retail → data/raw/online_retail.csv",
    )
    ap.add_argument(
        "--source",
        choices=sorted(COLUMN_MAPS),
        default="amazon",
        help="Which Kaggle dataset shape to map columns for.",
    )
    ap.add_argument(
        "--out",
        default="data/raw/ingested_real.csv",
        help="Where to write the unified-schema CSV (consumed by "
        "`load_ingested_real` + `load_data`).",
    )
    args = ap.parse_args()

    # Resolve source CSV path (explicit > auto-detect by --source).
    if args.csv:
        path = Path(args.csv)
    else:
        auto = {
            "amazon": "data/raw/amazon_sale_report.csv",
            "indian_ecom": "data/raw/indian_ecom.csv",
            "online_retail": "data/raw/online_retail.csv",
        }[args.source]
        path = Path(auto)
    if not path.exists():
        print(
            f"ERROR: source CSV not found: {path}\n"
            f"  → download the dataset from Kaggle into data/raw/ first.\n"
            f"  → for --source amazon, the expected file is "
            f"data/raw/amazon_sale_report.csv (~129k orders, the Amazon "
            f"India Sale Report). See data/raw/README.md for instructions.",
            file=sys.stderr,
        )
        return 1

    raw = pd.read_csv(path, low_memory=False)
    mapped = normalize_labels(map_columns(raw, args.source))
    mapped = infer_is_cod(mapped)

    # Light sanity: warn if is_returned has zero positive class.
    n_rows = len(mapped)
    n_returned = int(mapped["is_returned"].sum())
    return_rate = n_returned / max(n_rows, 1)
    if n_returned == 0:
        print(
            "[warn] 0 Returned rows in the dataset — downstream model can't train.",
            file=sys.stderr,
        )

    # Persist the unified-schema CSV.
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mapped.to_csv(out_path, index=False)
    source_label = {
        "amazon": "Amazon India Sale Report",
        "indian_ecom": "Indian E-commerce Dataset",
        "online_retail": "Online Retail (UCI)",
    }[args.source]
    print(
        f"Ingested {n_rows:,} orders from {source_label}. "
        f"{n_returned:,} returned ({return_rate:.1%}). Written to {out_path}. "
        f"Next: run `python scripts/retrain_real.py`"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
