import re

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


def load_orders(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["order_value_inr"] = df["OrderValue"].map(clean_order_value)
    df["log_order_value"] = np_log1p(df["order_value_inr"])
    df["discount_pct"] = pd.to_numeric(df["DiscountPct"], errors="coerce")
    df["city_tier"] = df["CityTier"].map(normalize_city_tier)
    df["state_norm"] = df["State"].map(normalize_state)
    df["is_cod"] = (df["PaymentMethod"] == "COD").astype(int)
    df["is_returned"] = (df["DeliveryStatus"] == "Returned").astype(int)
    return df


def np_log1p(s: pd.Series) -> pd.Series:
    import numpy as np

    return np.log1p(s.fillna(0))
