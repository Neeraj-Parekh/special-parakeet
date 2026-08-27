import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.cleaning import clean_order_value, normalize_city_tier, normalize_state
from src.models.splitting import group_leakage, group_split


def test_clean_order_value():
    assert clean_order_value("Rs.866") == 866.0
    assert clean_order_value("2,426") == 2426.0
    assert clean_order_value("") != clean_order_value("")


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
