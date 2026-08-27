from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

SPLIT_KEY = "CustomerID"


def group_split(
    df: pd.DataFrame, test_size: float = 0.2, seed: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame]:
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    train_idx, test_idx = next(splitter.split(df, groups=df[SPLIT_KEY]))
    return df.iloc[train_idx].copy(), df.iloc[test_idx].copy()


def group_leakage(train_df: pd.DataFrame, test_df: pd.DataFrame) -> int:
    return len(set(train_df[SPLIT_KEY]) & set(test_df[SPLIT_KEY]))


def encode_categoricals(
    train_df: pd.DataFrame, test_df: pd.DataFrame, cat_cols: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    tr = train_df.copy()
    te = test_df.copy()
    cats: dict[str, pd.Categorical] = {}
    for c in cat_cols:
        cats[c] = pd.Categorical(sorted(set(tr[c].astype(str)) | set(te[c].astype(str))))
        tr[c] = pd.Categorical(tr[c].astype(str), categories=cats[c].categories).codes
        te[c] = pd.Categorical(te[c].astype(str), categories=cats[c].categories).codes
        tr[c] = tr[c].replace(-1, np.nan)
        te[c] = te[c].replace(-1, np.nan)
    return tr, te, cat_cols
