from __future__ import annotations

import pandas as pd

from src.features.cleaning import normalize_state


def add_address_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["address_quality"] = df["AddressQuality"].astype(str).str.strip().str.lower()
    return df


def state_infrastructure(pincode_csv: str) -> pd.DataFrame:
    pin = pd.read_csv(pincode_csv, dtype=str)
    pin["state_norm"] = pin["StateName"].map(normalize_state)
    pin["is_delivery"] = (pin["Delivery"].str.strip().str.lower() == "delivery").astype(int)
    pin["is_bo"] = (pin["OfficeType"].str.strip().str.upper() == "BO").astype(int)
    g = pin.groupby("state_norm").agg(
        state_offices=("Pincode", "count"),
        state_delivery_share=("is_delivery", "mean"),
        state_rural_bo_share=("is_bo", "mean"),
    )
    return g.reset_index()


def add_geo_features(df: pd.DataFrame, pincode_csv: str) -> pd.DataFrame:
    df = df.copy()
    infra = state_infrastructure(pincode_csv)
    df = df.merge(infra, on="state_norm", how="left")
    for c in ["state_offices", "state_delivery_share", "state_rural_bo_share"]:
        df[c] = df[c].fillna(df[c].median())
    return df
