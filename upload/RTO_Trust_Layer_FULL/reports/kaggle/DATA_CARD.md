# Data Card — Amazon Sale Report (RTO)

**Zip:** `Amazon Sale Report.csv.zip` `6.16MB comp → 68.9MB uncomp` `00_eda_pre_training.py:75`  
**CSV:** `Amazon Sale Report.csv` `68923428 bytes` `1 file` `model_processing:1`

## Overview
Kaggle `thedevastator/unlock-profits-with-e-commerce-sales-data` — Amazon India sales **2022-01-04→2022-12-06** (bulk 68% Apr-Jun) `reports/REPORT.md:6`. Fashion (kurta/Set/Western Dress). 128975 rows → **121180 after dropna Amount (7795 NaN)** `01_preprocess:68` for RTO modeling.

## Schema (24 raw → 43 engineered)
| Col | Type | Null | Notes |
|---|---|---|---|
| index | int | 0% | row id |
| Order ID | object | 0% | nunique 120378, dup 8597 (6.6% multi-SKU orders) `00_eda:45` |
| Date | object→Date | 0% | `dayfirst=True` parsed, `Date` range `00_eda:45` |
| Status | category | 0% | 13 values: `Shipped 60%`, `Delivered 22%`, `Cancelled 14.2%`, `Returned to Seller 1.5%` etc. `00_eda:98` |
| Fulfilment | cat | 0% | `Amazon 69.5%` `Merchant 30.5%` |
| Sales Channel | cat | 0% | `Amazon.in 99.9%` |
| ship-service-level | cat | 0% | `Expedited 68% Standard 31%` |
| Style/SKU/Category/Size/ASIN | object | 0% | `SKU 7195→prefix 14 (JNE 54k, SET 34k)` `00_eda:45`, `Size M 17% L 17% XL 16%` |
| Courier Status | cat | 5.33% `6872` | `Shipped 85% Unshipped 5% Cancelled 4%` — **leaky post-shipment** |
| Qty | int16 | 0% | `mean 0.90` `Q 0:12807 (9.9%)`, `1:87%` `00_eda:45` |
| currency/Amount | float32 | 6.04% `7795` | `mean 648 median 605 min 0 max 5584` `00_eda:45` — `0:2343`, `NaN:7566 Cancelled +208 Shipped` |
| ship-city/state/postal/country | object | 0.03% `33` | `city 8955` `BENGALURU 11k HYDERABAD 8k`, `state MAHARASHTRA 22k KARNATAKA 17k`, `pincode 406 prefixes 560 13k 400 9k`, `country IN/NaN` |
| promotion-ids | object | 38.11% | `61.9%` non-null, `has_promotion 1` → `RTO 2.67%` vs `0.01%` within `Shipped` `04_tabnet:31` |
| B2B | bool | 0% | `True 871 (0.67%)` |
| fulfilled-by | object | 69.55% | `Easy Ship` vs `NaN` |
| Unnamed:22 | float | 38% `49050` | `junk 79925 False` dropped `00_eda:45` |

## Label
`rto = Status.lower contains return|rejected|rto|refused|returned to seller` `01:52` → **2109** `is_rto` `is_cancelled` `status contains cancelled` `01:53`. `is_cancelled 18332` separate. No overlap.

## Splits
`processed/train_processed.csv 96944 25MB rto 0.01697` `test 24236 6.2MB rto 0.01898` `processed/schema.json:4` `time_split 01:133` (not random).

## Engineered (leak-safe)
`sku_prefix`, `pincode_prefix 3-digit 406`, `amount_log`, `is_high_value >5000`, `amount_zscore_by_category (train cat_mean/std)` `01:99`, `amount_ratio_to_cat_median`, `pincode_length`, `amount_per_qty`, `has_promotion`, `is_qty_zero`, `pincode_region` first digit, `cat_has_promo`, `amount_x_promo`, `category/state/city/pincode_prefix/sku_prefix/fulfilment_rto_rate` expanding `shift(1)` `01:176`, `smooth m=20` `06:68`, `Size` (from zip `06:60`), `month_start_x_promo`.

## Quality
- Duplicates: `Order ID dup 8597` = multi-SKU, `full-row dup 0`
- Date: 11 months, bulk Apr-Jun 68% → seasonality
- Amount: `0` mostly `Shipped 1518`, `NaN` mostly `Cancelled 7566`
- City long-tail: top5 `28.4%` `00_eda:45` → rate-encode
- Gates: PASS `quality_gates.json:1` (except courier leak flagged)

## Usage
For RTO only (`rto`), optionally exclude cancellations `--exclude-cancellations` `01:52`. Use `train_stats.json` (`cat_mean/std/median`, `amount_bins`) for test `01:99`. **Do not** use `Courier Status` raw at inference (always `UNKNOWN`).

## Preprocessing Code
`01_preprocess_amazon_oom_safe.py:1` (junk drop, downcast `int16/float32/category`, `mem 107MB` `00_eda:45`, `psutil` guard, `chunksize 20k`).

## Files
`processed/*`, `reports/figures/*.png`, `reports/schema_snapshot.json`, `reports/quality_gates.json`, `reports/feature_preview_1000.csv` (1000 rows preview `00_eda:45`).

---
*EDA mem est 189MB `00_eda:45`, avail 8.8GB, no OOM.*
