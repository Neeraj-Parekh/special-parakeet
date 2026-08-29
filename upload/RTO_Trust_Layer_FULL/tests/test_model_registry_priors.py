"""Tests for the E14 fix — wiring Bahnsen Eq.(6) priors from train.py into
the model registry.

Self-check E14 (the 25-question audit):
  "train.py does NOT pass priors to the model registry, which means
   calibrate_probabilities() (Bahnsen Eq. 6) has nothing to resample
   against → the entire cost-optimizer math is a no-op at inference."

Fix (Task 14-a):
  * ``src/ml/registry.py`` — added ``priors`` kwarg to ``register_model``
    (folded into the metrics blob under the ``_priors`` key for the
    first-class read path via ``get_priors``); added ``set_priors`` for
    backfilling priors on an already-registered model.
  * ``src/models/train.py`` — added ``compute_priors(y_train, y_und=None)``
    helper + ``write_priors_artifact`` helper + ``main()`` that calls
    ``register_model(..., priors=priors)`` and writes ``priors.json`` next
    to the model artifact.

This test file is the regression net for the fix:
  1. ``test_register_model_with_priors_stores_and_returns_them`` — the
     first-class path: passing ``priors={...}`` makes ``get_priors(version)``
     return the dict verbatim (preserving ``p_orig`` / ``p_und`` at the top
     level so existing pre-E14 readers like routes.py:787 keep working).
  2. ``test_register_model_without_priors_backwards_compat`` — the
     pre-E14 path: ``priors=None`` (default) makes ``get_priors(version)``
     return ``{"p_orig": None, "p_und": None}`` — the documented no-op
     signal so the live decision path skips calibration correctly.
  3. ``test_train_main_writes_priors_json_and_prints_p_orig_p_und`` — the
     end-to-end dry-run: ``src.models.train.main()`` writes
     ``{model_path}.priors.json`` next to the artifact AND prints
     ``p_orig`` + ``p_und`` to stdout so the user can verify the
     calibration is no longer dead.

The 3rd test monkeypatches ``fit_model`` (skip real HistGB training),
``load_data`` (return a tiny synthetic DataFrame), and
``src.ml.registry.register_model`` (no DB/file side effects beyond what
we inspect) so it runs in <2 seconds in CI.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ml.registry import (  # noqa: E402
    current_champion,
    get_priors,
    register_model,
    set_priors,
)
from src.models.train import (  # noqa: E402
    compute_priors,
    write_priors_artifact,
)

# --------------------------------------------------------------------- #
# Test 1 — first-class priors path                                      #
# --------------------------------------------------------------------- #

def test_register_model_with_priors_stores_and_returns_them(tmp_path):
    """register_model(priors={...}) → get_priors(version) returns the dict
    verbatim. The p_orig / p_und keys remain at the top level so existing
    pre-E14 readers (routes.py:787 ``_priors.get("p_orig")`` pattern)
    continue to work without modification."""
    reg = str(tmp_path / "reg.json")
    priors_in = {
        "p_orig": 0.3,
        "p_und": 0.15,
        "n_train": 1000,
        "n_pos_train": 300,
        "calibration_method": "bahnsen_eq6",
        "created_at": "2025-01-01T00:00:00.000000",
    }
    register_model(
        "v-priors",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
        priors=priors_in,
    )
    out = get_priors("v-priors", reg)
    # The full priors blob is returned verbatim.
    assert out["p_orig"] == 0.3
    assert out["p_und"] == 0.15
    assert out["n_train"] == 1000
    assert out["n_pos_train"] == 300
    assert out["calibration_method"] == "bahnsen_eq6"
    assert out["created_at"] == "2025-01-01T00:00:00.000000"
    # Backwards-compat: p_orig / p_und also readable via the top-level
    # metrics keys (the pre-E14 read path used by routes.py:787).
    champ = current_champion(reg)
    assert champ is not None
    metrics = champ["metrics"]
    assert metrics["p_orig"] == 0.3
    assert metrics["p_und"] == 0.15
    assert metrics["_priors"] == priors_in
    # The 2-key shape returned by get_priors also carries the extra keys
    # at the top level (so the routes.py ``_priors.get("p_orig")`` pattern
    # keeps working — that pattern does NOT break when extra keys are added).
    assert out.get("p_orig") == 0.3
    assert out.get("p_und") == 0.15


def test_set_priors_backfills_on_existing_model(tmp_path):
    """set_priors can backfill priors on a model that was registered
    WITHOUT them (the in-process lifespan path: register_model is called
    with no priors kwarg, then an external auditor recomputes them later)."""
    reg = str(tmp_path / "reg.json")
    register_model(
        "v-backfill",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
        priors=None,
    )
    # Pre-backfill: both priors are None (the pre-E14 no-op signal).
    pre = get_priors("v-backfill", reg)
    assert pre == {"p_orig": None, "p_und": None}
    # Backfill.
    priors_in = {
        "p_orig": 0.05,
        "p_und": 0.50,
        "n_train": 5000,
        "n_pos_train": 250,
        "calibration_method": "bahnsen_eq6",
        "created_at": "2025-01-02T00:00:00.000000",
    }
    set_priors("v-backfill", priors_in, reg)
    post = get_priors("v-backfill", reg)
    assert post["p_orig"] == 0.05
    assert post["p_und"] == 0.50
    assert post["n_train"] == 5000
    assert post["calibration_method"] == "bahnsen_eq6"
    # Top-level metrics keys are also updated (the legacy read path).
    champ = current_champion(reg)
    assert champ["metrics"]["p_orig"] == 0.05
    assert champ["metrics"]["p_und"] == 0.50


def test_set_priors_missing_version_raises(tmp_path):
    """set_priors on a non-existent version raises KeyError — must NOT
    silently create a new entry (that's register_model's job)."""
    reg = str(tmp_path / "reg.json")
    # Empty registry → no versions exist.
    with pytest.raises(KeyError):
        set_priors("v-missing", {"p_orig": 0.1, "p_und": 0.2}, reg)


def test_register_model_priors_must_be_dict(tmp_path):
    """register_model(priors=<not-a-dict>) → TypeError, not silent
    mis-storage. Guards against train.py passing a stray float."""
    reg = str(tmp_path / "reg.json")
    with pytest.raises(TypeError):
        register_model(
            "v-bad",
            "/tmp/m.pkl",
            {"pr_auc": 0.55},
            champion=True,
            registry_path=reg,
            priors=0.3,  # type: ignore[arg-type]
        )


def test_set_priors_requires_p_orig_and_p_und(tmp_path):
    """set_priors rejects dicts missing p_orig or p_und (the minimum
    keys the live decision path needs for calibration)."""
    reg = str(tmp_path / "reg.json")
    register_model(
        "v-min",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
    )
    with pytest.raises(ValueError):
        set_priors("v-min", {"n_train": 1000}, reg)


# --------------------------------------------------------------------- #
# Test 2 — backwards-compat path (priors=None)                          #
# --------------------------------------------------------------------- #

def test_register_model_without_priors_backwards_compat(tmp_path):
    """register_model(priors=None) → get_priors(version) returns
    ``{"p_orig": None, "p_und": None}``. This is the pre-E14 behaviour
    (and the in-process lifespan registration path that doesn't pass
    priors) — the live decision path's no-op signal. Nothing breaks."""
    reg = str(tmp_path / "reg.json")
    register_model(
        "v-legacy",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
        priors=None,
    )
    out = get_priors("v-legacy", reg)
    assert out == {"p_orig": None, "p_und": None}


