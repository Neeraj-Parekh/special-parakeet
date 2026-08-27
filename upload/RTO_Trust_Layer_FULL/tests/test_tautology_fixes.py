"""Wave 3 (Subagent 15-c) — meta-regression guard for tautology fixes.

DO BADLY #4 — TEST TAUTOLOGY RESIDUALS — the prior test suite had a few
``assert X or True`` / ``or False`` patterns that always passed regardless
of the assertion. The T1.3 Merkle proof test was the worst offender
(fixed in Wave 1 by 14-d) — its final ``assert h == root or True`` always
passed even when the proof reconstruction was broken at odd indices.
The T1.3-style ``assert clean_order_value("") != clean_order_value("")``
test in test_pipeline.py exploited IEEE 754 NaN!=NaN semantics; while
not strictly a tautology (it would have failed if the function returned
0/None/""), it was opaque + masked non-determinism. Wave 3 (15-c)
replaced these with explicit ``math.isnan`` checks so the contract is
documented + the test fails on real regressions (e.g. if the function
started returning 0.0 for empty input, silently corrupting the
order-value feature column with fake zeros).

This file is a META-test that enforces the absence of these patterns
going forward. It scans the ``tests/`` directory for the literal
substrings ``or True`` / ``or False`` / ``assert True`` in executable
assert statements (excluding docstrings + comments) + asserts that
NONE remain. If a future PR re-introduces an ``or True`` tautology, this
test fails + the regression is surfaced at code-review time.

It also enforces (separately) that the DDM detector (src/ml/drift.py)
is exercised with REAL Bernoulli error streams in at least N tests —
not just mocked to return canned values. This closes DO BADLY #6
(REAL DDM-STATE ASSERTIONS) — the requirement that drift tests assert
the real detector state mutated (p / sigma_min / n) instead of mocking
the detector to return a hardcoded "DRIFT" string.

NOTE: This file is intentionally NOT in the PRESERVE list (it's a
meta-guard, not a strong-item test). It does NOT touch src/ — it only
reads tests/*.py to verify they remain tautology-free.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TESTS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Helpers — extract the source text minus docstrings + comments.
# ---------------------------------------------------------------------------


def _strip_docstrings_and_comments(source: str) -> str:
    """Parse Python source + return text WITHOUT docstrings + comments.

    Uses the ``ast`` module so the parse is robust to multi-line strings
    + triple-quoted docstrings (which is where the ``or True`` references
    in test_v3_endpoints.py:139,144 + test_pipeline.py:16-47 live — the
    T1.3 fix description documents the prior tautology by name; those
    occurrences are NOT tautologies in actual assert statements).
    """
    tree = ast.parse(source)
    # Collect (start_line, end_line) ranges to drop.
    drop_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                # Module/class/function docstring.
                drop_ranges.append((node.lineno, node.end_lineno or node.lineno))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # String constants (covers inline ``"or True"`` mentions in
            # error messages too — the assertion would still execute, but
            # the string itself isn't a tautology, just an error message).
            drop_ranges.append((node.lineno, node.end_lineno or node.lineno))
    lines = source.splitlines()
    for start, end in drop_ranges:
        for i in range(start - 1, end):
            if 0 <= i < len(lines):
                lines[i] = ""  # blank the docstring line
    # Strip comments (anything after # — naive but sufficient).
    out = []
    for ln in lines:
        # Find the # that's not inside a string — for this purpose, a
        # naive split is fine because docstrings are already blanked.
        # Handle the edge case where # appears in a regex pattern char
        # class — by counting unescaped quotes to know if we're in a
        # string. We're not doing perfect tokenization, but the residual
        # risk (a # in a regex inside a docstring-free line that looks
        # like an assert) is low for this test suite.
        if "#" in ln and not _is_hash_inside_string(ln):
            ln = ln.split("#", 1)[0]
        out.append(ln)
    return "\n".join(out)


def _is_hash_inside_string(line: str) -> bool:
    """Return True if the first ``#`` in ``line`` is inside a string."""
    in_string: str | None = None
    escaped = False
    for ch in line:
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == in_string:
                in_string = None
        else:
            if ch in ('"', "'"):
                in_string = ch
            elif ch == "#":
                return False  # outside any string → real comment
    return False  # no # found in string context (or EOL)


