"""T3.7 — CI test that mlops.yml Stage 3 PR-AUC gate fires on low-PR-AUC.

Closes the test-coverage gap noted by Subagent 11-d: the mlops.yml
Stage 3 ``Fail if PR-AUC < 0.60`` step has ``sys.exit(1)`` but no test
verifies the gate actually fires. This file:

1. Reads ``.github/workflows/mlops.yml`` as text + asserts the PR-AUC
   gate step is present (with the threshold value + the sys.exit(1)
   call + the ``::error::`` annotation).
2. Re-implements the EXACT gate logic from the YAML heredoc as a
   ``gate_pr_auc(metrics: dict) -> int`` function in this test file.
3. Tests the gate function with 3 cases:
   * ``pr_auc=0.30`` (deliberately-bad model — worse than random) →
     ``SystemExit(1)`` (gate fires, model NOT promoted).
   * ``pr_auc=0.59`` (just below threshold) → ``SystemExit(1)`` (edge).
   * ``pr_auc=0.80`` (good model) → no exception (gate passes,
     promotion proceeds).
   * ``pr_auc=0.60`` (exactly at threshold) → no exception (the
     ``< 0.60`` check is strict-less-than, so 0.60 passes).

The gate logic lives in mlops.yml's Stage 3 ``Fail if PR-AUC < 0.60``
step as a Python heredoc. We extract it via the regex parse + re-
implement the same check here so the test runs without needing the
GitHub Actions runtime. The 2 copies are kept in sync by the
``test_pr_auc_gate_yaml_matches_test_logic`` test that asserts the
threshold value + operator in the YAML match the test's gate function.

This test doesn't RUN mlops.yml (that's a CI action) — it tests that
the gate LOGIC is correct.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MLOPS_YML = REPO_ROOT / ".github" / "workflows" / "mlops.yml"


# ---------------------------------------------------------------------------
# 1. YAML structure assertions
# ---------------------------------------------------------------------------


def test_mlops_yml_pr_auc_gate_step_present():
    """The mlops.yml workflow file MUST contain the Stage 3 PR-AUC gate.

    Asserts the literal substrings that make up the gate step:
      * the step name "Fail if PR-AUC < 0.60"
      * the threshold value "0.60"
      * the ``sys.exit(1)`` call (the actual gate firing)
      * the ``::error::`` annotation (so the failure surfaces in the
        GitHub Actions UI's Annotations tab)
      * the ``pr_auc`` variable name (the metric being gated)
    """
    text = MLOPS_YML.read_text()
    # Step name.
    assert "Fail if PR-AUC < 0.60" in text, (
        "mlops.yml Stage 3 step name 'Fail if PR-AUC < 0.60' must be "
        "present — this is the gate the user's source-paper benchmark "
        "(Kandula 2021 e-commerce delivery AUC 0.73-0.79; PR-AUC < 0.60 "
        "= worse than random) mandates"
    )
    # Threshold value.
    assert "0.60" in text, (
        "mlops.yml PR-AUC threshold value 0.60 must be present"
    )
    # sys.exit(1) — the actual gate-firing call.
    assert "sys.exit(1)" in text, (
        "mlops.yml PR-AUC gate must call sys.exit(1) on threshold breach — "
        "without it the gate is a no-op echo (the gap Subagent 11-d noted)"
    )
    # GitHub Actions ::error:: annotation.
    assert "::error::" in text, (
        "mlops.yml PR-AUC gate must surface a ::error:: annotation so the "
        "failure is visible in the Actions UI's Annotations tab"
    )
    # The metric variable name.
    assert "pr_auc" in text, (
        "mlops.yml PR-AUC gate must reference the 'pr_auc' metric key "
        "(must match the metrics JSON key written by scripts/evaluate.py)"
    )


def test_pr_auc_gate_yaml_matches_test_logic():
    """The gate logic in this test file must match the YAML's threshold
    + operator. Parses the YAML's heredoc for the comparison + asserts
    our local GATE_THRESHOLD + GATE_OPERATOR match.
    """
    text = MLOPS_YML.read_text()
    # Find the comparison: "if pr_auc < 0.60:" (whitespace-flexible).
    match = re.search(
        r"if\s+pr_auc\s*([<>=!]+)\s*([0-9.]+)\s*:",
        text,
    )
    assert match is not None, (
        "mlops.yml PR-AUC gate must contain a comparison of the form "
        "'if pr_auc <OP> <THRESHOLD>:'; couldn't find it"
    )
    yaml_op = match.group(1)
    yaml_threshold = float(match.group(2))
    assert yaml_op == GATE_OPERATOR, (
        f"YAML gate operator '{yaml_op}' must match test's GATE_OPERATOR "
        f"'{GATE_OPERATOR}' — if these drift, the YAML + test disagree "
        f"on the gate semantics"
    )
    assert yaml_threshold == GATE_THRESHOLD, (
        f"YAML gate threshold {yaml_threshold} must match test's "
        f"GATE_THRESHOLD {GATE_THRESHOLD} — if these drift, the YAML + "
        f"test disagree on the gate cutoff"
    )


# ---------------------------------------------------------------------------
# 2. Re-implemented gate logic (mirrors mlops.yml heredoc verbatim)
# ---------------------------------------------------------------------------

GATE_THRESHOLD = 0.60
GATE_OPERATOR = "<"


def gate_pr_auc(metrics: dict) -> int:
    """The PR-AUC gate. Mirrors the mlops.yml Stage 3 heredoc verbatim:

        import json, sys
        m = json.load(open("out/metrics.json"))
        pr_auc = float(m["pr_auc"])
        print(f"PR-AUC = {pr_auc:.4f} (threshold 0.60)")
        if pr_auc < 0.60:
            print(f"::error::PR-AUC {pr_auc:.4f} below threshold 0.60 — "
                  "model NOT promoted")
            sys.exit(1)
        print("✓ PR-AUC gate passed")

    Returns 0 on pass; raises ``SystemExit(1)`` on fail. The caller
    (test_pr_auc_gate_fires_on_low_pr_auc etc.) wraps the call in
    ``pytest.raises(SystemExit)``.
    """
    pr_auc = float(metrics["pr_auc"])
    print(f"PR-AUC = {pr_auc:.4f} (threshold {GATE_THRESHOLD})")
    # NOTE: this comparison must match the YAML's `if pr_auc < 0.60:`.
    # The test_pr_auc_gate_yaml_matches_test_logic test above enforces
    # this stays in sync.
    if pr_auc < GATE_THRESHOLD:
        print(
            f"::error::PR-AUC {pr_auc:.4f} below threshold "
            f"{GATE_THRESHOLD} — model NOT promoted"
        )
        sys.exit(1)
    print("✓ PR-AUC gate passed")
    return 0


# ---------------------------------------------------------------------------
# 3. Gate behavior tests (good model + bad model + edge cases)
# ---------------------------------------------------------------------------


def test_pr_auc_gate_fires_on_low_pr_auc(capsys):
    """A deliberately-bad model (PR-AUC=0.30, worse than random) MUST
    fire the gate → ``SystemExit(1)``. The model is NOT promoted.
    """
    bad_metrics = {"pr_auc": 0.30, "roc_auc": 0.40}
    with pytest.raises(SystemExit) as exc_info:
        gate_pr_auc(bad_metrics)
    assert exc_info.value.code == 1, (
        "Gate must exit with code 1 on PR-AUC below threshold so the "
        "GitHub Actions step fails + downstream stages (register, canary, "
        "deploy) are blocked"
    )
    captured = capsys.readouterr()
    assert "PR-AUC = 0.3000" in captured.out
    assert "below threshold 0.6" in captured.out
    assert "::error::" in captured.out


def test_pr_auc_gate_fires_just_below_threshold():
    """PR-AUC=0.59 (just below the 0.60 threshold) must fire — the
    gate is strict-less-than, so 0.59 < 0.60 fires. This is the edge
    case the strict-< vs <= operator choice matters for.
    """
    edge_bad = {"pr_auc": 0.59, "roc_auc": 0.70}
    with pytest.raises(SystemExit) as exc_info:
        gate_pr_auc(edge_bad)
    assert exc_info.value.code == 1


def test_pr_auc_gate_passes_on_good_model(capsys):
    """A good model (PR-AUC=0.80, well above the 0.60 threshold) MUST
    pass the gate (no SystemExit). Promotion proceeds to the next
    stage (Register model + canary gate + container build + deploy).
    """
    good_metrics = {"pr_auc": 0.80, "roc_auc": 0.88}
    # Should NOT raise.
    rc = gate_pr_auc(good_metrics)
    assert rc == 0
    captured = capsys.readouterr()
    assert "PR-AUC = 0.8000" in captured.out
    assert "gate passed" in captured.out
    assert "::error::" not in captured.out


def test_pr_auc_gate_at_threshold_passes():
    """PR-AUC=0.60 (exactly at threshold) passes — the gate uses
    ``<`` (strict-less-than), so 0.60 is NOT below 0.60. This is the
    edge case that distinguishes ``<`` from ``<=``.
    """
    exact_metrics = {"pr_auc": 0.60, "roc_auc": 0.75}
    rc = gate_pr_auc(exact_metrics)
    assert rc == 0


def test_pr_auc_gate_round_trip_via_json_file(tmp_path, capsys):
    """End-to-end: write a metrics.json with a deliberately-bad
    PR-AUC, parse it back through the EXACT heredoc load path, run the
    gate → assert it fires.

    This mirrors the real CI flow: ``scripts/evaluate.py`` writes
    ``out/metrics.json`` → the mlops.yml heredoc does
    ``json.load(open("out/metrics.json"))`` → runs the gate.
    """
    metrics_file = tmp_path / "metrics.json"
    bad_metrics = {"pr_auc": 0.30, "roc_auc": 0.40, "threshold_best_f1": 0.5}
    metrics_file.write_text(json.dumps(bad_metrics))
    # Re-load via the same path the heredoc uses.
    loaded = json.loads(metrics_file.read_text())
    with pytest.raises(SystemExit) as exc_info:
        gate_pr_auc(loaded)
    assert exc_info.value.code == 1

    # And the good-model round-trip.
    good_metrics = {"pr_auc": 0.80, "roc_auc": 0.88, "threshold_best_f1": 0.5}
    metrics_file.write_text(json.dumps(good_metrics))
    loaded2 = json.loads(metrics_file.read_text())
    rc = gate_pr_auc(loaded2)
    assert rc == 0