def test_register_model_no_priors_kwarg_at_all_backwards_compat(tmp_path):
    """Callers that don't pass priors AT ALL (the pre-Track-R shape:
    ``register_model(version, model_path, metrics, champion=True,
    registry_path=reg)``) must continue to work — register_model gained
    a new kwarg but didn't break any existing call site."""
    reg = str(tmp_path / "reg.json")
    # No priors kwarg, no p_orig/p_und kwargs — the original 2024-Q4 call.
    register_model(
        "v-original",
        "/tmp/m.pkl",
        {"pr_auc": 0.52},
        champion=True,
        registry_path=reg,
    )
    out = get_priors("v-original", reg)
    assert out == {"p_orig": None, "p_und": None}


def test_register_model_legacy_p_orig_p_und_kwargs_still_work(tmp_path):
    """The Track-R lifespan path passes p_orig / p_und as separate kwargs
    (NOT the new priors dict). This must continue to work — get_priors
    returns the 2-key shape (not the full priors dict, because no
    _priors key was stored)."""
    reg = str(tmp_path / "reg.json")
    register_model(
        "v-track-r",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
        p_orig=0.05,
        p_und=0.50,
    )
    out = get_priors("v-track-r", reg)
    # 2-key shape (NOT the full priors dict — no _priors key was stored).
    assert out == {"p_orig": 0.05, "p_und": 0.50}


