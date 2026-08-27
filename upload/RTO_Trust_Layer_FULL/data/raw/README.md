# data/raw/ — Source CSVs

> User instructions for the Track L Day 4 real-data upgrade path
> (`scripts/ingest_kaggle.py` → `scripts/retrain_real.py`).

---

## Files present (shipped with the repo)

| File | Purpose |
|---|---|
| `cod_orders.csv` | Synthetic-but-realistic 7,235-row CODScore dataset (the schema-compat placeholder). Loaded by `src/api/routes.py` lifespan on every worker boot + by `scripts/evaluate.py` + `scripts/cost_table.py` (synthetic-data path). Untouched by the real-data upgrade. |
| `pincodes_india.csv` | India Post pincode directory (23 MB). Currently unused — Track B removed the dead `add_geo_features` path that read it; Track N / a future Feast feature store will reintroduce it. |

---

## Track L Day 4 — what the user adds

To upgrade from synthetic PR-AUC 0.55 → real-data PR-AUC ≥ 0.72
(Kandula 2021 DSS benchmark, DOI 10.1016/j.dss.2021.113584 — AUC 0.73-0.79
on real Indian e-commerce delivery data), download one of the Kaggle
datasets below into this folder and run the pipeline.

### Primary dataset — Amazon India Sale Report (~129k orders)

1. **Download** from Kaggle:
   - The most reliable Kaggle slug: `thedevastator/unlock-profits-with-e-commerce-market`
     (or any "Amazon Sale Report" / "Amazon India Sale Report" dataset)
   - Kaggle search URL: https://www.kaggle.com/search?q=amazon%20india%20sale%20report
   - User must be signed in to a Kaggle account; the dataset is public but
     gated behind the Kaggle auth wall.
2. **Place** the CSV at:
   ```
   data/raw/amazon_sale_report.csv
   ```
   The exact filename matters — `scripts/ingest_kaggle.py --source amazon`
   auto-detects this path. If your download has a different filename
   (e.g. `AmazonSaleReport.csv`), pass it explicitly:
   ```
   python scripts/ingest_kaggle.py path/to/your.csv --source amazon
   ```
3. **Run the pipeline**:
   ```bash
   python scripts/ingest_kaggle.py
   # → writes data/raw/ingested_real.csv (unified schema)
   # → prints "Ingested N orders from Amazon India Sale Report. X returned (Y%)."
   # → next: run `python scripts/retrain_real.py`

   python scripts/retrain_real.py
   # → loads data/raw/ingested_real.csv via load_ingested_real()
   # → group_split on CustomerID (leakage-safe; group_leakage()==0 CI gate)
   # → fit_model (HistGB, max_iter=300, lr=0.08, max_depth=6)
   # → evaluate PR-AUC + ROC-AUC + F1 + precision/recall@threshold
   # → register_model(version="real-{ts}", champion=True) if PR-AUC > incumbent
   # → regenerate docs/cost_table.md + docs/feature_importance.md
   # → exit 1 if PR-AUC < 0.60 (CI gate per mlops.yml Stage 3)
   # → print summary: "Retrained on N real orders. PR-AUC: X.XX (was Y.YY on synthetic). Champion: real-{version}."
   ```

### Alternative datasets (also supported by `ingest_kaggle.py --source`)

| `--source` flag | Expected file | Source | Notes |
|---|---|---|---|
| `amazon` (default) | `data/raw/amazon_sale_report.csv` | Kaggle, ~129k orders | Primary; no explicit COD flag — `ingest_kaggle.py` infers `is_cod` from the `B2B` flag (B2B→prepaid, B2C→COD) per the spec directive. |
| `indian_ecom` | `data/raw/indian_ecom.csv` | Kaggle, ~50k orders | Has explicit `payment_method` column; smaller but cleaner. |
| `online_retail` | `data/raw/online_retail.csv` | UCI ML Repo / Kaggle, ~541k txns | UK-based; NO RTO labels — useful only for RFM feature-engineering patterns; the downstream model can't train on it (no positive class). |