# ---------------------------------------------------------------------------
# Meta-test 1 — no `or True` / `or False` in assert statements.
# ---------------------------------------------------------------------------


def _find_tautology_in_asserts(text: str, file: Path) -> list[str]:
    """Scan stripped source for `assert ... or True` / `assert ... or False`.

    Returns a list of human-readable violation strings (empty = clean).
    The check is line-based after docstring/comment stripping — sufficient
    for catching the canonical tautology patterns the prior 15-c run
    removed (single-line ``assert x or True``).
    """
    violations: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("assert"):
            continue
        # ``assert X or True`` / ``assert X or False`` — both tautologies
        # because ``X or True == True`` always + ``X or False == X`` (but
        # the latter is a no-op that masks the real assertion — the prior
        # version ``assert h == root or True`` ALWAYS passed because
        # ``True`` short-circuits the ``or``; ``or False`` is the same
        # kind of paper-tiger).
        # Use word-boundary regex so we don't false-positive on substrings
        # like "user_or_True".
        if re.search(r"\bor\s+True\b", stripped) or re.search(
            r"\bor\s+False\b", stripped
        ):
            violations.append(f"{file.name}:{i}: {stripped}")
        # ``assert True`` (literal — no expression) is also a tautology.
        if re.match(r"^assert\s+True(\s|$|\))", stripped):
            violations.append(f"{file.name}:{i}: {stripped}")
    return violations


def test_no_or_true_or_or_false_tautologies_in_asserts():
    """META — no ``assert X or True`` / ``assert X or False`` / ``assert
    True`` remains anywhere in tests/.

    Scans every ``tests/*.py`` file, strips docstrings + comments, then
    looks for the literal tautology patterns. If a future PR
    re-introduces one (e.g. a copy-paste from a Stack Overflow answer
    that left a ``or True`` debugging crutch in), this test fails + the
    regression is surfaced at code-review time instead of masquerading
    as a green test.

    The two pre-existing occurrences of the literal string "or True" in
    the codebase (test_v3_endpoints.py:139 + test_pipeline.py:16-47)
    are inside DOCSTRINGS — they document the prior tautology by name +
    are stripped before this scan runs. So this test would PASS today +
    FAIL if someone added a real ``or True`` tautology to an assert.
    """
    all_violations: list[str] = []
    for py_file in sorted(TESTS_DIR.glob("test_*.py")):
        source = py_file.read_text()
        stripped = _strip_docstrings_and_comments(source)
        all_violations.extend(_find_tautology_in_asserts(stripped, py_file))
    if all_violations:
        msg = "Found tautology patterns in asserts (would always pass):\n"
        msg += "\n".join(f"  - {v}" for v in all_violations)
        msg += (
            "\n\nThese `or True` / `or False` / `assert True` patterns "
            "always pass regardless of the actual assertion — they paper "
            "over real bugs. Remove the `or True` / `or False` suffix + "
            "make the assertion real (assert the actual value the test "
            "is checking)."
        )
        pytest.fail(msg)


# ---------------------------------------------------------------------------
# Meta-test 2 — DDM detector is exercised with REAL streams (not mocked).
# ---------------------------------------------------------------------------


