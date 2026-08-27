"""Order loading + feature derivation for the RTO Trust Layer.

Three loaders coexist:

* ``load_orders`` — original synthetic CODScore CSV (``data/raw/cod_orders.csv``).
  Backward-compat for the 93-test suite + the API lifespan that reads the
  synthetic file at startup. Untouched by Track L.
* ``load_ingested_real`` — Track L Day 4 real-data path. Reads
  ``data/raw/ingested_real.csv`` produced by ``scripts/ingest_kaggle.py``
  (the unified-schema output of the Amazon India Sale Report, the Indian
  E-commerce Dataset, or the UCI Online Retail dataset). Maps the
  ingested columns to the same feature schema as ``load_orders`` so
  ``fit_model`` + ``build_feature_frame`` work unchanged.
* ``load_data(path=None)`` — dispatcher. If ``path`` is None, prefers
  ``data/raw/ingested_real.csv`` (the real-data upgrade) and falls back
  to ``data/raw/cod_orders.csv`` (the synthetic data) when no real-data
  file exists. Used by ``scripts/retrain_real.py`` + future TFX-style
  pipeline stages so the user can drop a Kaggle CSV in and the whole
  pipeline re-runs on real data with no code change.

Feature derivation is the SAME across both loaders so the model's
``ORDER_FEATURES`` whitelist in ``src/models/train.py`` matches
identically (no schema drift between synthetic + real-data runs):
  * ``order_value_inr``  — cleaned numeric amount (Rs. prefix stripped)
  * ``log_order_value``  — np.log1p(amount) (handles skew + zeroes)
  * ``discount_pct``     — coerced to numeric (real data has no discount
                            column → 0.0; the Kandula 2021 ladder shows
                            this feature is weak on synthetic but worth
                            carrying for real data)
  * ``city_tier``         — normalised tier_N classification
  * ``state_norm``        — lowercase + alias-mapped Indian state name
  * ``is_cod``            — already inferred at ingest time (1 = COD, 0 = prepaid)
  * ``is_returned``      — already inferred at ingest time (1 = Returned, 0 = Delivered)

For the real-data loader, we also synthesise the customer-history columns
(``PriorOrders``, ``PriorReturns``, ``CustomerID``) the synthetic CSV
ships with — the Amazon India Sale Report doesn't carry a customer
identifier, so we use ``ship-state`` + ``ship-postal-code`` as a coarse
customer proxy (Track N can refine this with the real Feast feature
store once Day 4 lands). ``OrderDay`` + ``OrderHour`` are derived from
``order_date`` when present.
"""
import re
from pathlib import Path

import pandas as pd


def clean_order_value(raw: str) -> float:
    digits = re.sub(r"[^\d]", "", str(raw))
    return float(digits) if digits else float("nan")


def normalize_city_tier(raw: str) -> str:
    m = re.search(r"\d", str(raw))
    return f"tier_{m.group()}" if m else "unknown"


STATE_ALIASES = {
    "orissa": "odisha",
    "pondicherry": "puducherry",
    "uttaranchal": "uttarakhand",
    "chattisgarh": "chhattisgarh",
    "telangana": "telangana",
    "nct of delhi": "delhi",
}


def normalize_state(raw: str) -> str:
    s = re.sub(r"[^a-z ]", "", str(raw).strip().lower())
    s = STATE_ALIASES.get(s, s)
    return s.strip()


def np_log1p(s: pd.Series) -> pd.Series:
    import numpy as np

    return np.log1p(s.fillna(0))


