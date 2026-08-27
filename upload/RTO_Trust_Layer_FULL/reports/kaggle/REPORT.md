# Amazon Sale Report — Pre-Training EDA (STOP before model)

Generated: 2026-08-27T16:31:53.450857+00:00
Zip: Amazon Sale Report.csv.zip — 68.9 MB uncompressed — 128975 rows, 24 cols raw

## TL;DR for trainer

- **Rows:** 128975  | **Orders unique:** 120378 (dup Order ID = multi-SKU line items, not error)
- **RTO (correct substring):** 2109 (1.64%) — statuses: ['Shipped - Returned to Seller', 'Shipped - Returning to Seller', 'Shipped - Rejected by Buyer']
- **Cancelled (separate):** 18332 (14.21%) — DO NOT conflate with RTO.
- **Wrong exact-match** correctly excludes 16223 Cancels that wrong logic flags as RTO (wrong=18332 vs true=2109).
- **Imbalance:** RTO ~1:61 → use PR-AUC.
- **Temporal:** 2022-01 → 2022-12, bulk 04-06 ( 68% of data) → **time-based split required**.
- **City/SKU high cardinality:** city 8955 / SKU 7195 → rate-encoding, not OHE.
- **Courier Status is LEAKY** (post-shipment) — exclude.
- **Amount NaN 7795 is informative:** almost all Cancelled/unpaid — don't mean-impute.

## Files produced

- `figures/*.png` — 7 distribution plots
- `schema_snapshot.json` — dtypes/nulls
- `quality_gates.json` — go/no-go checks
- `feature_blueprint.json` — ordered feature plan
- `feature_preview_1000.csv` — 1000-row preview with engineered cols

## Figures

1. Status breakdown — `figures/01_status_breakdown.png`
2. RTO by Category — `figures/02_rto_by_category.png`
3. Amount hist — `figures/03_amount_hist.png`
4. Amount by RTO — `figures/04_amount_by_rto.png`
5. Weekly orders vs RTO — `figures/05_weekly_orders_rto.png`
6. RTO by State — `figures/06_rto_by_state.png`
7. City long-tail — `figures/07_city_longtail.png`

## Next step (NOT run here)

`wrong_preprocess_amazon.py` is **correct** for this dataset (substring RTO, leak-safe expanding rates, cat-median Amount).
`wrong_train_colab.py` is **WRONG** — assumes `user_id`/`merchant_id` which don't exist here. Replace with the blueprint above.

**STOP — no model was trained in this script.**
