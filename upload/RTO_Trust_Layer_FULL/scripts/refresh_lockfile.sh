#!/usr/bin/env bash
# Refresh uv.lock — run this on your laptop after changing dependencies.
#
# Why this script exists:
#   The repo's `uv.lock` was a 3-line stub (`version=1, revision=3,
#   requires-python=">=3.11"`) — `uv lock` was never run. The sandbox agent
#   (Task 3-a, Track B, Day 1) couldn't run `uv` here (no network access in
#   the sandbox), so the actual lockfile resolution is deferred to you, the
#   user, on your laptop where the toolchain is available.
#
# When to run:
#   - After every change to `pyproject.toml` [project].dependencies /
#     [project.optional-dependencies]
#   - After every change to `requirements.txt`
#   - Before committing dependency changes
#   - Before running `uv sync` to install deps
#
# Prerequisites:
#   - `uv` installed on your laptop. If not:
#       curl -LsSf https://astral.sh/uv/install.sh | sh
#     or:
#       pip install uv
#     Then ensure `uv` is on your PATH (the installer prints instructions).
#
# What this script does:
#   1. cd to the project root (parent of this `scripts/` folder)
#   2. `uv lock` — reads `pyproject.toml`, resolves the full dep graph, and
#      rewrites `uv.lock` with pinned hashes for every transitive dep.
#   3. Prints a "Done" line.
#
# After running this, your `uv.lock` should grow from ~3 lines to ~200-400
# lines (one section per resolved package, with SHA-256 hashes). Commit it.
set -euo pipefail

cd "$(dirname "$0")/.."

echo ">> Project root: $(pwd)"
echo ">> Running: uv lock"
echo ">> (this may take 15-30s on first run — uv is resolving the full dep graph)"
uv lock
echo ">> Done. uv.lock refreshed."
echo ">> Next steps:"
echo ">>   uv sync                  # install the runtime deps into .venv"
echo ">>   uv sync --extra dev      # also install pytest + ruff"
echo ">>   ./verify.sh               # lint + tests + eval"