def load_orders(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # Track L Day 4: detect the unified real-data schema (the output of
    # `scripts/ingest_kaggle.py` — written to `data/raw/ingested_real.csv`)
    # and delegate to `load_ingested_real`. This keeps the synthetic-CODScore
    # behaviour unchanged for the 93-test suite (the synthetic CSVs all
    # carry `OrderValue` so the dispatch never trips for them) while
    # letting existing callers (`scripts/cost_table.py`,
    # `scripts/slice_metrics.py`, `scripts/canary_gate.py`) work
    # unchanged on real data — just point `--data` at the ingested CSV.
    if "OrderValue" not in df.columns and "amount_inr" in df.columns:
        return load_ingested_real(path)
    df["order_value_inr"] = df["OrderValue"].map(clean_order_value)
    df["log_order_value"] = np_log1p(df["order_value_inr"])
    df["discount_pct"] = pd.to_numeric(df["DiscountPct"], errors="coerce")
    df["city_tier"] = df["CityTier"].map(normalize_city_tier)
    df["state_norm"] = df["State"].map(normalize_state)
    df["is_cod"] = (df["PaymentMethod"] == "COD").astype(int)
    df["is_returned"] = (df["DeliveryStatus"] == "Returned").astype(int)
    return df


# --------------------------------------------------------------------- #
# Real-data path (Track L Day 4)                                        #
# --------------------------------------------------------------------- #

# Columns the unified ingested CSV is expected to carry (see
# `scripts/ingest_kaggle.py` for the per-source column map). Missing
# columns are filled with neutral defaults — the downstream model handles
# them (e.g. ``PriorOrders=0`` for new customers → high-RTO bias).
INGESTED_REQUIRED = [
    "order_id", "amount_inr", "payment_method", "category", "items",
    "city", "state", "pincode", "order_date", "is_cod", "is_returned",
    "status",  # raw status string — kept for slice metrics / audit
]


def _derive_customer_history(df: pd.DataFrame) -> pd.DataFrame:
    """Synthesise `CustomerID`, `PriorOrders`, `PriorReturns` when the
    source dataset has no explicit customer key.

    The Amazon India Sale Report has no customer identifier column, so we
    use a coarse proxy — ``ship-state`` + ``ship-postal-code`` — to
    approximate repeat-customer behaviour. This is intentionally
    conservative (Track N's Feast-backed feature store will refine
    this with a real customer_id once Day 4 lands), but it gives the
    model a meaningful prior-orders signal on real data instead of the
    degenerate "all new customers" baseline.
    """
    df = df.copy()

    # CustomerID proxy: ship-state + pincode (coarse but repeatable).
    state_key = df.get("state", pd.Series([""] * len(df), index=df.index))
    pin_key = df.get("pincode", pd.Series([""] * len(df), index=df.index))
    df["CustomerID"] = (
        state_key.fillna("").astype(str).str.strip()
        + "|"
        + pin_key.fillna("").astype(str).str.strip()
    )
    # Some rows have empty pincode → treat each as a unique proxy.
    df.loc[df["CustomerID"].str.endswith("|"), "CustomerID"] = (
        df.loc[df["CustomerID"].str.endswith("|"), "CustomerID"]
        + df.loc[df["CustomerID"].str.endswith("|"), "order_id"].astype(str)
    )

    # Prior orders / returns per (proxy) customer — computed via expanding
    # counts over the order_date (or row order if no date). Each row sees
    # the count BEFORE it (the prior history), not including itself.
    sort_col = "order_date" if "order_date" in df.columns and df["order_date"].notna().any() else df.index
    try:
        df = df.sort_values(by=sort_col, kind="stable")
    except (TypeError, ValueError):
        # Mixed-type sort fails — fall back to index order.
        pass

    # Cumulative counts per customer, shifted by 1 so the current row's
    # outcome is NOT included in its own prior history (no label leakage).
    grp = df.groupby("CustomerID", sort=False).cumcount()
    df["PriorOrders"] = grp.astype(int)
    ret_so_far = (
        df.groupby("CustomerID", sort=False)["is_returned"]
        .cumsum()
        .astype(int)
        - df["is_returned"].astype(int)
    )
    df["PriorReturns"] = ret_so_far.clip(lower=0)
    return df


def _derive_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Derive `OrderDay` + `OrderHour` from `order_date` when present.

    The synthetic data ships with explicit ``OrderDay`` (1-365) +
    ``OrderHour`` (0-23) columns; the real data carries an ISO
    ``order_date`` string. We normalise to the same integer schema so
    the model's feature whitelist works unchanged.
    """
    df = df.copy()
    if "order_date" in df.columns and df["order_date"].notna().any():
        ts = pd.to_datetime(df["order_date"], errors="coerce", utc=False, dayfirst=True)
        # OrderDay: day-of-year (1-366). Year-agnostic so multi-year datasets
        # still produce a usable signal (the model treats year as a constant).
        df["OrderDay"] = ts.dt.dayofyear.fillna(0).astype(int)
        df["OrderHour"] = ts.dt.hour.fillna(0).astype(int)
    else:
        df["OrderDay"] = 0
        df["OrderHour"] = 0
    return df


def _derive_city_tier_from_state(df: pd.DataFrame) -> pd.DataFrame:
    """Map `state` to `city_tier` for the real-data path.

    The synthetic CSV ships ``CityTier`` as ``Tier-1/2/3``. The Amazon
    India Sale Report has no tier column, so we use a coarse state →
    tier heuristic for the real-data path:

      * Tier-1 metros: Maharashtra (Mumbai/Pune), Delhi, Karnataka
        (Bengaluru), Tamil Nadu (Chennai), Telangana (Hyderabad),
        West Bengal (Kolkata), Gujarat (Ahmedabad)
      * Tier-2: the next-largest urbanised states (Punjab, Kerala,
        Haryana, Rajasthan, Madhya Pradesh, Andhra Pradesh)
      * Tier-3: the rest (Bihar, Jharkhand, Odisha, Assam, NE states,
        J&K, etc.)

    This is a coarse heuristic — Track N's feature store will replace
    it with a real city-tier lookup once the India Post pincode
    directory is ingested (the `data/raw/pincodes_india.csv` file
    is already in the repo, currently only used by the dead-code
    `add_geo_features` path which Track B removed).
    """
    TIER1 = {
        "maharashtra", "delhi", "karnataka", "tamil nadu",
        "telangana", "west bengal", "gujarat",
    }
    TIER2 = {
        "punjab", "kerala", "haryana", "rajasthan",
        "madhya pradesh", "andhra pradesh", "uttar pradesh",
    }
    df = df.copy()
    if "city_tier" in df.columns and df["city_tier"].notna().any():
        df["city_tier"] = df["city_tier"].map(normalize_city_tier)
        return df
    state = df.get("state", pd.Series([""] * len(df), index=df.index))
    s_norm = state.fillna("").astype(str).str.strip().str.lower().map(normalize_state)
    df["city_tier"] = s_norm.map(
        lambda s: "tier_1" if s in TIER1 else ("tier_2" if s in TIER2 else "tier_3")
    )
    return df


def load_ingested_real(path: str = "data/raw/ingested_real.csv") -> pd.DataFrame:
    """Load + derive features from the unified-schema real-data CSV.

    The CSV is produced by ``scripts/ingest_kaggle.py`` and carries the
    columns enumerated in ``INGESTED_REQUIRED``. This function maps those
    to the same feature schema ``load_orders`` produces
    (``order_value_inr``, ``log_order_value``, ``discount_pct``,
    ``city_tier``, ``state_norm``, ``is_cod``, ``is_returned``) so
    ``fit_model`` + ``build_feature_frame`` work unchanged.

    Customer-history columns (``CustomerID``, ``PriorOrders``,
    ``PriorReturns``) are synthesised from a coarse state+pincode proxy
    when the source dataset has no explicit customer key (the Amazon
    India Sale Report doesn't). Time features (``OrderDay``,
    ``OrderHour``) are derived from ``order_date`` when present.
    """
    df = pd.read_csv(path, low_memory=False)

    # Normalise the column names that already match the unified schema
    # but may carry type noise (str → str.strip(); numeric → numeric).
    df["order_value_inr"] = df["amount_inr"].map(clean_order_value)
    df["log_order_value"] = np_log1p(df["order_value_inr"])

    # discount_pct — the Amazon dataset has no discount column; default to 0
    # (carries no signal but matches the feature whitelist).
    if "discount_pct" in df.columns:
        df["discount_pct"] = pd.to_numeric(df["discount_pct"], errors="coerce")
    else:
        df["discount_pct"] = 0.0

    # Items → integer count (the synthetic CSV uses ``Items``; the ingested
    # real CSV uses ``items`` lower-case — bridge to the canonical name).
    if "items" in df.columns and "Items" not in df.columns:
        df["Items"] = pd.to_numeric(df["items"], errors="coerce").fillna(1).astype(int)

    # Device — the real dataset has no device column; default to "unknown"
    # (the model's categorical handler treats unseen categories as NaN
    # via the from_dtype mode; "unknown" is the safe constant).
    if "device" not in df.columns:
        df["device"] = "unknown"

    # AddressQuality — the real dataset has no address-quality column;
    # default to "unknown" (same reasoning as device).
    if "AddressQuality" not in df.columns:
        df["AddressQuality"] = "unknown"

    # state_norm — normalised Indian state name (alias mapping).
    if "state" in df.columns:
        df["state_norm"] = df["state"].map(normalize_state)
    else:
        df["state_norm"] = "unknown"

    # city_tier — derived from state via coarse heuristic if not provided.
    df = _derive_city_tier_from_state(df)

    # is_cod + is_returned are already 0/1 ints from ingest_kaggle.py —
    # coerce defensively in case the CSV read them as strings.
    df["is_cod"] = pd.to_numeric(df["is_cod"], errors="coerce").fillna(1).astype(int)
    df["is_returned"] = pd.to_numeric(df["is_returned"], errors="coerce").fillna(0).astype(int)

    # Customer-history + time features (synthesised — see docstrings above).
    df = _derive_customer_history(df)
    df = _derive_time_features(df)

    # Required for `group_split` (GroupShuffleSplit on CustomerID) + the
    # `group_leakage` CI gate.
    if "CustomerID" not in df.columns:
        df["CustomerID"] = "real-customer-" + df["order_id"].astype(str)

    return df


def load_data(path: str | None = None) -> pd.DataFrame:
    """Dispatcher: prefer real-data, fall back to synthetic.

    Order of preference when ``path`` is None:
      1. ``data/raw/ingested_real.csv`` — produced by
         ``scripts/ingest_kaggle.py`` after the user downloads a Kaggle
         CSV. This is the Track L Day 4 real-data path (target PR-AUC
         ≥ 0.72, Kandula 2021 DSS benchmark).
      2. ``data/raw/cod_orders.csv`` — the synthetic CODScore dataset
         shipped with the repo. Used by the 93-test suite + the API
         lifespan; preserved as the backward-compat fallback so the
         project still works out-of-the-box before the user downloads
         the Kaggle data.

    Callers may pass an explicit ``path`` to override (e.g.
    ``scripts/retrain_real.py --data data/raw/ingested_real.csv``).
    """
    if path is not None:
        if path.endswith("ingested_real.csv"):
            return load_ingested_real(path)
        return load_orders(path)

    real = Path("data/raw/ingested_real.csv")
    if real.exists():
        return load_ingested_real(str(real))
    return load_orders("data/raw/cod_orders.csv")