---

## Expected Amazon India Sale Report columns (verified)

The `scripts/ingest_kaggle.py` column map for `--source amazon` expects
these source columns (case-sensitive; first match wins):

| Unified-schema target | Candidate source columns (first match wins) |
|---|---|
| `order_id` | `Order ID`, `order_id`, `Order#`, `invoice_id` |
| `amount_inr` | `Amount`, `amount`, `Order Value`, `order_value`, `Cur. Sales Value` |
| `category` | `Category`, `category`, `Product Category` |
| `items` | `Qty`, `Quantity`, `items`, `qty` |
| `city` | `ship-city`, `ship_city`, `City` |
| `state` | `ship-state`, `ship_state`, `State`, `state` |
| `pincode` | `ship-postal-code`, `ship_postal_code`, `postal-code`, `Pincode`, `pincode` |
| `order_date` | `Date`, `date`, `Order Date`, `order_date` |
| `status` | `Status`, `status`, `Delivery Status`, `Order Status` |
| `fulfilment` | `Fulfilment`, `fulfilment`, `Fulfillment` |
| `b2b` | `B2B`, `b2b` |
| `ship_service_tier` | `ship-service-tier`, `ship_service_tier`, `Service Tier` |

### Status → `is_returned` mapping

The Amazon `Status` column is a free-text field. `ingest_kaggle.py`
normalises it via `STATUS_LABEL_MAP` + tokenisation:

| `Status` value (lowercased, stripped) | Maps to | Reason |
|---|---|---|
| `shipped - delivered to buyer` | `Delivered` (is_returned=0) | Successful delivery |
| `shipped` / `pending` / `shipped - picked up` | `Delivered` (is_returned=0) | In transit at extraction time; treat as non-returned |
| `shipped - returned to seller` | `Returned` (is_returned=1) | RTO positive class |
| `shipped - returned to buyer` | `Returned` (is_returned=1) | RTO positive class |
| `shipped - rejected by buyer` | `Returned` (is_returned=1) | Buyer refused delivery at door = RTO |
| `rto` / `return to seller` / `returned` | `Returned` (is_returned=1) | Explicit RTO outcomes |
| `undeliverable` | `Returned` (is_returned=1) | Address-level RTO |
| `cancelled` / `canceled` / `shipped - cancelled` | **DROPPED** | Merchant-initiated; not an RTO outcome (the order never shipped) |

### `is_cod` inference

