"""T3.7 -- CI test that mlops.yml Stage 3 PR-AUC gate fires on low-PR-AUC.

Wave 3 (Subagent 15-c) update: the gate was migrated from a fixed
``< 0.60`` absolute threshold to a RELATIVE ``< 3x baseline`` gate
(honest for imbalanced data -- a random classifier scores PR-AUC =
positive_rate = 0.0164 at 1.64% RTO prevalence, so 0.60 was
mathematically unreachable). The new gate reads ``baseline_pr_auc``
(or ``train_rto_rate`` fallback or hard 0.05 floor) from
metrics.json, computes ``threshold = max(3.0 * baseline, 0.05)``
+ fires ``sys.exit(1)`` when ``pr_auc < threshold``.

This file:
1. Reads ``.github/workflows/mlops.yml`` as text + asserts the PR-AUC
   gate step is present (with the relative-threshold formula + the
   sys.exit(1) call + the ``::error::`` annotation).
2. Re-implements the EXACT gate logic from the YAML heredoc as a
   ``gate_pr_auc(metrics: dict) -> int`` function in this test file.
3. Tests the gate function with 5 cases:
   * ``pr_auc=0.05, baseline=0.05`` (no lift) -> ``SystemExit(1)``.
   * ``pr_auc=0.04, baseline_pr_auc=0.0164`` (below hard 0.05 floor) ->
     ``SystemExit(1)`` (degenerate-pass on tiny baselines blocked).
   * ``pr_auc=0.20, baseline=0.05`` (4x lift -- strong) -> no exception.
   * ``pr_auc=0.10, baseline=0.0164`` (6x lift on realistic 1.64% RTO
     rate) -> no exception (the case the old ``< 0.60`` absolute gate
     got WRONG; a useful 6x-lift model was being rejected).
   * ``pr_auc=0.375, baseline=0.125`` (exactly at threshold) -> no
     exception (the ``<`` check is strict-less-than, so
     exactly-at-threshold passes; 0.125/0.375 pair is IEEE-754-clean).

The gate logic lives in mlops.yml's Stage 3 ``Fail if PR-AUC < 3x
baseline (relative gate, honest for imbalanced data)`` step as a
Python heredoc. We extract it via the regex parse + re-implement the
same check here so the test runs without needing the GitHub Actions
runtime. The 2 copies are kept in sync by the
``test_pr_auc_gate_yaml_matches_test_logic`` test that asserts the
threshold formula + operator in the YAML match the test's gate
function.

This test doesn't RUN mlops.yml (that's a CI action) -- it tests
that the gate LOGIC is correct.
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
      * the step name "Fail if PR-AUC < 3x baseline (relative gate, ...)"
      * the relative threshold formula ``3.0 * baseline`` (3x lift)
      * the hard 0.05 floor fallback (``max(3.0 * baseline, 0.05)``)
      * the ``baseline_pr_auc`` field name (read from metrics.json)
      * the ``train_rto_rate`` fallback field (when baseline_pr_auc absent)
      * the ``sys.exit(1)`` call (the actual gate firing)
      * the ``::error::`` annotation (so the failure surfaces in the
        GitHub Actions UI's Annotations tab)
      * the ``pr_auc`` variable name (the metric being gated)
    """
    text = MLOPS_YML.read_text()
    # Step name (the new relative-gate name).
    assert "Fail if PR-AUC < 3x baseline" in text, (
        "mlops.yml Stage 3 step name 'Fail if PR-AUC < 3x baseline...' must "
        "be present -- this is the relative gate (3x lift = minimum useful "
        "classifier for imbalanced data). The OLD absolute '< 0.60' gate was "
        "mathematically unreachable for low-prevalence targets (1.64% RTO "
        "rate -> random classifier scores PR-AUC = 0.0164; 0.60 unreachable)."
    )
    # The relative threshold formula -- 3.0 * baseline (the lift multiplier).
    assert "3.0 * baseline" in text, (
        "mlops.yml PR-AUC gate must compute threshold = 3.0 * baseline "
        "(the 3x lift multiplier -- minimum useful lift for imbalanced data)"
    )
    # The hard 0.05 floor fallback.
    assert "0.05" in text, (
        "mlops.yml PR-AUC gate must have a hard 0.05 floor in the "
        "max(3.0 * baseline, 0.05) formula -- prevents the gate from passing "
        "a degenerate model when baseline is artificially low"
    )
    # The baseline_pr_auc field (read from metrics.json).
    assert "baseline_pr_auc" in text, (
        "mlops.yml PR-AUC gate must read baseline_pr_auc from metrics.json "
        "(the train-set positive rate that defines the random-classifier "
        "baseline -- a model that can't beat 3x this baseline is not useful)"
    )
    # The train_rto_rate fallback (when baseline_pr_auc is absent).
    assert "train_rto_rate" in text, (
        "mlops.yml PR-AUC gate must fall back to train_rto_rate when "
        "baseline_pr_auc is absent -- backwards-compat with metrics.json "
        "files written before the baseline_pr_auc field was added"
    )
    # sys.exit(1) -- the actual gate-firing call.
    assert "sys.exit(1)" in text, (
        "mlops.yml PR-AUC gate must call sys.exit(1) on threshold breach -- "
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
    formula + operator. Parses the YAML's heredoc for the comparison +
    asserts our local GATE_LIFT_MULTIPLIER + GATE_FLOOR + GATE_OPERATOR
    match.

    The new gate uses a RELATIVE threshold: ``threshold =
    max(3.0 * baseline, 0.05)``. The test mirrors this exactly so they
    stay in sync -- if the YAML's multiplier (3.0), floor (0.05), or
    operator (<) drifts from the test's constants, this test fails +
    surfaces the drift.
    """
    text = MLOPS_YML.read_text()
    # Find the comparison: "if pr_auc < threshold:" (whitespace-flexible).
    match = re.search(
        r"if\s+pr_auc\s*([<>=!]+)\s*threshold\s*:",
        text,
    )
    assert match is not None, (
        "mlops.yml PR-AUC gate must contain a comparison of the form "
        "'if pr_auc <OP> threshold:' where 'threshold' is the computed "
        "max(3.0 * baseline, 0.05); couldn't find it"
    )
    yaml_op = match.group(1)
    assert yaml_op == GATE_OPERATOR, (
        f"YAML gate operator '{yaml_op}' must match test's GATE_OPERATOR "
        f"'{GATE_OPERATOR}' -- if these drift, the YAML + test disagree "
        f"on the gate semantics"
    )
    # Find the threshold formula: "threshold = max(3.0 * baseline, 0.05)".
    # The multiplier + floor are the 2 constants that must stay in sync.
    formula_match = re.search(
        r"threshold\s*=\s*max\s*\(\s*([0-9.]+)\s*\*\s*baseline\s*,\s*([0-9.]+)\s*\)",
        text,
    )
    assert formula_match is not None, (
        "mlops.yml PR-AUC gate must compute threshold = max(<MULT> * "
        "baseline, <FLOOR>) -- the relative-gate formula. Couldn't find it."
    )
    yaml_multiplier = float(formula_match.group(1))
    yaml_floor = float(formula_match.group(2))
    assert yaml_multiplier == GATE_LIFT_MULTIPLIER, (
        f"YAML gate lift multiplier {yaml_multiplier} must match test's "
        f"GATE_LIFT_MULTIPLIER {GATE_LIFT_MULTIPLIER} -- if these drift, "
        f"the YAML + test disagree on the lift cutoff (3x = minimum useful "
        f"lift for imbalanced data)"
    )
    assert yaml_floor == GATE_FLOOR, (
        f"YAML gate floor {yaml_floor} must match test's GATE_FLOOR "
        f"{GATE_FLOOR} -- if these drift, the YAML + test disagree on the "
        f"hard floor (0.05 prevents degenerate-pass when baseline is "
        f"artificially low)"
    )


# ---------------------------------------------------------------------------
# 2. Re-implemented gate logic (mirrors mlops.yml heredoc verbatim)
# ---------------------------------------------------------------------------

# Wave 3 (Subagent 15-c) update: the gate was migrated from a fixed
# 0.60 absolute threshold to a RELATIVE 3x-baseline gate (honest for
# imbalanced data). The 3 constants below are the gate's contract --
# they MUST stay in sync with the mlops.yml heredoc. The
# ``test_pr_auc_gate_yaml_matches_test_logic`` test enforces this.
GATE_LIFT_MULTIPLIER = 3.0  # 3x baseline = minimum useful lift
GATE_FLOOR = 0.05  # hard floor prevents degenerate-pass when baseline is tiny
GATE_OPERATOR = "<"


def gate_pr_auc(metrics: dict) -> int:
    """The PR-AUC gate. Mirrors the mlops.yml Stage 3 heredoc verbatim:

        import json, sys
        m = json.load(open("out/metrics.json"))
        pr_auc = float(m["pr_auc"])
        baseline = float(m.get("baseline_pr_auc") or m.get("train_rto_rate") or 0.05)
        threshold = max(3.0 * baseline, 0.05)
        lift = pr_auc / baseline if baseline > 0 else 0
        print(f"PR-AUC = {pr_auc:.4f} (baseline {baseline:.4f}, "
              f"threshold {threshold:.4f}, lift {lift:.2f}x)")
        if pr_auc < threshold:
            print(f"::error::PR-AUC {pr_auc:.4f} below relative threshold "
                  f"{threshold:.4f} (3x baseline {baseline:.4f}) -- "
                  f"model NOT promoted")
            sys.exit(1)
        print(f"PR-AUC gate passed ({lift:.2f}x baseline)")

    Returns 0 on pass; raises ``SystemExit(1)`` on fail. The caller
    (test_pr_auc_gate_fires_on_no_lift_model etc.) wraps the call in
    ``pytest.raises(SystemExit)``.
    """
    pr_auc = float(metrics["pr_auc"])
    baseline = float(
        metrics.get("baseline_pr_auc")
        or metrics.get("train_rto_rate")
        or GATE_FLOOR
    )
    threshold = max(GATE_LIFT_MULTIPLIER * baseline, GATE_FLOOR)
    lift = pr_auc / baseline if baseline > 0 else 0
    print(
        f"PR-AUC = {pr_auc:.4f} (baseline {baseline:.4f}, "
        f"threshold {threshold:.4f}, lift {lift:.2f}x)"
    )
    # NOTE: this comparison must match the YAML's `if pr_auc < threshold:`.
    # The test_pr_auc_gate_yaml_matches_test_logic test above enforces
    # this stays in sync.
    if pr_auc < threshold:
        print(
            f"::error::PR-AUC {pr_auc:.4f} below relative threshold "
            f"{threshold:.4f} (3x baseline {baseline:.4f}) -- "
            f"model NOT promoted"
        )
        sys.exit(1)
    print(f"PR-AUC gate passed ({lift:.2f}x baseline)")
    return 0


# ---------------------------------------------------------------------------
# 3. Gate behavior tests (good model + bad model + edge cases)
# ---------------------------------------------------------------------------


def test_pr_auc_gate_fires_on_no_lift_model(capsys):
    """A no-lift model (PR-AUC=0.05, baseline=0.05) MUST fire the gate ->
    ``SystemExit(1)``. The model is NOT promoted.

    With baseline=0.05 + lift_multiplier=3.0, threshold = max(0.15, 0.05)
    = 0.15. PR-AUC=0.05 < 0.15 -> gate fires. This is the "no lift over
    baseline" case -- a model that can't beat the random-classifier
    baseline by even 1x (let alone the 3x gate) is not useful.
    """
    bad_metrics = {
        "pr_auc": 0.05,
        "roc_auc": 0.40,
        "baseline_pr_auc": 0.05,
    }
    with pytest.raises(SystemExit) as exc_info:
        gate_pr_auc(bad_metrics)
    assert exc_info.value.code == 1, (
        "Gate must exit with code 1 on PR-AUC below threshold so the "
        "GitHub Actions step fails + downstream stages (register, canary, "
        "deploy) are blocked"
    )
    captured = capsys.readouterr()
    assert "PR-AUC = 0.0500" in captured.out
    assert "baseline 0.0500" in captured.out
    assert "threshold 0.1500" in captured.out
    assert "below relative threshold" in captured.out
    assert "::error::" in captured.out


def test_pr_auc_gate_fires_below_hard_floor():
    """PR-AUC=0.04 with baseline=0.0164 (1.64% RTO rate) must fire --
    the 3x baseline = 0.0492 < hard floor 0.05, so the floor kicks in
    + threshold = 0.05. 0.04 < 0.05 -> gate fires.

    This is the edge case that justifies the hard 0.05 floor: without
    it, a model with PR-AUC=0.045 (only 2.7x baseline) would pass,
    even though 0.045 is barely better than the random-classifier
    baseline (0.0164) -- the floor prevents "degenerate pass" on tiny
    baselines.
    """
    edge_bad = {
        "pr_auc": 0.04,
        "roc_auc": 0.30,
        "baseline_pr_auc": 0.0164,  # 1.64% RTO rate (the realistic case)
    }
    with pytest.raises(SystemExit) as exc_info:
        gate_pr_auc(edge_bad)
    assert exc_info.value.code == 1


def test_pr_auc_gate_passes_on_strong_lift(capsys):
    """A strong-lift model (PR-AUC=0.20, baseline=0.05 -> 4x baseline)
    MUST pass the gate (no SystemExit). Promotion proceeds to the next
    stage (Register model + canary gate + container build + deploy).

    With baseline=0.05 + multiplier=3.0, threshold = max(0.15, 0.05)
    = 0.15. PR-AUC=0.20 > 0.15 -> gate passes (4x lift).
    """
    good_metrics = {
        "pr_auc": 0.20,
        "roc_auc": 0.88,
        "baseline_pr_auc": 0.05,
    }
    rc = gate_pr_auc(good_metrics)
    assert rc == 0
    captured = capsys.readouterr()
    assert "PR-AUC = 0.2000" in captured.out
    assert "baseline 0.0500" in captured.out
    assert "threshold 0.1500" in captured.out
    assert "gate passed" in captured.out
    assert "4.00x baseline" in captured.out
    assert "::error::" not in captured.out


def test_pr_auc_gate_passes_on_realistic_imbalanced_data():
    """A realistic imbalanced-data case (1.64% RTO prevalence, baseline
    = 0.0164, model PR-AUC=0.10 -> 6.1x baseline) passes -- this is the
    case the OLD ``< 0.60`` gate got WRONG. Under the old gate, this
    model would have been rejected (0.10 < 0.60) even though it's a
    6x lift over the random-classifier baseline (legitimately useful).

    Under the new relative gate, threshold = max(3 * 0.0164, 0.05) =
    max(0.0492, 0.05) = 0.05. PR-AUC=0.10 > 0.05 -> gate passes.
    """
    realistic_metrics = {
        "pr_auc": 0.10,
        "roc_auc": 0.75,
        "baseline_pr_auc": 0.0164,  # 1.64% RTO rate (the realistic case)
    }
    rc = gate_pr_auc(realistic_metrics)
    assert rc == 0


def test_pr_auc_gate_at_threshold_passes():
    """PR-AUC = threshold (exactly) passes -- the gate uses ``<`` (strict-
    less-than), so a PR-AUC equal to the threshold is NOT below it.
    This is the edge case that distinguishes ``<`` from ``<=``.

    Uses baseline_pr_auc=0.125 (exactly representable in IEEE 754 binary
    float as 0.011_2 = 1/8) so threshold = 3 * 0.125 = 0.375 is also
    exactly representable (no floating-point slop). PR-AUC=0.375 is NOT
    strictly less than 0.375 -> gate passes (exactly-at-threshold).

    With the prior naive baseline=0.05 + pr_auc=0.15, floating-point
    representation makes 3 * 0.05 = 0.15000000000000002 (slightly above
    0.15), so 0.15 < 0.15000000000000002 is True + the gate fires
    incorrectly -- the test would have masked this. The 0.125/0.375
    pair avoids the floating-point edge entirely.
    """
    exact_metrics = {
        "pr_auc": 0.375,  # exactly at threshold (3 * 0.125)
        "roc_auc": 0.75,
        "baseline_pr_auc": 0.125,  # exactly representable in IEEE 754
    }
    rc = gate_pr_auc(exact_metrics)
    assert rc == 0


def test_pr_auc_gate_round_trip_via_json_file(tmp_path, capsys):
    """End-to-end: write a metrics.json with a deliberately-bad
    PR-AUC + baseline, parse it back through the EXACT heredoc load
    path, run the gate -> assert it fires.

    This mirrors the real CI flow: ``scripts/evaluate.py`` writes
    ``out/metrics.json`` -> the mlops.yml heredoc does
    ``json.load(open("out/metrics.json"))`` -> runs the gate.
    """
    metrics_file = tmp_path / "metrics.json"
    bad_metrics = {
        "pr_auc": 0.05,  # no lift over baseline
        "roc_auc": 0.40,
        "threshold_best_f1": 0.5,
        "baseline_pr_auc": 0.05,
    }
    metrics_file.write_text(json.dumps(bad_metrics))
    # Re-load via the same path the heredoc uses.
    loaded = json.loads(metrics_file.read_text())
    with pytest.raises(SystemExit) as exc_info:
        gate_pr_auc(loaded)
    assert exc_info.value.code == 1

    # And the good-model round-trip (strong lift).
    good_metrics = {
        "pr_auc": 0.20,
        "roc_auc": 0.88,
        "threshold_best_f1": 0.5,
        "baseline_pr_auc": 0.05,
    }
    metrics_file.write_text(json.dumps(good_metrics))
    loaded2 = json.loads(metrics_file.read_text())
    rc = gate_pr_auc(loaded2)
    assert rc == 0
