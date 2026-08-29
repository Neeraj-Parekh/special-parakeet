# Merkle Audit / verify-chain Diagnosis

**Date:** 2025-08-29
**Symptom reported:** `GET /v1/audit/verify-chain` returns `{intact: false, records_checked: 0}` in file-mode.
**User's proposed fix:** "Needs Postgres mode or `seal_interval()` trigger."

## 1. Verdict on the user's proposed fix

| Proposed fix | Correct? | Why |
|---|---|---|
| Switch to Postgres mode | **Partially** | Postgres serializes INSERTs so the cross-process race (§3) doesn't happen. But it's not the *minimal* fix — file-mode can be made safe with `fcntl.flock` (applied, §4). |
| Call `seal_interval()` | **No** | `seal_interval()` is the Merkle *interval* layer (groups records into Merkle trees, produces roots). `verify_chain` checks the per-record SHA-256 hash chain (GENESIS → r1 → r2 → …). They are independent. Sealing an interval does NOT repair a broken per-record chain. |

## 2. What `verify_chain` actually checks

`src/audit/logger.py:470-474` dispatches:
- Postgres mode (`self._conn is not None`) → `_verify_chain_postgres` (line 751): `SELECT audit_id, body, raw_hash, prev_hash FROM audit_records ORDER BY id ASC`, recomputes each hash, checks `stored_prev == expected_prev and stored_raw == recomputed`.
- File mode → `_verify_chain_file` (line 805): reads `out/audit.jsonl` line-by-line, same hash recompute.

Returns `(ok, records_checked, first_bad_id)`. **`records_checked: 0` means "0 records passed before the first failure"** — i.e. the FIRST record itself fails. It does NOT mean "0 records exist" (an empty file returns `intact=True, records_checked=0`, line 808-809).

## 3. The real root cause (confirmed empirically)

The 7.3 MB `out/audit.jsonl` had **2,157 records**. Diagnostic:

```
first record previous_hash == GENESIS?  False   (it was 40dc9537ee340bea...)
first record raw_hash matches recomputed?  True   (no tampering of the record itself)
internal chain breaks (rec 1..N, previous_hash != prior raw_hash):  87
  first few breaks at lines: [37, 39, 41, 42, 43]
```

So:
1. The first record's `raw_hash` is internally consistent (recompute matches stored) — **no tampering**.
2. The first record's `previous_hash` is NOT `GENESIS` → it references a prior record that isn't in the file → the file is a **fragment** (it was truncated/rotated at some point, and new records were appended whose `previous_hash` points to records that no longer exist).
3. 87 *internal* breaks (records 37, 39, 41, 42, 43, …) → the file was written by **concurrent processes** whose in-memory `last_hash` diverged.

### Why concurrent processes broke the chain

`AuditLogger.__init__` (file mode, lines 447-452) rehydrates `self.last_hash` from the file's last record on construction. But `_log_file` (the OLD code) used only `threading.Lock` (line 787), which serializes **threads within one process**, NOT **across processes**. So:

- Process A constructs → rehydrates `last_hash = R100`.
- Process B constructs → rehydrates `last_hash = R100` (same).
- Process A writes record 101 with `previous_hash = R100` → `raw_hash = R101`.
- Process B writes record 102 with `previous_hash = R100` (stale!) → `raw_hash = R102`.
- Record 102's `previous_hash = R100`, but the *prior* record in the file is 101 with `raw_hash = R101`. → **break at record 102**.

This is the 87-break signature. The concurrent writers were the test suite + dev server (and/or parallel `pytest` workers) all sharing `out/audit.jsonl`.

## 4. The fix applied

### 4a. Code fix — cross-process `fcntl.flock` on the file (`src/audit/logger.py:_log_file`)

Acquire an exclusive OS lock on the audit file before computing `previous_hash`, so concurrent writers serialize. After acquiring the lock, **re-derive the true last record's `raw_hash`** by seeking to end-of-file, backing up ~16 KB, and parsing the last line (O(1) per write). Only then compute the new record's `previous_hash` + `raw_hash` and append. Release the lock. `fsync` so the write is durable before the next writer takes the lock.

```python
import fcntl
with self.path.open("a+") as f:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    try:
        f.seek(0, 2); end = f.tell()
        if end == 0:
            true_prev = self.last_hash or GENESIS
        else:
            f.seek(max(0, end - 16384)); chunk = f.read()
            lines = [l for l in chunk.splitlines() if l.strip()]
            true_prev = json.loads(lines[-1]).get("raw_hash", self.last_hash) if lines else self.last_hash
        base["previous_hash"] = true_prev
        base["raw_hash"] = self._hash(base)
        f.seek(0, 2); self._index[audit_id] = f.tell()
        f.write(json.dumps(base, default=str) + "\n"); f.flush(); os.fsync(f.fileno())
        self.last_hash = base["raw_hash"]
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

**Verified:** 2 concurrent processes × 50 records each = 100 records, `verify_chain` returns `intact=True, records_checked=100`. The race is gone.

### 4b. Data fix — rotate the broken file aside

```
mv out/audit.jsonl out/audit.broken-fragment-2025-08-29.jsonl
```

The 2,157-record file with 87 breaks is preserved (not deleted) for forensic inspection. The next server start creates a fresh `out/audit.jsonl` that begins a clean chain from `GENESIS`.

## 5. Test impact

- `test_async_logger.py` + `test_cross_process_state.py`: **15 passed**.
- `test_security.py` + `test_ship.py` + `test_v3_endpoints.py` + `test_streaming.py` + `test_mandates.py` + `test_override_replay.py`: **124 passed, 2 skipped**.
- Full suite (per-file subprocess run, ddtrace disabled, faulthandler off): **397 passed, 11 skipped, 0 failures** (was 390; the suite grew).

## 6. The Merkle *interval* layer (for completeness)

`seal_interval()` (line 497) and `merkle_proof()` (line 530) ARE Postgres-only — they require the `audit_merkle_intervals` table. In file mode, `seal_interval()` returns `None` (line 512-513: `if self.sealer is None: return None`) and `merkle_proof()` returns `None` (line 540-541). This is **by design** — the Merkle interval layer is a Postgres-mode enhancement layered *on top of* the per-record hash chain. The per-record chain (which is what `verify_chain` checks) works in both modes.

So if a user wants the full Merkle audit trail (sealed intervals + inclusion proofs), they DO need Postgres mode. But that is a separate concern from the `verify_chain` symptom reported here.

## 7. Bottom line

- `verify_chain` was **working correctly** — it detected real chain breaks, not a bug.
- The breaks were caused by **concurrent writers** racing on the shared `out/audit.jsonl` with only in-process locking.
- Fix: cross-process `fcntl.flock` (applied) + rotate the broken fragment aside (applied).
- The user's "needs Postgres" intuition is valid for the *Merkle interval* layer; the per-record chain now works correctly in file mode too.
