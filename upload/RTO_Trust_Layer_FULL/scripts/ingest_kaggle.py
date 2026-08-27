"""Ingest a real Kaggle e-commerce CSV into our schema.

Usage:
  1. Download e.g. 'Amazon Sale Report' CSV manually from Kaggle into data/raw/
  2. python scripts/ingest_kaggle.py data/raw/amazon_sale_report.csv
Column mapping handles common variants; unmapped columns are dropped loudly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COLUMN_MAP = {
    # target schema column : candidate source columns (first match wins)
    "OrderID": ["Order ID", "order_id", "Order#", "invoice_id"],
    "Amount": ["Amount", "amount", "Order Value", "order_value", "Cur. Sales Value"],
    "Category": ["Category", "category", "Product Category"],
    "Status": ["Status", "status", "Delivery Status", "Order Status"],
    "State": ["ship-state", "ship_state", "State", "state"],
    "City": ["ship-city", "ship_city", "City"],
    "Pincode": ["ship-postal-code", "postal-code", "Pincode", "pincode"],
    "PaymentMethod": ["Payment Method", "payment_method", "Fulfilment"],
    "Date": ["Date", "date", "Order Date"],
}

STATUS_LABEL_MAP = {
    "returned": "Returned",
    "return to seller": "Returned",
    "returned to seller": "Returned",
    "shipped - returned to seller": "Returned",
    "rto": "Returned",
    "rejected by buyer": "Returned",
    "cancelled": "Returned",
    "undeliverable": "Returned",
}


def map_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for target, candidates in COLUMN_MAP.items():
        for c in candidates:
            if c in df.columns:
                out[target] = df[c]
                break
        else:
            print(f"[warn] no source for '{target}' - filling nulls")
            out[target] = pd.NA
    return out


def normalize_labels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    status_norm = df["Status"].astype(str).str.strip().str.lower()
    df["DeliveryStatus"] = status_norm.map(
        lambda s: STATUS_LABEL_MAP.get(s, "Delivered")
    )
    df["is_returned"] = (df["DeliveryStatus"] == "Returned").astype(int)
    return df


def main() -> int:
    if len(sys.argv) < 2:
        src = sorted(Path("data/raw").glob("*.csv"))
        if not src:
            print("usage: ingest_kaggle.py <csv>   (or drop csv into data/raw/)")
            return 1
        path = src[0]
    else:
        path = Path(sys.argv[1])
    raw = pd.read_csv(path, low_memory=False)
    mapped = normalize_labels(map_columns(raw))
    out_path = Path("data/raw/ingested_real.csv")
    mapped.to_csv(out_path, index=False)
    rate = mapped["is_returned"].mean()
    print(
        f"ingested {len(mapped):,} rows -> {out_path} | "
        f"return-rate={rate:.1%} | columns={list(mapped.columns)}"
    )
    print("next: extend src/features/cleaning.py load_orders to read ingested_real.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