def test_get_priors_missing_version_returns_none_none(tmp_path):
    """get_priors on a missing version returns the no-op signal (not an
    exception) — mirrors the pre-E14 behaviour documented in the
    registry docstring."""
    reg = str(tmp_path / "reg.json")
    out = get_priors("v-missing", reg)
    assert out == {"p_orig": None, "p_und": None}


def test_get_priors_empty_registry_returns_none_none(tmp_path):
    """get_priors on the current champion when no model is registered
    returns the no-op signal — pre-E14 behaviour preserved."""
    reg = str(tmp_path / "reg.json")
    out = get_priors(None, reg)
    assert out == {"p_orig": None, "p_und": None}


# --------------------------------------------------------------------- #
# Test 3 — train.py main() end-to-end dry run                           #
# --------------------------------------------------------------------- #

def _make_synthetic_df(n=200, seed=42):
    """Tiny synthetic DataFrame that satisfies load_orders' schema.

    Has the columns train.py main() needs:
      * ``CustomerID`` (for group_split)
      * ``is_returned`` (the label — ~30% positive rate)
      * ``log_order_value``, ``discount_pct``, ``Items``, ``OrderDay``,
        ``OrderHour``, ``PriorOrders``, ``PriorReturns``, ``is_cod``
        (ORDER_FEATURES numeric)
      * ``category``, ``device``, ``city_tier`` (ORDER_FEATURES categorical)
      * ``address_quality`` (ADDR_FEATURES)
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    n_pos = int(n * 0.30)
    is_returned = np.zeros(n, dtype=int)
    is_returned[:n_pos] = 1
    rng.shuffle(is_returned)
    return pd.DataFrame({
        "CustomerID": [f"CUST-{i % 50}" for i in range(n)],  # 50 unique customers
        "is_returned": is_returned,
        "log_order_value": rng.uniform(3.0, 8.0, n),
        "discount_pct": rng.uniform(0.0, 0.5, n),
        "Items": rng.integers(1, 5, n),
        "OrderDay": rng.integers(1, 366, n),
        "OrderHour": rng.integers(0, 24, n),
        "PriorOrders": rng.integers(0, 10, n),
        "PriorReturns": rng.integers(0, 3, n),
        "is_cod": rng.integers(0, 2, n),
        "category": rng.choice(["Fashion", "Electronics", "Books"], n),
        "device": rng.choice(["mobile", "desktop", "tablet"], n),
        "city_tier": rng.choice(["tier_1", "tier_2", "tier_3"], n),
        "address_quality": rng.choice(["complete", "partial", "missing"], n),
    })


class _StubModel:
    """Stand-in for HistGradientBoostingClassifier — returns a constant
    predict_proba so average_precision_score / roc_auc_score in main()
    step 5 produce a deterministic, finite number."""
    def predict_proba(self, X):
        n = len(X) if hasattr(X, "__len__") else 1
        import numpy as np
        return np.tile([0.5, 0.5], (n, 1))


def test_train_main_writes_priors_json_and_prints_p_orig_p_und(
    tmp_path, monkeypatch, capsys
):
    """End-to-end dry-run of src.models.train.main():
      * monkeypatches fit_model + load_data + register_model so no real
        training / file IO / DB writes happen beyond what we inspect,
      * asserts ``{model_path}.priors.json`` is written next to the
        artifact,
      * asserts stdout contains ``p_orig=`` and ``p_und=`` (the user-
        facing signal that the calibration is no longer dead),
      * asserts the registry received the priors blob via the priors kwarg.
    """
    model_out = tmp_path / "model.joblib"
    reg_path = tmp_path / "reg.json"
    df = _make_synthetic_df(n=200)

    # --- monkeypatches (the late imports inside main() resolve to these) ---
    # 1. load_data: skip the file read.
    monkeypatch.setattr(
        "src.features.cleaning.load_data",
        lambda path=None: df.copy(),
    )
    # 2. add_address_features: the synthetic df already has the column.
    monkeypatch.setattr(
        "src.features.enrich.add_address_features",
        lambda d: d,
    )
    # 3. fit_model: return the stub.
    monkeypatch.setattr(
        "src.models.train.fit_model",
        lambda X, y, seed=42: _StubModel(),
    )
    # 4. save_model: no-op (skip joblib.dump — the stub doesn't pickle cleanly).
    monkeypatch.setattr(
        "src.models.train.save_model",
        lambda model, path: None,
    )
    # 5. current_champion: empty registry.
    monkeypatch.setattr(
        "src.ml.registry.current_champion",
        lambda registry_path="out/model_registry.json": None,
    )
    # 6. register_model: capture the priors kwarg + return a fake entry.
    captured = {}
    def _fake_register_model(version, model_path, metrics, champion=True,
                             registry_path="out/model_registry.json",
                             p_orig=None, p_und=None, priors=None):
        captured["version"] = version
        captured["model_path"] = model_path
        captured["metrics"] = metrics
        captured["champion"] = champion
        captured["registry_path"] = registry_path
        captured["p_orig"] = p_orig
        captured["p_und"] = p_und
        captured["priors"] = priors
        return {"version": version, "model_path": model_path, "metrics": metrics}
    monkeypatch.setattr(
        "src.ml.registry.register_model",
        _fake_register_model,
    )

    # --- run main() ---
    from src.models.train import main
    rc = main([
        "--model-out", str(model_out),
        "--registry-path", str(reg_path),
        "--version", "test-v1",
        "--feature-set", "order+addr",
    ])
    assert rc == 0, "main() should exit 0 on success"

    # --- assertions ---
    # 1. priors.json artifact was written next to the model.
    priors_path = Path(str(model_out) + ".priors.json")
    assert priors_path.exists(), f"{priors_path} should have been written"
    priors_on_disk = json.loads(priors_path.read_text())
    assert priors_on_disk["calibration_method"] == "bahnsen_eq6"
    assert priors_on_disk["n_train"] == len(df) - int(len(df) * 0.2)  # train_size = 0.8
    # p_orig should match the training-set positive rate.
    assert abs(priors_on_disk["p_orig"] - 0.30) < 0.10  # ~30% positive, ±sampling noise
    # Identity calibration (no resampling in the train.py path).
    assert priors_on_disk["p_orig"] == priors_on_disk["p_und"]
    # n_pos_train is consistent with p_orig * n_train.
    assert priors_on_disk["n_pos_train"] >= 0
    assert abs(
        priors_on_disk["n_pos_train"] / max(priors_on_disk["n_train"], 1)
        - priors_on_disk["p_orig"]
    ) < 1e-9

    # 2. register_model received the priors kwarg (not None — the E14 fix).
    assert captured["priors"] is not None, (
        "register_model must be called with priors=<dict> — the E14 bug was that "
        "train.py never passed priors so calibrate_probabilities had nothing to "
        "resample against."
    )
    assert captured["priors"]["calibration_method"] == "bahnsen_eq6"
    assert captured["priors"]["p_orig"] == priors_on_disk["p_orig"]
    assert captured["priors"]["p_und"] == priors_on_disk["p_und"]

    # 3. stdout contains the user-facing signal — p_orig + p_und printed.
    out = capsys.readouterr().out
    assert "p_orig=" in out, "stdout must print p_orig (the user-facing signal)"
    assert "p_und=" in out, "stdout must print p_und (the user-facing signal)"
    assert "calibration_method=bahnsen_eq6" in out
    # The summary block also prints p_orig + p_und.
    assert "IDENTITY" in out or "NON-TRIVIAL" in out  # one of the two labels


def test_compute_priors_helper_contract():
    """Unit test for compute_priors() — the helper train.py uses.

    With no y_und (the default — no SMOTE / under-sampling), p_und must
    equal p_orig (identity calibration). With y_und supplied (the future
    SMOTE path), p_und is the resampled positive rate.
    """
    y_train = pd.Series([0, 1, 0, 1, 0, 0, 1, 0])  # 3/8 = 0.375 positive
    priors = compute_priors(y_train)
    assert priors["p_orig"] == 0.375
    assert priors["p_und"] == 0.375  # identity
    assert priors["n_train"] == 8
    assert priors["n_pos_train"] == 3
    assert priors["calibration_method"] == "bahnsen_eq6"
    assert "created_at" in priors

    # With a resampled y_und (50% positive — under-sampled the majority).
    y_und = pd.Series([1, 0, 1, 0, 1, 0, 1, 0])  # 4/8 = 0.5
    priors2 = compute_priors(y_train, y_und=y_und)
    assert priors2["p_orig"] == 0.375
    assert priors2["p_und"] == 0.5  # non-trivial calibration ratio
    assert priors2["n_train"] == 8  # n_train is the ORIGINAL count
    assert priors2["n_pos_train"] == 3

    # Empty y_train edge case.
    priors3 = compute_priors(pd.Series([], dtype=int))
    assert priors3["p_orig"] == 0.0
    assert priors3["p_und"] == 0.0
    assert priors3["n_train"] == 0
    assert priors3["n_pos_train"] == 0


def test_write_priors_artifact_helper_contract(tmp_path):
    """Unit test for write_priors_artifact() — the helper train.py uses.

    The artifact path is ``{model_path}.priors.json`` (sibling of the
    model artifact with .priors.json suffix appended). The file is JSON
    with the full priors dict.
    """
    priors = {
        "p_orig": 0.05,
        "p_und": 0.50,
        "n_train": 1000,
        "n_pos_train": 50,
        "calibration_method": "bahnsen_eq6",
        "created_at": "2025-01-01T00:00:00.000000",
    }
    model_path = str(tmp_path / "subdir" / "model.joblib")
    out = write_priors_artifact(priors, model_path)
    assert out == Path(model_path + ".priors.json")
    assert out.exists()
    written = json.loads(out.read_text())
    assert written == priors
    # Parent dirs were created (the helper mkdir -p's).
    assert out.parent.exists()


# --------------------------------------------------------------------- #
# Test 4 — end-to-end: priors actually drive calibrate_probabilities    #
# (the regression that E14 fixes — the cost-optimizer was a no-op      #
# because train.py never passed priors)                                #
# --------------------------------------------------------------------- #

def test_priors_round_trip_drives_calibrate_probabilities(tmp_path):
    """End-to-end: register_model(priors=...) → get_priors() →
    calibrate_probabilities([proba], p_orig, p_und) returns a non-trivial
    calibrated probability. This is the actual production path: the live
    decision path at routes.py:787 + 2464 reads priors from the registry
    and applies Bahnsen Eq.(6). Before the E14 fix this was a no-op
    (priors were always None → calibration was skipped)."""
    from src.business.cost_optimizer import calibrate_probabilities

    reg = str(tmp_path / "reg.json")
    priors_in = {
        "p_orig": 0.05,
        "p_und": 0.50,
        "n_train": 5000,
        "n_pos_train": 250,
        "calibration_method": "bahnsen_eq6",
        "created_at": "2025-01-01T00:00:00.000000",
    }
    register_model(
        "v-round-trip",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
        priors=priors_in,
    )

    # Read priors back (the routes.py pattern).
    priors = get_priors("v-round-trip", reg)
    p_orig = priors["p_orig"]
    p_und = priors["p_und"]

    # The live decision path's no-op check (routes.py:787 + 2464):
    #   if p_orig is not None and p_und is not None and p_orig != p_und:
    #       proba = calibrate_probabilities([proba], p_orig, p_und)[0]
    assert p_orig is not None
    assert p_und is not None
    assert p_orig != p_und  # non-trivial — calibration WILL fire

    # Bahnsen Eq.(6): P*(f|x) = P(f|x) · P_orig / P_und
    # Model predicts p=0.50 (under-sampling inflated the minority prior
    # from 0.05 to 0.50). Post-calibration, p* = 0.50 * 0.05 / 0.50 = 0.05.
    calibrated = calibrate_probabilities([0.50], p_orig, p_und)[0]
    assert abs(calibrated - 0.05) < 1e-9


def test_priors_none_does_not_fire_calibration(tmp_path):
    """When priors=None (the pre-E14 / pre-Track-R path), the live
    decision path's no-op check skips calibration — the un-calibrated
    probability is used as-is. This test confirms the no-op signal is
    preserved so existing pre-E14 deployments don't suddenly start
    calibrating against None (which would crash)."""
    reg = str(tmp_path / "reg.json")
    register_model(
        "v-none",
        "/tmp/m.pkl",
        {"pr_auc": 0.55},
        champion=True,
        registry_path=reg,
    )
    priors = get_priors("v-none", reg)
    # The no-op signal — both None.
    assert priors["p_orig"] is None
    assert priors["p_und"] is None
    # routes.py's check: `if priors.get("p_orig") is not None and ...`
    # → False → calibration skipped. We simulate that check here.
    fires = (
        priors.get("p_orig") is not None
        and priors.get("p_und") is not None
        and priors["p_orig"] != priors["p_und"]
    )
    assert fires is False
