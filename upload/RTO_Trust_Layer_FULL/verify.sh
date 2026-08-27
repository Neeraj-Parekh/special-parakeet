#!/usr/bin/env bash
set -euo pipefail
PY=/mnt/20265E15265DEC72/study/CODE/linux_venv/bin/python
cd "$(dirname "$0")"
"$PY" -m ruff check src scripts tests
"$PY" -m pytest tests -q
"$PY" scripts/evaluate.py --feature-set "${FEATURE_SET:-order}" --out out/metrics.json