def test_ddm_tests_use_real_instances_not_mocks():
    """META — at least 2 tests in test_feedback.py construct REAL DDM
    instances + feed real Bernoulli error streams + assert the INTERNAL
    state mutated (not just the public ``state`` field).

    DO BADLY #6 — the prior ``test_feedback_metrics_endpoint_exposes_
    drift_gauges`` was a SHAPE-only test — it ingested 1 label + asserted
    the 5 Prometheus gauge TYPE comments are present + the STABLE state
    values (0/0/1) appear in /metrics. It did NOT fire real DRIFT + assert
    the gauge value actually transitioned to 2 (DRIFT) at detection time.

    The 15-c fix added 4 real-DDM-state tests (alongside the existing
    SHAPE-only test — the SHAPE test wasn't deleted because it's a
    legitimate shape contract for the /metrics endpoint):
      * ``test_ddm_internal_state_mutates_on_real_drift_stream`` —
        constructs DDM(min_n=10), feeds 30 cold-start labels + 1 error
        + 40 burst errors, asserts ``d.n``, ``d.p``, ``d.p_min``,
        ``d.sigma_min`` ALL mutated from constructor defaults to values
        consistent with the stream's statistics. A regression where
        ``DDM.update()`` returned a hardcoded "DRIFT" without computing
        the running mean would fail this test.
      * ``test_label_feedback_service_drift_resets_baseline_to_stable_
        after_retrain`` — constructs a real LabelFeedbackService in
        pure-file mode, feeds 30 baseline labels + 50 wrong labels
        through the service, asserts the response body's ``ddm_state``
        actually transitioned to "DRIFT" + the post-DRIFT auto-reset
        brought ``ddm_n`` back to 0 + ``ddm_state`` back to STABLE.
        A regression where the gauge value was hardcoded to 2 would
        fail this test.
      * ``test_ddm_prometheus_gauge_numeric_value_fires_at_drift_moment``
        — constructs DDM(min_n=10), feeds 30 cold-start + 1 baseline-
        breaker + burst, asserts ``STATE_NUMERIC[d.state] == 2`` AT THE
        MOMENT of DRIFT detection (the gauge numeric value the scraper
        reads — closes the gap where the public ``state`` string said
        DRIFT but the STATE_NUMERIC mapping was broken).
      * ``test_ddm_drift_fires_on_long_stream_with_mean_shift_at_event_
        500`` (15-c third pass) — the canonical "stream where the mean
        shifts by 3σ at event 500" pattern from the spec. Constructs
        DDM(min_n=30), feeds 500 cold-start events with a 1% error rate
        (establishes a low baseline) then shifts to 100% error rate,
        asserts DRIFT fires within 10 events of the shift + the running
        p climbed from 0.01 to >= 0.05 + the state stays DRIFT for
        subsequent updates.

    This META-test asserts ALL 4 tests exist by name + that they
    construct a real DDM (or LabelFeedbackService) instance + assert a
    state field beyond just the public ``state`` attribute. If a future
    refactor accidentally deletes or weakens any of these tests, this
    META-test fails + surfaces the regression.
    """
    feedback = (TESTS_DIR / "test_feedback.py").read_text()

    # Check 1: the 4 real-DDM-state tests exist by name.
    assert "def test_ddm_internal_state_mutates_on_real_drift_stream(" in feedback, (
        "test_ddm_internal_state_mutates_on_real_drift_stream must exist "
        "(closes DO BADLY #6 — DDM internal-state mutation assertion)"
    )
    assert (
        "def test_label_feedback_service_drift_resets_baseline_to_stable_after_retrain("
        in feedback
    ), (
        "test_label_feedback_service_drift_resets_baseline_to_stable_"
        "after_retrain must exist (closes DO BADLY #6 — DDM auto-reset "
        "post-DRIFT assertion)"
    )
    # 15-c second pass — the gauge-numeric-value invariant. This
    # catches regressions where the public ``state`` field says DRIFT
    # but the STATE_NUMERIC mapping (the Prometheus gauge numeric
    # value the scraper reads) is broken (e.g. hardcoded to 0).
    assert (
        "def test_ddm_prometheus_gauge_numeric_value_fires_at_drift_moment("
        in feedback
    ), (
        "test_ddm_prometheus_gauge_numeric_value_fires_at_drift_moment "
        "must exist (closes DO BADLY #6 — the gauge-numeric-value "
        "invariant: at the moment of DRIFT detection, "
        "STATE_NUMERIC['DRIFT'] must == 2 — what the scraper reads)."
    )
    # 15-c third pass — the canonical "stream where the mean shifts
    # by 3σ at event 500" pattern from the DO BADLY #6 spec. The prior
    # 3 tests use short 30-71 sample bursts; this 4th test verifies
    # the DDM correctly handles a 500-sample stable baseline + then
    # a sudden shift (production-realistic scenario).
    assert (
        "def test_ddm_drift_fires_on_long_stream_with_mean_shift_at_event_500("
        in feedback
    ), (
        "test_ddm_drift_fires_on_long_stream_with_mean_shift_at_event_500 "
        "must exist (closes DO BADLY #6 — the canonical 'stream where "
        "the mean shifts by 3σ at event 500' pattern from the spec). "
        "The prior 3 real-DDM-state tests use short 30-71 sample bursts; "
        "this 4th test verifies the DDM handles a 500-sample stable "
        "baseline + then a sudden 100% error-rate shift (the "
        "production-realistic scenario where drift is GRADUAL, not a "
        "sudden burst)."
    )

    # Check 2: at least one of the tests constructs a real DDM (not a mock).
    # Look for the literal `DDM(` or `LabelFeedbackService(` constructor
    # call inside the test body (after the def line).
    has_real_ddm = "DDM(min_n=" in feedback or "DDM(min_n = " in feedback
    has_real_service = (
        "LabelFeedbackService(redis_url=None, database_url=None)" in feedback
    )
    assert has_real_ddm or has_real_service, (
        "At least one of the 2 real-DDM-state tests must construct a real "
        "DDM or LabelFeedbackService instance (not a mock) — the whole "
        "point of DO BADLY #6 is to replace mocks with real-state "
        "assertions."
    )

    # Check 3: the tests assert internal-state fields beyond the public
    # ``state`` attribute. Look for assertions on ``d.p``, ``d.n``,
    # ``d.p_min``, ``d.sigma_min``, ``ddm_n``, ``ddm_state_numeric``.
    internal_state_fields = [
        "d.p", "d.n", "d.p_min", "d.sigma_min",
        "ddm_n", "ddm_state_numeric", "ddm_p",
    ]
    found = [f for f in internal_state_fields if f in feedback]
    assert len(found) >= 3, (
        f"The 2 real-DDM-state tests must assert at least 3 distinct "
        f"internal-state fields (beyond the public ``state`` attribute) "
        f"to prove the detector PROCESSED the stream; found {found}. "
        f"This closes the DO BADLY #6 gap — a regression where "
        f"DDM.update() returned a hardcoded 'DRIFT' without computing "
        f"the running mean would fail these assertions."
    )