The Amazon India Sale Report has no explicit `payment_method` column.
Per the spec directive ("if no explicit COD flag, assume COD for the
RTO problem"), `ingest_kaggle.py` infers `is_cod`:

1. If the source CSV has `payment_method` column → use it (COD/cash → 1,
   otherwise 0). Not the case for the Amazon dataset.
2. If the source CSV has a `B2B` flag → use it as a proxy (B2B=True →
   prepaid → `is_cod=0`; B2C → COD → `is_cod=1`). This is a reasonable
   heuristic because B2B orders are almost always prepaid.
3. Otherwise → `is_cod=1` (assume COD per spec).

---

## After the retrain — what changes

| File | What changes |
|---|---|
| `out/model_real.joblib` | New model artifact (HistGB trained on real data). |
| `out/metrics_real.json` | Evaluation report (PR-AUC, ROC-AUC, F1, precision/recall@threshold, confusion matrix, lift-over-base, leakage=0 assertion). |
| `out/model_registry.json` | New champion `real-{timestamp}` registered; prior champion demoted. The dashboard's Model Health page (`/api/v1/models/current`) reflects this immediately. |
| `docs/cost_table.md` | Regenerated by `scripts/cost_table.py --data data/raw/ingested_real.csv` (via the `load_orders` dispatch in `src/features/cleaning.py`). The 3-way sweep is fresh; the 5-way intervention policy section (Track N) is hand-curated and unchanged. |
| `docs/feature_importance.md` | Regenerated by `src.models.explain.global_importance` (permutation AP-drop, n_repeats=10). |
| `README.md` | The "Results" table's PR-AUC row should be updated by hand once the real-data number is known (the synthetic 0.5495 cell becomes the real-data PR-AUC). |

The API doesn't need a restart to pick up the new champion — the
`current_champion()` call in `src/api/routes.py` reads the registry
live on each request. The model artifact at `out/model_api.joblib`
(the file the API lifespan loads at worker boot) is NOT overwritten by
`retrain_real.py`; to switch the API to use the real-data model,
either:
- re-deploy with `docker compose up -d --build` (the lifespan will
  load `model_real.joblib` once you symlink or copy it to
  `model_api.joblib`); or
- register the real-data model in Postgres mode (set `DATABASE_URL`
  to a real Postgres DSN, run `retrain_real.py`, restart the API —
  the lifespan will pick up the new champion from the
  `model_registry` table on boot).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: source CSV not found: data/raw/amazon_sale_report.csv` | File not placed at the expected path | Pass the explicit path: `python scripts/ingest_kaggle.py path/to/your.csv` |
| `[warn] no source for '<column>' - filling nulls` | Source CSV has different column names | Update the `COLUMN_MAPS["amazon"]` dict in `scripts/ingest_kaggle.py` |
| `[warn] 0 Returned rows in the dataset` | All rows had `Status=Delivered` or were `Cancelled` (dropped) | Verify the source CSV's `Status` column has the expected RTO outcomes; check the `STATUS_LABEL_MAP` for missing tokens |
| `ERROR: 0 returned rows — cannot train` | The ingested CSV has no positive class after the status mapping | Same as above; the model can't train without RTO positives |
| `CustomerID leakage = N (must be 0)` | Repeat customers leaked across the train/test split | Re-run; the `GroupShuffleSplit` is deterministic (random_state=42) so this shouldn't happen — file an issue if it does |
| `::error::PR-AUC X.XXXX below floor 0.60 — model NOT promoted` | The model trained but is worse than random on the positive class | Verify the data has real signal; check feature engineering (the `state_norm` + `city_tier` heuristic may need tuning); consider `--feature-set order` (drop `address_quality`) if the Amazon dataset has no address-quality column |
| `model-registry registration skipped: ...` | Postgres mode is enabled but the connection failed | Check `DATABASE_URL` env var; the registry falls back to file mode if not set, so this is informational |

---

## Reference papers (cited in the column map + status mapping)

- **Kandula, Krishnamoorthy, Roy** — *A prescriptive analytics framework
  for efficient E-commerce order delivery*, Decision Support Systems vol.
  147, 2021. DOI 10.1016/j.dss.2021.113584. **AUC 0.73-0.79** on real
  Indian e-commerce delivery data (Flipkart acknowledged); feature ladder
  is Payment_Type → Service_Tier → Delay → POI/amenity counts. This is
  the PR-AUC ≥ 0.72 target benchmark.
- **Bahnsen, Stojanovic, Aouada, Ottersten** — *Cost Sensitive Credit
  Card Fraud Detection using Bayes Minimum Risk*, ICMLA 2013. DOI
  10.1109/ICMLA.2013.68. Per-transaction FN cost = amount; the
  `optimal_decision()` + 5-way intervention policy
  (`src/business/cost_optimizer.py`) implement this.
- **Drummond & Holte** — *Cost Curves: An Improved Method for
  Visualizing Classifier Performance*, Machine Learning 65:95-130, 2006.
  DOI 10.1007/s10994-006-8199-5. The `docs/cost_table.md` 3-way sweep +
  `/v1/policy/cost-curves` endpoint implement this.

For the full bibliography see [`docs/research/INDEX.md`](../../docs/research/INDEX.md).
