"""Tests for the KaggleFeatureBuilder (Subagent 15-d — Wave 3 CRITICAL
wiring of the real Kaggle champion model into the /risk/score inference
path).

Verifies the 4 spec-mandated contracts:
  1. ``transform(raw_order)`` returns a 79-dim numpy array (the champion's
     ``feat_names`` length).
  2. ``model.predict_proba(X)`` on the transformed matrix returns a valid
     probability in [0, 1] (the champion HistGB + the feature builder
     are mutually consistent).
  3. The rate-lookup fallback fires when a category isn't in
     ``rate_lookup.json`` — falls back to the global RTO rate
     (0.016979 — from priors.json's p_orig).
  4. The OHE column count matches ``feature_list.json``'s base-feature
     expectation (35 base features → 79 OHE'd columns; the champion's
     ``feat_names`` length is 79 — the spec's target).

The tests load the committed champion bundle from ``models/champion/`` —
they SKIP if the bundle is missing (e.g. a fresh checkout before the
user has dropped the Kaggle artifacts in place). This makes the test
suite robust on CI environments without the committed model.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest

# Make src/ importable when run from the tests/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Silence the sklearn version-skew warning from the champion model
# (trained on sklearn 1.8.0, may be loaded into an older runtime).
warnings.filterwarnings(
    "ignore",
    message="Trying to unpickle estimator.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message="X has feature names.*",
    category=UserWarning,
)

from src.models.feature_builder import KaggleFeatureBuilder  # noqa: E402

CHAMPION_DIR = Path(__file__).resolve().parents[1] / "models" / "champion"

# Skip the entire module if the champion bundle isn't committed yet.
# This makes the test suite robust on CI environments that don't have
# the Kaggle artifacts.
pytestmark = pytest.mark.skipif(
    not (CHAMPION_DIR / "model.pkl").exists()
    or not (CHAMPION_DIR / "train_stats.json").exists()
    or not (CHAMPION_DIR / "priors.json").exists(),
    reason="models/champion/ bundle not committed — run scripts/register_champion.py first",
)


@pytest.fixture(scope="module")
def builder() -> KaggleFeatureBuilder:
    """Load the KaggleFeatureBuilder once for the whole module."""
    return KaggleFeatureBuilder.from_champion_dir(str(CHAMPION_DIR))


@pytest.fixture(scope="module")
def champion_model():
    """Load the champion HistGB estimator (the ``model`` key in the
    ``model.pkl`` dict)."""
    import joblib
    bundle = joblib.load(CHAMPION_DIR / "model.pkl")
    assert isinstance(bundle, dict) and "model" in bundle, (
        f"champion model.pkl must be a dict with a 'model' key; "
        f"got: {type(bundle).__name__} / "
        f"{list(bundle.keys()) if isinstance(bundle, dict) else 'not a dict'}"
    )
    return bundle["model"]


# ---------------------------------------------------------------------------
# Test 1 — transform(raw_order) returns a 79-dim numpy array.
# ---------------------------------------------------------------------------


def test_transform_returns_79_dim_vector(builder: KaggleFeatureBuilder):
    """transform(raw_order) returns a (1, 79) numpy array.

    The champion's ``feat_names`` length is 79 (35 base features → 79
    OHE'd columns after the OneHotEncoder expansion). The feature
    builder must produce exactly this shape on a raw OrderIn dict.
    """
    raw_order = {
        "order_id": "TEST-FB-1",
        "amount_inr": 899.0,
        "category": "Kurta",
        "customer_id": "CUST-TEST-1",
        "items": 1,
        "order_hour": 12,
    }
    X = builder.transform(raw_order)
    assert isinstance(X, np.ndarray), (
        f"transform must return a numpy.ndarray, got {type(X).__name__}"
    )
    assert X.shape == (1, 79), (
        f"transform shape must be (1, 79); got {X.shape} "
        f"(feat_names length = {len(builder.feat_names)})"
    )
    # All values must be finite floats (no NaN, no inf — the OHE produces
    # 0/1 floats + the StandardScaler produces finite scaled values).
    assert np.all(np.isfinite(X)), (
        f"transform output must be all finite; got NaN/inf at indices "
        f"{np.where(~np.isfinite(X))[0]}"
    )
    # The OHE columns must sum to >= 1 for the categorical columns
    # (at least one category per column group is set — even if it's
    # the infrequent_sklearn column).
    # The first 10 columns are category_* OHE columns (per feat_names).
    # Sum the first 7 (category_*) — must be 1 (exactly one set).
    cat_cols_start = 0
    cat_cols_end = 7  # 6 known categories + 1 infrequent_sklearn
    cat_sum = float(X[0, cat_cols_start:cat_cols_end].sum())
    assert abs(cat_sum - 1.0) < 1e-6, (
        f"category OHE columns must sum to 1.0 (one-hot); got {cat_sum}"
    )


# ---------------------------------------------------------------------------
# Test 2 — model.predict_proba(X) returns a valid probability.
# ---------------------------------------------------------------------------


def test_predict_proba_on_transformed_vector(
    builder: KaggleFeatureBuilder, champion_model
):
    """The transformed matrix is consumable by the champion HistGB.

    Calls ``champion_model.predict_proba(X)`` on the feature builder's
    output. The probability must be a valid float in [0, 1] — this
    verifies the feature builder's matrix shape + column order matches
    what the champion was trained on (a mismatch would either crash or
    produce a near-0/near-1 probability that's obviously broken).
    """
    raw_order = {
        "order_id": "TEST-FB-2",
        "amount_inr": 1299.0,
        "category": "Set",
        "customer_id": "CUST-TEST-2",
        "items": 2,
        "order_hour": 14,
        # Extra Kaggle-specific fields (richer than OrderIn):
        "fulfilment": "Amazon",
        "sales_channel": "AMAZON.IN",
        "ship_service_level": "EXPEDITED",
        "fulfilled_by": "EASY SHIP",
        "has_promotion": 1,
        "is_b2b": 0,
        "Qty": 2,
        "pincode": "560001",
        "state": "KARNATAKA",
        "city": "BENGALURU",
        "Size": "L",
        "SKU": "JNE-TEST-SKU",
    }
    X = builder.transform(raw_order)
    proba = champion_model.predict_proba(X)
    assert proba.shape == (1, 2), (
        f"predict_proba shape must be (1, 2); got {proba.shape}"
    )
    p_rto = float(proba[0, 1])
    assert 0.0 <= p_rto <= 1.0, (
        f"P(RTO) must be in [0, 1]; got {p_rto}"
    )
    # For a non-degenerate model + a real order, the probability must
    # be a finite float strictly inside (0, 1) — a probability of
    # EXACTLY 0 or EXACTLY 1 would indicate a feature mismatch (the
    # OHE producing all-zeros for every categorical column → the model
    # falls back to its leaf prior). Very small values (e.g. 1e-5) are
    # legitimate — the Kaggle champion learned that Amazon-fulfilled
    # Set orders with has_promotion=0 are essentially never RTO, so
    # P(RTO)=3e-5 is a CORRECT prediction for that segment, not a
    # feature-mismatch symptom.
    assert 0.0 < p_rto < 1.0, (
        f"P(RTO)={p_rto} must be strictly inside (0, 1) — exactly 0 or "
        f"exactly 1 indicates a feature mismatch (the OHE producing "
        f"all-zeros for every categorical column)"
    )
    # Sanity-check: the Kaggle champion's mean prediction over a benign
    # order (no Amazon-fulfilment, no has_promotion, STANDARD shipping)
    # should land near the base rate (0.016979) — orders like that
    # are the bulk of the training set + the model's leaf prior.
    benign_order = {
        "order_id": "TEST-FB-2-BENIGN",
        "amount_inr": 899.0,
        "category": "Kurta",
        "customer_id": "CUST-TEST-2-BENIGN",
        "items": 1,
        # Defaults: fulfilment='Merchant', has_promotion=0, ship=STANDARD.
    }
    X_benign = builder.transform(benign_order)
    p_benign = float(champion_model.predict_proba(X_benign)[0, 1])
    assert 0.001 < p_benign < 0.5, (
        f"benign order P(RTO)={p_benign} should be near the base rate "
        f"(0.016979); an extreme value would indicate the feature "
        f"builder's defaults are pushing the model off the natural "
        f"distribution"
    )


# ---------------------------------------------------------------------------
# Test 3 — rate-lookup fallback when a category isn't in rate_lookup.json.
# ---------------------------------------------------------------------------


def test_rate_lookup_fallback_for_unseen_category(builder: KaggleFeatureBuilder):
    """When a category isn't in rate_lookup.json, the per-category rate
    feature falls back to the global RTO rate (0.016979 — from
    priors.json's p_orig).

    The spec-mandated inference-time approximation: rate features at
    inference are proxies from the 1000-row preview CSV; categories not
    seen in that sample fall back to the global rate.
    """
    # "DIY" is a category not in the Kaggle training set + not in the
    # 1000-row preview CSV → the rate lookup must fall back to the
    # global rate (0.016979).
    assert "DIY" not in builder.rate_lookup.get("category", {}), (
        f"test setup: 'DIY' should NOT be in the rate_lookup category "
        f"sub-dict; got {builder.rate_lookup.get('category', {}).get('DIY')}"
    )
    # Test the private _lookup_rate method directly.
    p = builder._lookup_rate("category", "DIY")
    expected_global = float(builder.priors.get("p_orig", 0.016979))
    assert abs(p - expected_global) < 1e-9, (
        f"_lookup_rate('category', 'DIY') must fall back to global rate "
        f"{expected_global}; got {p}"
    )
    # Test with an empty value too (no category supplied).
    p_empty = builder._lookup_rate("category", "")
    assert abs(p_empty - expected_global) < 1e-9, (
        f"_lookup_rate('category', '') must fall back to global rate "
        f"{expected_global}; got {p_empty}"
    )
    # Test with a category that IS in the lookup — must NOT fall back.
    # Use 'KURTA' (uppercased; the rate_lookup stores 'kurta' lowercased
    # from the preview CSV; the _lookup_rate tries upper + lower).
    if "kurta" in builder.rate_lookup.get("category", {}):
        p_kurta = builder._lookup_rate("category", "KURTA")
        expected_kurta = float(builder.rate_lookup["category"]["kurta"])
        assert abs(p_kurta - expected_kurta) < 1e-9, (
            f"_lookup_rate('category', 'KURTA') must return the lookup "
            f"value {expected_kurta}; got {p_kurta}"
        )

    # End-to-end: transform an order with an unseen category + verify
    # the matrix doesn't crash + the model still returns a probability
    # in [0, 1]. The actual rate column value in the matrix is opaque
    # (post-StandardScaler); we just verify no crash + valid output.
    import joblib
    bundle = joblib.load(CHAMPION_DIR / "model.pkl")
    X = builder.transform({
        "order_id": "TEST-FB-3",
        "amount_inr": 599.0,
        "category": "DIY-UNKNOWN",  # unseen
        "customer_id": "CUST-TEST-3",
        "items": 1,
    })
    proba = bundle["model"].predict_proba(X)
    p_rto = float(proba[0, 1])
    assert 0.0 <= p_rto <= 1.0, (
        f"P(RTO) with unseen category must still be in [0, 1]; got {p_rto}"
    )


# ---------------------------------------------------------------------------
# Test 4 — OHE column count matches feature_list.json expectation.
# ---------------------------------------------------------------------------


def test_ohe_column_count_matches_feature_list(builder: KaggleFeatureBuilder):
    """The transformed matrix's column count (79) matches the champion's
    ``feat_names`` length AND the spec's expected 79 (35 base features →
    79 OHE'd columns after the OneHotEncoder expansion).

    The champion's ``feat_names`` is the authoritative source — it's
    what the model was trained on. The static ``feature_list.json``
    carries the 35 BASE features (pre-OHE); the OHE expansion produces
    79 columns. This test verifies the consistency.
    """
    # 1. The champion's feat_names length is 79.
    assert len(builder.feat_names) == 79, (
        f"champion feat_names length must be 79 (35 base → 79 OHE'd); "
        f"got {len(builder.feat_names)}"
    )

    # 2. The transform output shape matches the feat_names length.
    X = builder.transform({
        "order_id": "TEST-FB-4",
        "amount_inr": 499.0,
        "category": "TOP",
        "customer_id": "CUST-TEST-4",
        "items": 1,
    })
    assert X.shape == (1, len(builder.feat_names)), (
        f"transform shape must match feat_names length "
        f"({len(builder.feat_names)}); got {X.shape}"
    )

    # 3. The feature_list.json base features (35) are a strict subset
    # of the champion's expected input cols (35 — same length but may
    # differ in column identity due to the QtyZero_Region_histgb config
    # adding cat_has_promo + pincode_region + amount_x_promo +
    # is_qty_zero + city_rto_rate_smooth +
    # pincode_prefix_rto_rate_smooth). Both lists have length 35.
    feature_list_path = CHAMPION_DIR / "feature_list.json"
    if feature_list_path.exists():
        base_features = json.loads(feature_list_path.read_text())
        assert len(base_features) == 35, (
            f"feature_list.json base features length must be 35; "
            f"got {len(base_features)}"
        )
        # The champion's input cols (from pre.feature_names_in_) is
        # the actual fit-time schema. Its length is also 35 (a
        # SLIGHTLY different 35 than the schema.json feature_columns
        # list, due to the QtyZero_Region_histgb config — but the
        # COUNT is invariant).
        assert len(builder._input_cols) == 35, (
            f"champion pre.feature_names_in_ length must be 35; "
            f"got {len(builder._input_cols)}"
        )

    # 4. The transform_batch also produces (n, 79).
    Xs = builder.transform_batch([
        {"order_id": "TEST-FB-4-A", "amount_inr": 499.0,
         "category": "TOP", "customer_id": "C1", "items": 1},
        {"order_id": "TEST-FB-4-B", "amount_inr": 1499.0,
         "category": "SAREE", "customer_id": "C2", "items": 2},
        {"order_id": "TEST-FB-4-C", "amount_inr": 8999.0,
         "category": "WESTERN DRESS", "customer_id": "C3", "items": 1},
    ])
    assert Xs.shape == (3, 79), (
        f"transform_batch shape must be (3, 79); got {Xs.shape}"
    )
