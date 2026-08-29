"""Tests for the cross-process state persistence layer (DO BADLY #3 —
Subagent 15-b, Wave 3 of the RTO Trust Layer).

The in-memory dict fallbacks (file-mode, when ``DATABASE_URL`` is unset)
lost state across process restarts. Every redeploy reset:

  * the mandate counters (``cumulative_monthly`` / ``cumulative_24h`` /
    ``last_activity``) — blowing the ₹15k/month cap (the cumulative
    spend silently reset to 0), clearing the 24h cooling window, and
    resurrecting revoked mandates whose 6-month inactivity had tripped.
  * the override nonce cache (``_override_nonce_cache`` TTLCache) —
    opening a replay window where a captured override request could be
    replayed verbatim within the 5-minute timestamp window after a
    redeploy.

The fix wraps the 3 mandate counter dicts in a ``_FileState`` helper
that persists to JSON on mutate (throttled to max once per 5 sec to
avoid I/O thrash) + loads on module import. The override nonce cache
gains a parallel ``_persist_nonce`` helper that appends consumed nonce
hashes to ``override_nonces_state.json`` + a ``_load_nonces_from_disk``
that re-populates the cache at module import.

Test plan (7 tests):
* ``test_mandate_counter_persistence_survives_simulated_restart`` —
  set a value in ``_cumulative_monthly``, force-flush, create a NEW
  ``_FileState`` pointing at the same file (simulates a process
  restart that re-loads state from disk); assert the value survives.
* ``test_mandate_24h_events_persistence_survives_simulated_restart`` —
  same for the rolling 24h txn log (list of (ts, amount) tuples).
* ``test_mandate_last_activity_persistence_survives_simulated_restart`` —
  same for the per-mandate last-activity timestamp.
* ``test_override_nonce_persistence_survives_simulated_restart`` —
  consume a nonce via ``_check_and_consume_override_nonce`` (file-mode
  path), simulate a process restart (clear in-memory cache, re-run
  ``_load_nonces_from_disk``), assert the same nonce → 409 (replay
  protection survives the restart).
* ``test_throttle_limits_disk_writes_under_burst`` — 100 rapid
  mutations on a fresh ``_FileState`` → assert < 20 ``os.replace``
  calls (the 5-second throttle suppresses writes within the window).
* ``test_force_persist_bypasses_throttle`` — ``force=True`` bypasses
  the throttle so explicit flushes (test isolation, the
  ``reset_upi_counters`` clear, process shutdown hooks) write
  immediately regardless of the throttle window.
* ``test_override_nonce_cache_lru_cap_at_10000`` — insert 10_001
  nonces into the ``_override_nonce_cache`` (TTLCache with
  ``maxsize=10_000``); assert the cache stays at 10_000 entries
  (LRU eviction per the bounded-cache mandate).

Implementation note — the test accesses the mandates module's state
via ``import src.api.mandates as mandates_mod`` + attribute access
(``mandates_mod._mandate_state`` etc.) at call time, NOT via
top-of-file ``from src.api.mandates import _mandate_state``. The
reason: the test_mandate_concurrency.py file (which runs BEFORE this
file in the full pytest suite) calls ``importlib.reload(mandates_mod)``
in its ``test_mandates_module_imports_clean_after_c8c9c10_refactor``
test. The reload RE-EXECUTES the module code, RE-BINDING
``_mandate_state`` to a NEW instance. A top-of-file
``from src.api.mandates import _mandate_state`` captures the OLD
instance + never updates; calling ``_persist_to_disk(force=True)``
on the OLD instance would write the OLD ``_data`` (which is stale,
post-reload). Attribute access at call time always returns the CURRENT
binding (post-reload NEW instance), which is what we want.

File mode is used throughout (no ``DATABASE_URL`` → file-mode fallback
for both the mandate counters and the override nonce cache). The
Postgres paths (C8/C9/C10 from 14-b; A2 from 14-d) are UNAFFECTED —
the file persistence is ONLY the file-mode fallback.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import only the stable public + class-level symbols (NOT the
# module-level singletons like ``_mandate_state`` — those are
# re-bound by ``importlib.reload`` in test_mandate_concurrency.py
# and would be captured stale here).
from src.api.mandates import (  # noqa: E402
    _FileState,
    reset_upi_counters,
)
from src.api.routes import (  # noqa: E402
    _check_and_consume_override_nonce,
    _clear_override_nonce_cache,
    _load_nonces_from_disk,
    _override_nonce_cache_lock,
    _persist_nonce,
    _reset_nonces_conn,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    """Use a tmp dir for ``RTO_STATE_DIR`` so tests don't pollute the
    real ``out/`` dir.

    The mandate module's ``_mandate_state`` was created at import time
    (so its ``_load_from_disk`` ran against the OLD env value); but
    ``_persist_to_disk`` + the routes' ``_persist_nonce`` +
    ``_load_nonces_from_disk`` all re-read the env var at call time, so
    setting ``RTO_STATE_DIR`` via monkeypatch directs new writes to the
    new dir.

    The throttle timestamp on the module-level ``_mandate_state`` is
    reset to ``0.0`` (looked up via attribute access at call time so
    we get the post-reload instance) so the next persist isn't throttled
    by a prior test's persist.
    """
    import src.api.mandates as mandates_mod

    monkeypatch.setenv("RTO_STATE_DIR", str(tmp_path))
    # Reset the throttle timestamp on the module-level _FileState so
    # the next persist isn't throttled by a prior test's persist (the
    # 5-second throttle window would otherwise suppress the first
    # persist of THIS test). Attribute access gets the CURRENT binding
    # (post-reload if test_mandate_concurrency ran first).
    mandates_mod._mandate_state._last_persist = 0.0
    yield tmp_path
    # Cleanup: clear the module-level mandate state + reset the
    # override nonce cache so the next test starts fresh.
    reset_upi_counters()
    _clear_override_nonce_cache()


@pytest.fixture
def no_database_url(monkeypatch):
    """Ensure ``DATABASE_URL`` is unset for the duration of the test
    so the file-mode fallback path is exercised (not the Postgres
    path). Restores the original value on teardown."""
    old_db_url = os.environ.get("DATABASE_URL")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _reset_nonces_conn()
    yield
    if old_db_url is not None:
        os.environ["DATABASE_URL"] = old_db_url
    _reset_nonces_conn()


# ============================================================================
# Mandate counter persistence (file-mode) — survives a simulated
# process restart (re-instantiate ``_FileState`` pointing at the same
# file).
# ============================================================================


def test_mandate_counter_persistence_survives_simulated_restart(tmp_state_dir):
    """The ``cumulative_monthly`` counter survives a process restart.

    Set a value via the module-level ``_cumulative_monthly`` view,
    force-flush, then create a NEW ``_FileState`` instance pointing at
    the same file (simulates a process restart that re-loads state
    from disk via ``__init__``'s ``_load_from_disk`` call). The new
    instance must see the same value.

    This is the headline DO-BADLY-#3 fix for the mandate side: a
    redeploy no longer resets the ₹15k/month cap to 0.
    """
    # Attribute access at call time — gets the CURRENT binding
    # (post-reload if test_mandate_concurrency ran first). A top-of-
    # file ``from src.api.mandates import _cumulative_monthly`` would
    # capture the pre-reload view + never update, leaving us writing
    # to the OLD _FileState instance's _data while the rest of the
    # suite (reset_upi_counters etc.) operates on the NEW instance.
    import src.api.mandates as mandates_mod

    # Clear the module-level state so we start from a clean slate.
    reset_upi_counters()
    # Set a value via the module-level view (triggers throttled persist).
    mandates_mod._cumulative_monthly["mandate-XYZ"] = 12345.67
    # Force-flush so the file actually has the data (the throttled
    # default would skip the write within 5 sec of the reset's flush).
    mandates_mod._mandate_state._persist_to_disk(force=True)
    # Sanity check: the file actually exists + contains the value.
    state_path = tmp_state_dir / "mandate_counters_state.json"
    assert state_path.exists(), (
        "The force-flush must have written the file; the test cannot "
        "verify restart-persistence without an actual on-disk file."
    )
    import json
    with open(state_path) as f:
        on_disk = json.load(f)
    assert "cumulative_monthly" in on_disk, (
        f"The persisted file must have a 'cumulative_monthly' key; "
        f"got keys={list(on_disk.keys())}."
    )
    assert on_disk["cumulative_monthly"]["mandate-XYZ"] == 12345.67

    # Simulate a process restart: NEW _FileState instance pointing at
    # the same file name. The __init__ calls _load_from_disk, which
    # should pick up the persisted state.
    restarted = _FileState("mandate_counters_state.json")
    restarted_view = restarted.sub("cumulative_monthly")
    assert restarted_view["mandate-XYZ"] == 12345.67, (
        "The cumulative_monthly counter must survive a process "
        "restart (the whole point of the DO BADLY #3 fix — every "
        "redeploy used to reset the ₹15k/month cap to 0)."
    )


def test_mandate_24h_events_persistence_survives_simulated_restart(tmp_state_dir):
    """The 24h rolling txn log (``cumulative_24h``) survives a process
    restart.

    The log is a list of ``(timestamp, amount)`` tuples per mandate.
    On JSON round-trip, tuples become lists; the test accounts for
    that (the in-memory representation can be either).
    """
    import src.api.mandates as mandates_mod

    reset_upi_counters()
    base_ts = time.time()
    events = [
        (base_ts, 100.0),
        (base_ts + 1, 200.0),
        (base_ts + 2, 300.0),
    ]
    # Use a pure __setitem__ so the persist hook fires (the
    # setdefault+append pattern would mutate the list in place without
    # triggering the persist — see the _SubStateView docstring).
    mandates_mod._cumulative_24h["mandate-EVT"] = events
    mandates_mod._mandate_state._persist_to_disk(force=True)

    restarted = _FileState("mandate_counters_state.json")
    restarted_view = restarted.sub("cumulative_24h")
    loaded = restarted_view["mandate-EVT"]
    # JSON round-trip turns tuples into lists — compare by element.
    assert len(loaded) == 3, (
        f"The 24h events list should have 3 entries after restart; "
        f"got {len(loaded)}."
    )
    # Coerce both sides to lists of (ts, amount) pairs for comparison.
    assert [tuple(e) for e in loaded] == [tuple(e) for e in events], (
        "The 24h rolling txn log must survive a process restart — "
        "the OC-201B ₹5k cooling-window check depends on the prior "
        "txns being present after a redeploy."
    )


def test_mandate_last_activity_persistence_survives_simulated_restart(tmp_state_dir):
    """The per-mandate ``last_activity`` timestamp survives a process
    restart.

    The OC-201B 6-month inactivity auto-revoke depends on this
    timestamp being present after a redeploy — without persistence,
    a mandate whose 6-month inactivity had tripped could be
    resurrected by a redeploy (the timestamp resets to 0 → the
    inactivity check thinks the mandate is freshly active).
    """
    import src.api.mandates as mandates_mod

    reset_upi_counters()
    ts = time.time() - 3600  # 1 hour ago
    mandates_mod._last_activity["mandate-ACT"] = ts
    mandates_mod._mandate_state._persist_to_disk(force=True)

    restarted = _FileState("mandate_counters_state.json")
    restarted_view = restarted.sub("last_activity")
    assert restarted_view["mandate-ACT"] == ts, (
        "The last_activity timestamp must survive a process restart — "
        "the OC-201B 6-month inactivity auto-revoke depends on the "
        "timestamp being present + accurate after a redeploy."
    )


# ============================================================================
# Override nonce persistence (file-mode) — survives a simulated
# process restart (re-run ``_load_nonces_from_disk`` to pick up the
# persisted nonces).
# ============================================================================


def test_override_nonce_persistence_survives_simulated_restart(
    tmp_state_dir, no_database_url
):
    """A consumed override nonce hash survives a process restart — a
    captured override request cannot be replayed after a redeploy.

    The A2 fix (14-d) inserts the nonce hash into the in-memory LRU+TTL
    cache (file-mode) on first sighting → 200; a second sighting →
    409. But the in-memory cache was wiped on every redeploy, opening
    a replay window. The 15-b fix persists the consumed nonce hash to
    ``override_nonces_state.json`` + re-loads on module import so the
    replay protection survives the restart.
    """
    # Access the module-level cache via attribute access (same reason
    # as the mandate tests above — the routes module doesn't get
    # reloaded by the test suite, but consistency is safer).
    import src.api.routes as routes_mod

    # Clear both the in-memory cache + the file (the fixture's
    # _clear_override_nonce_cache call does both, but the tmp_state_dir
    # fixture runs first so the file path is already tmp_path).
    _clear_override_nonce_cache()

    # Consume a fresh nonce — first sighting, no exception. The
    # _check_and_consume_override_nonce helper inserts into the
    # in-memory cache + calls _persist_nonce (file-mode path).
    nonce_hash = hashlib.sha256(b"test-nonce-restart-1").hexdigest()
    _check_and_consume_override_nonce({}, nonce_hash, None)
    # The in-memory cache now has the entry.
    assert nonce_hash in routes_mod._override_nonce_cache

    # Force-flush the persist file (the throttled default might skip
    # the write within 5 sec of the clear's reset). This makes the
    # test robust against throttle-timing flakes.
    _persist_nonce(nonce_hash, force=True)

    # Verify the file actually has the nonce hash.
    state_path = tmp_state_dir / "override_nonces_state.json"
    assert state_path.exists(), (
        "The force-flush must have written the nonce file; the test "
        "cannot verify restart-persistence without an actual on-disk file."
    )
    import json
    with open(state_path) as f:
        on_disk = json.load(f)
    assert isinstance(on_disk, list)
    assert nonce_hash in on_disk

    # Simulate a process restart: clear the in-memory cache (but
    # DON'T delete the file — the _clear_override_nonce_cache helper
    # would delete the file too, which is NOT what a real restart
    # does). Then re-run _load_nonces_from_disk (the same function
    # that runs at module import).
    with _override_nonce_cache_lock:
        routes_mod._override_nonce_cache.clear()
    _load_nonces_from_disk()

    # The cache should now be re-populated from the file — the
    # persisted nonce hash should be present.
    assert nonce_hash in routes_mod._override_nonce_cache, (
        "After a process restart, the override nonce cache should be "
        "re-populated from the file so the same nonce still gets "
        "rejected (replay protection survives the restart — this is "
        "the whole point of the DO BADLY #3 fix on the routes side)."
    )

    # Replay the same nonce — must get 409 (replay detected). The
    # persisted nonce hash is in the cache, so the second sighting
    # trips the replay check.
    with pytest.raises(HTTPException) as exc:
        _check_and_consume_override_nonce({}, nonce_hash, None)
    assert exc.value.status_code == 409
    assert "replay detected" in str(exc.value.detail), (
        "The replayed nonce must be rejected with 409 'replay detected' "
        "even after a process restart — this is the A2 + 15-b combined "
        "guarantee (no replay window opens on redeploy)."
    )


# ============================================================================
# Throttle test — 100 rapid mutations → < 20 disk writes (the 5-sec
# throttle suppresses writes within the window).
# ============================================================================


def test_throttle_limits_disk_writes_under_burst(tmp_state_dir, monkeypatch):
    """100 rapid mutations on a fresh ``_FileState`` → fewer than 20
    actual disk writes (the 5-second throttle skips writes within the
    window).

    The first mutation writes (since ``_last_persist`` is ``0.0`` on a
    fresh instance); the next 99 mutations are within the 5-second
    throttle window → skipped. The assertion ``< 20`` is generous
    headroom (the expected count is exactly 1).
    """
    # Fresh _FileState instance (not the module-level one) so the
    # throttle window is wide open (no prior persist to set
    # _last_persist to "now").
    fs = _FileState("test_throttle.json")
    # Reset _last_persist explicitly (the __init__ calls _load_from_disk
    # which doesn't touch _last_persist; but a prior test in the same
    # tmp_state_dir — there isn't one, since tmp_path is per-test —
    # could have left a file. This is belt-and-suspenders).
    fs._last_persist = 0.0
    sub = fs.sub("cumulative_monthly")

    # Count os.replace calls. The _FileState._persist_to_disk uses
    # os.replace(tmp, path) as the atomic-write step — this is the
    # cleanest signal of "actual disk write happened" (vs "persist
    # was called but throttled"). The throttle check is upstream, so
    # only writes that pass the throttle reach os.replace.
    write_count = {"n": 0}
    original_replace = os.replace

    def counting_replace(src, dst):
        write_count["n"] += 1
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", counting_replace)

    # 100 rapid mutations (all within ~0.01 sec — well within the
    # 5-second throttle window).
    for i in range(100):
        sub[f"key_{i:03d}"] = float(i)

    # The 5-second throttle should suppress writes after the first.
    assert write_count["n"] < 20, (
        f"100 rapid mutations should produce fewer than 20 disk writes "
        f"(the 5-second throttle suppresses writes within the window); "
        f"got {write_count['n']} writes. The throttle is broken."
    )
    # Sanity check — at least 1 write happened (the very first
    # mutation, before the throttle window opens).
    assert write_count["n"] >= 1, (
        "At least one write should have happened (the very first "
        "mutation, before the throttle window opens — _last_persist "
        "was 0.0 so the first persist's throttle check passes). "
        "Zero writes means the persist path is broken."
    )


def test_force_persist_bypasses_throttle(tmp_state_dir, monkeypatch):
    """``force=True`` bypasses the 5-second throttle so explicit
    flushes (test isolation, the ``reset_upi_counters`` clear, process
    shutdown hooks) write immediately regardless of the throttle
    window.

    The 15-b implementation uses ``force=True`` in
    ``reset_upi_counters`` so the post-clear empty state is reflected
    on disk immediately (the 3 individual ``.clear()`` calls each
    trigger a throttled persist; the throttle would skip the 2nd +
    3rd writes, leaving the file with a stale partial-cleared state).
    """
    fs = _FileState("test_force.json")
    # Set _last_persist to "just now" so the throttle would normally
    # suppress the next persist (force=False would skip).
    fs._last_persist = time.time()
    sub = fs.sub("cumulative_monthly")

    write_count = {"n": 0}
    original_replace = os.replace

    def counting_replace(src, dst):
        write_count["n"] += 1
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", counting_replace)

    # 50 force-flushes — all should write (bypass throttle). Each
    # force-flush is a separate _persist_to_disk(force=True) call.
    for i in range(50):
        sub[f"key_{i:03d}"] = float(i)
        fs._persist_to_disk(force=True)

    # All 50 force-flushes should write (bypass the throttle).
    assert write_count["n"] == 50, (
        f"force=True bypasses the throttle — all 50 force-flushes "
        f"should write; got {write_count['n']} (expected 50). The "
        f"force-bypass is broken."
    )

    # Counter-test: the same 50 mutations WITHOUT force would all be
    # throttled (since _last_persist was set to "just now" at the top
    # + the force-flushes above kept updating it). Verify the throttle
    # WOULD have skipped them.
    pre_count = write_count["n"]
    for i in range(50, 100):
        sub[f"key_{i:03d}"] = float(i)
        # NO force — the throttle should kick in.
        fs._persist_to_disk()  # force=False (default)
    # All 50 throttled persists should have been skipped.
    assert write_count["n"] == pre_count, (
        f"The 50 throttled (force=False) persists after a "
        f"force-flush should ALL be skipped (within the 5-sec window); "
        f"got {write_count['n'] - pre_count} unexpected writes. The "
        f"throttle is broken."
    )


# ============================================================================
# LRU cap on the override nonce cache — bounded to 10_000 entries
# (the TTLCache maxsize; older entries are evicted on insertion beyond
# the cap).
# ============================================================================


def test_override_nonce_cache_lru_cap_at_10000(tmp_state_dir):
    """Insert 10_001 nonces into the ``_override_nonce_cache`` — the
    cache must stay at 10_000 entries (LRU eviction per the TTLCache
    ``maxsize=10_000`` configuration).

    The 14-d implementation already configured ``TTLCache(maxsize=10_000,
    ttl=86400)`` for the override nonce cache (bounded LRU + 1-day TTL).
    This test is a regression guard: any future change that bumps the
    maxsize to unbounded (or removes the TTLCache for a plain dict)
    will fail this test, surfacing the unbounded-growth bug.

    The mandate of DO BADLY #3 explicitly calls out "bounded LRU cap
    (max 10_000) on any unbounded dict in your territory" — the
    override nonce cache IS in my territory + the cap was already in
    place via 14-d's TTLCache. This test makes the cap ASSERTED, not
    just claimed.
    """
    # Attribute access at call time — gets the CURRENT binding.
    import src.api.routes as routes_mod

    # Clear first (the autouse fixture in test_override_replay.py
    # would do this if we were running there, but this test runs in
    # its own file — be explicit).
    _clear_override_nonce_cache()
    try:
        cache = routes_mod._override_nonce_cache
        # Defensive: the cache's maxsize should be configured to 10_000
        # (asserting the configuration, not just the behaviour).
        assert cache.maxsize == 10_000, (
            "The override nonce cache must be configured with "
            "maxsize=10_000 (bounded LRU cap per the DO BADLY #3 "
            "mandate). Got a different maxsize — the configuration "
            "was changed."
        )

        # Insert 10_001 fresh nonce hashes directly into the cache.
        # The TTLCache evicts the oldest (least-recently-used) entry
        # when the 10_001st entry is inserted, keeping the size at
        # 10_000.
        for i in range(10_001):
            cache[f"nonce-hash-{i:05d}"] = True

        # The cache must be at exactly 10_000 entries (the maxsize cap
        # evicted the oldest entry).
        assert len(cache) == 10_000, (
            f"The override nonce cache is bounded to 10_000 entries "
            f"(TTLCache maxsize=10_000); after inserting 10_001 "
            f"entries, the size should be 10_000 (LRU eviction); got "
            f"{len(cache)}. The cap is broken — the cache would grow "
            f"unbounded under burst traffic, eventually OOMing the "
            f"process."
        )

        # The first inserted entry (``nonce-hash-00000``) should have
        # been evicted (LRU — it's the least-recently-used after 10_000
        # subsequent inserts).
        assert "nonce-hash-00000" not in cache, (
            "The oldest nonce hash should have been evicted (LRU) "
            "when the 10_001st entry was inserted."
        )
        # The last inserted entry (``nonce-hash-10000``) should still
        # be present.
        assert "nonce-hash-10000" in cache, (
            "The most-recently-inserted nonce hash should still be "
            "present (LRU eviction only evicts the oldest entries)."
        )
    finally:
        _clear_override_nonce_cache()


# ============================================================================
# Module-import smoke test — the imports the test file declares must
# all resolve (otherwise the test file collects but skips silently).
# ============================================================================


def test_cross_process_state_module_imports_clean():
    """Smoke test — the 15-b additions (``_FileState``,
    ``_persist_nonce``, ``_load_nonces_from_disk``) all import clean
    from their respective modules. This catches import-time errors
    (typos, missing helpers, circular imports) that would otherwise
    only surface when an actual test exercises the path."""
    import src.api.mandates as mandates_mod
    import src.api.routes as routes_mod

    # Class + module-level singletons (looked up via attribute access
    # at call time — robust against any future importlib.reload). Note:
    # after a reload, ``mandates_mod._FileState`` may be a NEW class
    # object (re-executed by the reload), so we check the NAME matches
    # rather than identity (``is``) — what matters is that the symbol
    # resolves to a class with the right name + the documented helpers.
    assert mandates_mod._FileState.__name__ == "_FileState"
    assert hasattr(mandates_mod._FileState, "_persist_to_disk")
    assert hasattr(mandates_mod._FileState, "_load_from_disk")
    assert hasattr(mandates_mod._FileState, "sub")
    assert mandates_mod._mandate_state is not None
    assert mandates_mod._cumulative_monthly is not None
    assert mandates_mod._cumulative_24h is not None
    assert mandates_mod._last_activity is not None
    # Routes-side helpers.
    assert callable(routes_mod._persist_nonce)
    assert callable(routes_mod._load_nonces_from_disk)
    assert routes_mod._override_nonce_cache is not None
    assert routes_mod._override_nonce_cache_lock is not None
    assert isinstance(routes_mod._override_nonces_state_file, str)
    assert routes_mod._override_nonces_state_file == "override_nonces_state.json"
