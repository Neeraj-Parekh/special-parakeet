from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # `pandas` is a runtime dep (see requirements.txt / pyproject.toml) so it's
    # always present when this module is imported by the API or by tests. The
    # import is kept under `TYPE_CHECKING` to make the annotation-only usage
    # explicit and avoid surprising importers who pass in duck-typed frames.
    import pandas as pd


def add_address_features(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the `address_quality` column from the raw `AddressQuality` field.

    Called from `src.api.routes` lifespan at startup so the column exists by
    the time the first `/v1/risk/score` request lands. Keep the produced column
    name in sync with `src/models/train.py::build_feature_frame` which lists
    `address_quality` in its feature whitelist.
    """
    df = df.copy()
    df["address_quality"] = df["AddressQuality"].astype(str).str.strip().str.lower()
    return df


# Geo features (pincode POI/amenity counts per Kandula 2021 paper) are a future
# enhancement. Removed `add_geo_features` / `state_infrastructure` as dead code
# — there is no `data/raw/pincodes_india.csv` in the repo, the API lifespan
# never calls them (only `add_address_features` is wired in at routes.py:72),
# and the only nominal caller (`scripts/evaluate.py --feature-set full`) cannot
# function without the CSV anyway. See `docs/ARCHITECTURE.md §future-work` for
# the planned re-introduction path:
#   1. ingest Indian pincodes CSV (geonames.gov.in or similar)
#   2. aggregate state-level office counts + rural-BO share + delivery share
#   3. merge into the feature frame on `state_norm`
# Re-adding would also need an entry in `src/models/train.py::build_feature_frame`
# so the new columns are picked up by the HistGB model.
