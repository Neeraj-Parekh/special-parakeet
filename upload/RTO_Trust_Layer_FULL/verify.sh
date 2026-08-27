#!/usr/bin/env bash
# Verify the project end-to-end: lint → tests → evaluation metrics.
#
# Pick a Python interpreter in this order:
#   1. $PY env var (if the caller wants to pin a specific binary)
#   2. `python3` on PATH
#   3. `python` on PATH
#   4. `uv run python` (requires `uv` — install: pip install uv, or
#      curl -LsSf https://astral.sh/uv/install.sh | sh)
#
# The previous version hard-coded Neeraj's laptop venv path
# (`/mnt/20265E15265DEC72/study/CODE/linux_venv/bin/python`) which fails
# everywhere else. This version is portable.
set -euo pipefail
PY="${PY:-$(command -v python3 || command -v python || echo 'uv run python')}"
cd "$(dirname "$0")"

echo ">> Using Python: $PY"
"$PY" -m ruff check src scripts tests
"$PY" -m pytest tests -q
"$PY" scripts/evaluate.py --feature-set "${FEATURE_SET:-order}" --out out/metrics.json
