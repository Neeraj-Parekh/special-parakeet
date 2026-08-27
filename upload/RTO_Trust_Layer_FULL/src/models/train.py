from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ORDER_FEATURES = {
    "numeric": [
        "log_order_value",
        "discount_pct",
        "Items",
        "OrderDay",
        "OrderHour",
        "PriorOrders",
        "PriorReturns",
        "is_cod",
    ],
    "categorical": ["category", "device", "city_tier"],
}

ADDR_FEATURES = {"numeric": [], "categorical": ["address_quality"]}
GEO_FEATURES = {
    "numeric": ["state_offices", "state_delivery_share", "state_rural_bo_share"],
    "categorical": [],
}


def build_feature_frame(df: pd.DataFrame, feature_set: str) -> tuple[pd.DataFrame, pd.Series]:
    cols_num: list[str] = []
    cols_cat: list[str] = []

    def take(spec: dict[str, list[str]]) -> None:
        cols_num.extend([c for c in spec["numeric"] if c in df.columns])
        cols_cat.extend([c for c in spec["categorical"] if c in df.columns])

    take(ORDER_FEATURES)
    if feature_set in {"order+addr", "full"}:
        take(ADDR_FEATURES)
    if feature_set == "full":
        take(GEO_FEATURES)

    for c in cols_num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in cols_cat:
        df[c] = df[c].astype("category")
    X = df[cols_num + cols_cat]
    if "is_returned" in df.columns:
        return X, df["is_returned"]
    return X, None


def fit_model(X: pd.DataFrame, y: pd.Series, seed: int = 42):
    model = HistGradientBoostingClassifier(
        max_iter=300,
        learning_rate=0.08,
        max_depth=6,
        categorical_features="from_dtype",
        l2_regularization=1.0,
        random_state=seed,
    )
    model.fit(X, y)
    return model


def save_model(model, path: str) -> None:
    import joblib

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_model(path: str):
    import joblib

    return joblib.load(path)
