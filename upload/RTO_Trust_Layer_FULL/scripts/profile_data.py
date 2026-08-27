"""TFX stage 1 — Data Analysis (``generate_data_statistics``).

Per the TFX paper (Baylor 2017, ``command/05-PAPER-SKILLS-MAP.md`` gap #14),
the first stage of a production ML pipeline emits per-feature statistics
(count, nulls, unique values, quantiles, categorical distribution) so a
human reviewer can spot distribution drift before it corrupts the model.

This script is the CI-side replacement for TFX's
``GenerateDataStatistics`` component. It runs in the GitHub Actions
``data-analysis`` job (``.github/workflows/mlops.yml`` stage 1) and emits
an HTML report that's uploaded as a CI artifact for audit lineage.

DEPS: pandas + numpy (already in requirements.txt). whylogs is optional —
imported lazily so the script still works if whylogs isn't installed (the
CI env pins only the runtime deps; a future Track L can add
``whylogs>=1.3`` to requirements.txt for the richer profile, but for the
Buildathon demo pandas.describe() + value_counts() is enough).

OUTPUT: ``out/data_profile.html`` (single-file, no external CSS —
viewable in a browser tab + the GitHub Actions artifacts viewer).

USAGE:
    python scripts/profile_data.py --data data/raw/cod_orders.csv \\
        --out out/data_profile.html
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402


def profile_column(s: pd.Series) -> dict:
    """Per-column stats — works for numeric, categorical, and datetime.

    Mirrors TFX's ``DatasetFeatureStatistics`` proto: ``count``,
    ``num_non_missing``, ``num_missing``, ``unique``, plus type-specific
    quantiles (numeric) or top-10 levels (categorical).
    """
    n = int(len(s))
    n_missing = int(s.isna().sum())
    n_unique = int(s.nunique(dropna=True))
    out = {
        "name": s.name,
        "dtype": str(s.dtype),
        "count": n,
        "num_missing": n_missing,
        "pct_missing": round(100.0 * n_missing / n, 2) if n else 0.0,
        "unique": n_unique,
    }
    if pd.api.types.is_numeric_dtype(s):
        try:
            q = s.quantile([0.01, 0.25, 0.5, 0.75, 0.99]).to_dict()
        except Exception:  # pragma: no cover — defensive; quantile on
            # all-NaN column raises in some pandas versions.
            q = {}
        out.update(
            {
                "type": "numeric",
                "mean": float(s.mean()) if not s.isna().all() else None,
                "std": float(s.std()) if not s.isna().all() else None,
                "min": float(s.min()) if not s.isna().all() else None,
                "max": float(s.max()) if not s.isna().all() else None,
                "quantiles": {f"p{int(k * 100)}": float(v) for k, v in q.items()},
            }
        )
    else:
        vc = s.value_counts(dropna=True).head(10)
        out.update(
            {
                "type": "categorical",
                "top_levels": {str(k): int(v) for k, v in vc.items()},
            }
        )
    return out


def render_html(df: pd.DataFrame, col_stats: list[dict], out_path: Path) -> None:
    """Render a single-file HTML report (no external CSS so it works in
    the GitHub Actions artifact viewer + offline browsers)."""
    n_rows, n_cols = df.shape
    rows_html = []
    for c in col_stats:
        rows_html.append(
            "<tr>"
            f"<td><code>{html.escape(str(c['name']))}</code></td>"
            f"<td>{html.escape(c['dtype'])}</td>"
            f"<td>{c['count']}</td>"
            f"<td>{c['num_missing']} ({c['pct_missing']}%)</td>"
            f"<td>{c['unique']}</td>"
            f"<td>{html.escape(c.get('type', '—'))}</td>"
            "<td><pre>"
            f"{html.escape(json.dumps(c.get('quantiles') or c.get('top_levels') or {}, indent=2))}"
            "</pre></td>"
            "</tr>"
        )
    html_doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>RTO Data Profile</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; max-width: 1200px; }}
  h1, h2 {{ color: #0f172a; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 6px 8px; text-align: left; vertical-align: top; }}
  th {{ background: #f1f5f9; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  pre {{ margin: 0; white-space: pre-wrap; max-width: 480px; }}
  code {{ background: #f1f5f9; padding: 1px 4px; border-radius: 2px; }}
  .summary {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0 2rem; }}
  .summary div {{ background: #f8fafc; padding: 12px 16px;
                  border-radius: 6px; border: 1px solid #e2e8f0; }}
  .summary strong {{ font-size: 28px; color: #0f172a; }}
  .summary span {{ color: #64748b; font-size: 12px; }}
</style></head><body>
<h1>RTO Trust Layer — Data Profile</h1>
<div class="summary">
  <div><strong>{n_rows:,}</strong><br><span>rows</span></div>
  <div><strong>{n_cols}</strong><br><span>columns</span></div>
</div>
<h2>Per-column statistics</h2>
<table>
  <thead><tr>
    <th>Column</th><th>DType</th><th>Count</th><th>Missing</th>
    <th>Unique</th><th>Type</th><th>Detail (quantiles / top levels)</th>
  </tr></thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>
<p><small>Generated by <code>scripts/profile_data.py</code> —
TFX stage 1 <code>generate_data_statistics</code> equivalent.</small></p>
</body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc, encoding="utf-8")


def try_whylogs(df: pd.DataFrame) -> dict | None:
    """Optional whylogs profile. Returns None if whylogs isn't installed
    (the common case in CI — we only pin runtime deps). A future Track L
    can add ``whylogs>=1.3`` to requirements.txt; this code path becomes
    the primary one and pandas.describe() becomes the fallback."""
    try:
        import whylogs  # type: ignore
    except ImportError:
        return None
    try:
        results = whylogs.pandas(df)
        # Don't actually write to WhyLabs in CI (no API key); just return
        # the profile summary so the HTML report can include it.
        return {"profiled_with": "whylogs", "summary": results.summary().to_dict()}
    except Exception as e:  # pragma: no cover — defensive.
        return {"profiled_with": "whylogs", "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/raw/cod_orders.csv")
    ap.add_argument("--out", default="out/data_profile.html")
    args = ap.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"::error::data file not found: {data_path}")
        return 1

    df = pd.read_csv(data_path)
    print(f"Loaded {len(df):,} rows × {len(df.columns)} cols from {data_path}")

    whylogs_result = try_whylogs(df)
    if whylogs_result is None:
        print("whylogs not installed — falling back to pandas.describe()")
    else:
        print(f"whylogs profile OK: {whylogs_result.get('profiled_with')}")

    col_stats = [profile_column(df[c]) for c in df.columns]
    # Emit JSON alongside the HTML — machine-readable for the canary-gate
    # stage to diff against the previous profile (Track L future work).
    json_path = Path(args.out).with_suffix(".json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "n_rows": int(len(df)),
                "n_cols": int(len(df.columns)),
                "columns": col_stats,
                "whylogs": whylogs_result,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    render_html(df, col_stats, Path(args.out))
    print(f"Wrote HTML profile → {args.out}")
    print(f"Wrote JSON stats  → {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