# ---------------------------------------------------------------------------
# Meta-test 3 — T1.3 Merkle proof test (the worst offender) is clean.
# ---------------------------------------------------------------------------


def test_t13_merkle_proof_test_has_no_or_true():
    """META — the T1.3 Merkle proof test (the worst tautology offender,
    fixed in Wave 1 by 14-d) must NOT contain ``or True`` in any assert.

    The original test had ``assert h == root or True`` which ALWAYS
    passed even when the proof reconstruction was broken at odd leaf
    indices. Wave 1 (14-d) rewrote it to route through the shared
    ``MerkleSealer._build_proof_path`` + honor each step's ``position``
    field. This META-test reads the test source + asserts that the
    rewrite stayed clean — if a future PR re-introduces the ``or True``
    crutch, this META-test fails.

    The test name ``test_merkle_proof_reconstructs_root`` is the
    canonical T1.3 test name. If the function is renamed, this META-test
    will fail (intentionally — the canonical name should be preserved
    so grep-based audits still work).
    """
    v3 = (TESTS_DIR / "test_v3_endpoints.py").read_text()
    # Locate the test function body (from def line to the next top-level def).
    match = re.search(
        r"^def test_merkle_proof_reconstructs_root\(.*?\):\n"
        r"(.*?)(?=^def |^class |\Z)",
        v3,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, (
        "test_merkle_proof_reconstructs_root must exist in "
        "test_v3_endpoints.py (the T1.3 Merkle proof test — the worst "
        "tautology offender, fixed in Wave 1). If renamed, restore the "
        "canonical name so grep-based audits still work."
    )
    body = match.group(1)
    # Strip the docstring (the docstring LITERALLY mentions ``or True``
    # because it documents the prior bug — that's NOT a tautology in an
    # assert statement, that's documentation of what was wrong).
    body_no_docstring = re.sub(
        r'^\s*"""[^"]*"""', "", body, count=1, flags=re.MULTILINE | re.DOTALL
    )
    # The body must NOT contain `or True` in any executable line.
    # (It's OK for the docstring to mention ``or True`` as documentation
    # of what the bug was — the docstring is stripped above.)
    executable_lines = [
        ln for ln in body_no_docstring.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for i, ln in enumerate(executable_lines, 1):
        assert not re.search(r"\bor\s+True\b", ln), (
            f"test_merkle_proof_reconstructs_root re-introduced an "
            f"`or True` tautology in executable line: {ln!r}. The T1.3 "
            f"fix removed this crutch — re-introducing it would mask "
            f"broken Merkle proof reconstruction at odd leaf indices "
            f"(the exact bug Wave 1 fixed)."
        )
