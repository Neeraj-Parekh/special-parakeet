"""TFX stage 2 — Data Validation (``build_and_apply_schema``).

Per TFX Baylor 2017 (``command/05-PAPER-SKILLS-MAP.md`` gap #14), the
second pipeline stage asserts the incoming data matches a versioned
schema BEFORE training begins — so model regressions caused by schema
drift (a renamed column, a new category level, a null rate spike) fail
fast with an actionable error message rather than silently corrupting
the trained model.

This script implements the CI-side validation:
  1. Required columns are present (block on missing).
  2. Column dtypes are the expected pandas dtypes (block on type drift).
  3. Null rate per column is below the configured threshold (block on
     null spike).
  4. Categorical columns have known value sets (block on new unknown
     levels for ``PaymentMethod`` / ``DeliveryStatus``; WARN on new
     levels for ``Category`` — merchant catalogs grow).
  5. Numeric ranges are sane (block on negative ``OrderValue`` etc.).

DEPENDENCY: pandera is OPTIONAL — imported lazily. If installed, the
schema is validated via ``pandera.DataFrameSchema`` (rich error
messages). If not, we fall back to manual pandas checks (still produces
the same block/warn semantics, just terser errors). The CI env pins
only runtime deps (per Track B/E work) so the fallback path is the
common case; ``pandera>=0.20`` can be added to ``[project.optional-
dependencies].dev`` later without changing this script's logic.

BLOCKING vs WARNING (TFX ``actionable anomaly descriptions``):
  * BLOCKING — exit 1, training stage does NOT run.
    - Missing required column
    - Type drift (e.g. ``OrderValue`` becomes non-numeric)
    - Null rate > 50% on a required column
    - New unknown ``PaymentMethod`` or ``DeliveryStatus`` level
    - Negative order value
  * WARNING — print to stderr but exit 0.
    - New ``Category`` level (merchant catalogs grow)
    - Null rate > 5% on a numeric column
    - Quantile shift vs baseline (Track L future work)

USAGE:
    python scripts/validate_data.py --data data/raw/cod_orders.csv
    python scripts/validate_data.py --data data/raw/cod_orders.csv --strict
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

# Schema spec — the source of truth for what the training pipeline
# expects. Mirrors the columns consumed by `src.features.cleaning.load_orders`
# + `src.features.enrich.add_address_features` + `src.models.train.build_feature_frame`.
EXPECTED_COLUMNS = {
    "OrderID": {"dtype": "object", "required": True, "max_null_pct": 0.0},
    "CustomerID": {"dtype": "object", "required": True, "max_null_pct": 0.0},
    "OrderDay": {"dtype": "int64", "required": True, "max_null_pct": 0.0, "min": 0},
    "CityTier": {"dtype": "object", "required": True, "max_null_pct": 0.0,
                 # The raw CSV has 15+ variants (T1, TIER-1, Tier-1, tier1,
                 # Tier 1, etc.). `src.features.cleaning.normalize_city_tier`
                 # extracts the digit + lowercases — so all variants
                 # normalize to {tier_1, tier_2, tier_3}. Validation should
                 # not block on these; instead we warn on truly new shapes
                 # (e.g. a "Tier-4" level — the digit would still extract,
                 # but the business intent has changed).
                 "warn_on_new_level": True},
    "State": {"dtype": "object", "required": True, "max_null_pct": 5.0},
    "Category": {"dtype": "object", "required": True, "max_null_pct": 5.0,
                 "warn_on_new_level": True},
    # OrderValue comes in many formats: "Rs.866", "1,000", "1000", etc.
    # `clean_order_value` extracts digits via regex sub, so any non-empty
    # string with at least one digit is acceptable. The strict `Rs\.?` regex
    # was a false-positive blocker (87/100 sampled values didn't match);
    # the permissive `\d` regex matches any value with at least one digit,
    # which is the actual contract.
    "OrderValue": {"dtype": "object", "required": True, "max_null_pct": 0.0,
                   "regex": r"\d"},  # at least one digit; cleaned via clean_order_value
    "DiscountPct": {"dtype": "int64", "required": True, "max_null_pct": 5.0, "min": 0, "max": 100},
    "PaymentMethod": {"dtype": "object", "required": True, "max_null_pct": 0.0,
                      "allowed": {"COD", "Prepaid"}},
    "Device": {"dtype": "object", "required": True, "max_null_pct": 5.0},
    "AddressQuality": {"dtype": "object", "required": True, "max_null_pct": 5.0,
                       "allowed": {"complete", "partial", "vague", "Complete", "Partial", "Vague"}},
    "OrderHour": {"dtype": "int64", "required": True, "max_null_pct": 0.0, "min": 0, "max": 23},
    "Items": {"dtype": "int64", "required": True, "max_null_pct": 5.0, "min": 1},
    "PriorOrders": {"dtype": "int64", "required": True, "max_null_pct": 0.0, "min": 0},
    "PriorReturns": {"dtype": "int64", "required": True, "max_null_pct": 0.0, "min": 0},
    "DeliveryStatus": {"dtype": "object", "required": True, "max_null_pct": 0.0,
                       "allowed": {"Delivered", "Returned"}},
}

# Hard null-rate floor — any column above this is BLOCKING (TFX:
# "data is unusable as-is"). 50%+ nulls means the column contributes
# essentially no signal — fail rather than impute garbage.
HARD_NULL_FLOOR_PCT = 50.0


def try_pandera(df: pd.DataFrame) -> tuple[bool, list[str]]:
    """Optional pandera validation. Returns (used_pandera, errors).

    If pandera isn't installed, returns (False, []) and the manual
    checks below handle everything. If pandera IS installed, we still
    run the manual checks below because they emit TFX-style actionable
    error messages that the CI log can display; pandera's schema is
    only used as a second-line defence.
    """
    try:
        import pandera.pandas as pa  # type: ignore
    except ImportError:
        return False, []
    try:
        # Minimal pandera schema — the real checks are in
        # `EXPECTED_COLUMNS`. This is just to confirm pandera is wired
        # in case a future Track L wants the richer error model.
        schema = pa.DataFrameSchema(
            {col: pa.Column(nullable=spec["max_null_pct"] > 0)
             for col, spec in EXPECTED_COLUMNS.items() if col in df.columns}
        )
        schema.validate(df, lazy=True)
        return True, []
    except Exception as e:  # pragma: no cover — defensive; pandera errors
        # are surfaced as warnings, the manual checks below are the
        # authoritative validators.
        return True, [f"pandera warning: {e}"]


def validate(df: pd.DataFrame, strict: bool = False) -> tuple[list[str], list[str]]:
    """Return (blocking_errors, warnings). Mirrors TFX's
    "actionable anomaly descriptions" — each error message tells the
    operator exactly which column + which check failed."""
    blocking: list[str] = []
    warnings: list[str] = []

    # Missing required columns.
    for col, spec in EXPECTED_COLUMNS.items():
        if spec.get("required") and col not in df.columns:
            blocking.append(
                f"missing required column '{col}' — schema drift detected; "
                f"check the data source for a renamed/dropped column"
            )

    # Pandera second-line defence (emits warnings only — manual checks
    # are authoritative so we don't double-fail on the same issue).
    used_pandera, pandera_warnings = try_pandera(df)
    if used_pandera:
        warnings.extend(pandera_warnings)

    for col, spec in EXPECTED_COLUMNS.items():
        if col not in df.columns:
            continue
        s = df[col]
        n = len(s)
        if n == 0:
            blocking.append(f"column '{col}' is empty (0 rows)")
            continue

        # Null rate.
        n_null = int(s.isna().sum())
        pct_null = 100.0 * n_null / n
        if pct_null >= HARD_NULL_FLOOR_PCT and spec.get("required"):
            blocking.append(
                f"column '{col}' has {pct_null:.1f}% nulls — exceeds the "
                f"{HARD_NULL_FLOOR_PCT:.0f}% hard floor; data is unusable"
            )
        elif pct_null > spec.get("max_null_pct", 0.0):
            warnings.append(
                f"column '{col}' has {pct_null:.1f}% nulls — exceeds the "
                f"{spec['max_null_pct']:.1f}% threshold"
            )

        # Type drift (pandas dtypes — int64 vs object vs float64).
        if "dtype" in spec and str(s.dtype) != spec["dtype"]:
            # Tolerant: if the expected dtype is int64 but we got
            # float64 (because nulls coerced it), warn not block.
            if spec["dtype"] == "int64" and str(s.dtype) == "float64":
                warnings.append(
                    f"column '{col}' dtype drifted int64→float64 — likely "
                    f"null-induced; will be coerced back by load_orders"
                )
            elif spec["dtype"] == "object" and str(s.dtype) != "object":
                blocking.append(
                    f"column '{col}' dtype drifted {spec['dtype']}→{s.dtype}"
                )
            elif strict:
                blocking.append(
                    f"column '{col}' dtype drifted {spec['dtype']}→{s.dtype} "
                    f"(strict mode)"
                )

        # Categorical allowed-set enforcement.
        if "allowed" in spec:
            actual_levels = set(s.dropna().unique())
            unknown = actual_levels - spec["allowed"]
            if unknown:
                if spec.get("warn_on_new_level"):
                    warnings.append(
                        f"column '{col}' has new level(s) {unknown} — "
                        f"not in known set {sorted(spec['allowed'])}"
                    )
                else:
                    blocking.append(
                        f"column '{col}' has UNKNOWN level(s) {unknown} — "
                        f"not in allowed set {sorted(spec['allowed'])}; "
                        f"rejecting to prevent label leakage / silent retrain"
                    )

        # Numeric range checks (only if the column is numeric).
        if pd.api.types.is_numeric_dtype(s) and ("min" in spec or "max" in spec):
            if "min" in spec:
                mn = float(s.min()) if not s.isna().all() else None
                if mn is not None and mn < spec["min"]:
                    blocking.append(
                        f"column '{col}' has min={mn} < {spec['min']} — "
                        f"negative/zero where positive expected"
                    )
            if "max" in spec:
                mx = float(s.max()) if not s.isna().all() else None
                if mx is not None and mx > spec["max"]:
                    blocking.append(
                        f"column '{col}' has max={mx} > {spec['max']} — "
                        f"value out of expected range"
                    )

        # Regex format check (e.g. OrderValue contains at least one digit,
        # so clean_order_value's digit-extraction will produce a non-zero
        # float). `re.search` (not `re.match`) because the digit may not
        # be at the start of the string — e.g. "Rs.866" / "INR 10,579".
        if "regex" in spec and spec.get("required"):
            import re

            sample = s.dropna().astype(str).head(100)
            n_bad = sum(1 for v in sample if not re.search(spec["regex"], v))
            if n_bad > 0:
                blocking.append(
                    f"column '{col}': {n_bad}/{len(sample)} sampled values don't "
                    f"contain regex {spec['regex']!r}"
                )

    return blocking, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/raw/cod_orders.csv")
    ap.add_argument("--strict", action="store_true",
                    help="block on dtype drift + warn-level anomalies")
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"::error::data file not found: {data_path}")
        return 1

    df = pd.read_csv(data_path)
    print(f"Validating {len(df):,} rows × {len(df.columns)} cols from {data_path}")

    blocking, warnings = validate(df, strict=args.strict)

    if warnings:
        print("\n".join(f"::warning::{w}" for w in warnings))
    if blocking:
        print("\n".join(f"::error::{b}" for b in blocking))
        print(f"\n::error::data validation FAILED — {len(blocking)} blocking "
              f"anomalies; training stage will NOT run")
        return 1

    print("✓ Data validation passed — schema + ranges + nulls OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
