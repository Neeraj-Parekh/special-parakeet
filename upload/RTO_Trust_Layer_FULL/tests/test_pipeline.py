import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.cleaning import clean_order_value, normalize_city_tier, normalize_state
from src.models.splitting import group_leakage, group_split


def test_clean_order_value():
    assert clean_order_value("Rs.866") == 866.0
    assert clean_order_value("2,426") == 2426.0
    # T1.3-style tautology fix (Wave 3 — Subagent 15-c). The prior
    # ``assert clean_order_value("") != clean_order_value("")`` exploited
    # IEEE 754 NaN!=NaN semantics — it ALWAYS passed (regardless of what
    # ``clean_order_value`` actually returned for the empty-string case)
    # because no Python value (None, 0, "", NaN) is equal to itself under
    # the != operator EXCEPT for the special NaN case. That meant the
    # assertion failed to verify the REAL contract: empty input → NaN
    # (the documented ``float("nan")`` sentinel so downstream
    # ``df.fillna()`` works). The fix asserts the actual NaN-ness with
    # ``math.isnan`` so the test now FAILS if the function ever returns 0,
    # None, "" or raises — it would have caught a regression where the
    # function started returning ``0.0`` for empty input (which would
    # silently corrupt the order-value feature column with fake zeros).
    empty_result = clean_order_value("")
    assert isinstance(empty_result, float), (
        f"clean_order_value('') must return a float (the NaN sentinel so "
        f"downstream pandas fillna() can clean it); got {type(empty_result)}"
    )
    assert math.isnan(empty_result), (
        f"clean_order_value('') must return NaN (the sentinel for "
        f"'no digits extracted'); got {empty_result!r}"
    )
    # The function must also be DETERMINISTIC for the same empty input —
    # a second call returns the same NaN contract (the prior != assertion
    # masked non-determinism too — a function that randomly returned
    # either NaN or None would have passed the old test). Both calls MUST
    # be NaN; if either returns non-NaN, the contract is broken.
    second_call = clean_order_value("")
    assert math.isnan(second_call), (
        f"clean_order_value('') must be deterministic — second call must "
        f"also return NaN; got {second_call!r}"
    )


def test_normalize_tier():
    assert normalize_city_tier("TIER-3") == "tier_3"
    assert normalize_city_tier("Tier 3") == "tier_3"
    assert normalize_city_tier("Tier-1") == "tier_1"


def test_normalize_state():
    assert normalize_state("ANDHRA PRADESH") == "andhra pradesh"
    assert normalize_state("Orissa") == "odisha"
    assert normalize_state("NCT of Delhi") == "delhi"


def test_group_split_no_leakage():
    import pandas as pd

    df = pd.DataFrame(
        {
            "CustomerID": [f"C{i}" for i in range(100)] * 3,
            "x": range(300),
            "is_returned": [0] * 150 + [1] * 150,
        }
    )
    tr, te = group_split(df)
    assert group_leakage(tr, te) == 0


def test_data_loads_and_label_exists(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data = root / "data/raw/cod_orders.csv"
    if not data.exists():
        pytest.skip("raw data not present")
    from src.features.cleaning import load_orders

    df = load_orders(str(data))
    assert set(df["is_returned"].unique()) == {0, 1}
    assert df["order_value_inr"].notna().all()
