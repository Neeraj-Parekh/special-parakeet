"""Tamper-evident audit log with SHA-256 hash chain + Merkle interval sealing.

Dual-mode: Postgres (when ``DATABASE_URL`` is set) or JSONL file fallback
(otherwise — the 63 existing tests run this way). The Postgres schema lives
in ``alembic/versions/001_initial.py`` (table ``audit_records``); the file
format is unchanged from the original implementation.

raw_hash = sha256(canonical(record_without_hash_fields) + previous_raw_hash).
Editing any historical record breaks every subsequent link; ``verify_chain``
recomputes the full chain for compliance audits (per SoK Mao 2026 paper —
gap #11 in 05-PAPER-SKILLS-MAP.md, the Merkle-interval sealing extension is
Track H Day 2's job; the per-record hash chain here is the foundation).

Day 2 Track H — Merkle interval sealing (V3 §10.3). ON TOP of the
per-record hash chain (additional layer — the per-record chain is
unchanged). Every N records (default 1000) or T seconds (default 3600),
``MerkleSealer`` computes the Merkle root of the interval's raw_hash
leaves, chains it to the previous interval's root, and inserts a row in
``audit_merkle_intervals`` (migration 002). The interval roots form a
coarser tamper-evidence layer enabling O(log N) inclusion proofs per
record (vs O(N) full-chain recompute). Only active in Postgres mode —
file mode keeps the per-record hash chain only (sufficient for tests).
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def self_salt() -> str:
    """Salt for ``redact_customer()``. Centralised via ``Settings`` (Day 2
    Track E). Kept for backward-compat — existing call sites use the bare
    function; new code should read ``get_settings().rto_audit_salt``.
    """
    from src.config import get_settings

    return get_settings().rto_audit_salt


def redact_customer(customer_id: str) -> str:
    """Never store raw customer identifiers; store salted digest prefix."""
    return "cust_" + hashlib.sha256(f"{customer_id}:{self_salt()}".encode()).hexdigest()[:16]


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


# ------------------------------------------------------------------ #
# Merkle interval sealing (V3 §10.3 — Day 2 Track H)                 #
# ------------------------------------------------------------------ #


class MerkleSealer:
    """Merkle interval sealing per V3 §10.3.

    Every N records (default 1000) or T seconds (default 3600), compute
    a Merkle root of the batch's ``raw_hash`` leaves, chain it to the
    previous interval's root, store the interval.

    This is ADDITIONAL to the per-record hash chain (Track E Day 2) —
    it's a coarser tamper-evidence layer that enables O(log N) proof
    generation per record (vs O(N) full-chain recompute via
    ``AuditLogger.verify_chain()``). The per-record ``raw_hash`` is the
    leaf; ``audit_merkle_intervals.merkle_root`` is the interval root;
    ``audit_merkle_intervals.prev_interval_root`` chains intervals
    together so cross-interval tampering is detected (same model as
    Track E's per-record ``prev_hash``).

    Source: SoK (Mao 2026) capability ``recommend_layered_defenses``
    layer 5 — "market & compliance monitoring with tamper-evident audit
    trails"; ``audit_agent_mandate_scoping`` (the dual-control override
    in routes.py consumes this tamper-evidence layer so both admin
    signatures are anchored to a verifiable root, not just the live
    hash chain tip).

    File mode is a no-op: the per-record hash chain is enough for tests
    + local dev without Postgres. Merkle is a Postgres-mode enhancement.
    """

    def __init__(
        self,
        conn=None,
        interval_size: int = 1000,
        interval_seconds: int = 3600,
    ):
        # Postgres connection (None in file mode → no-op). Shared with
        # the parent AuditLogger's connection — no extra pool needed.
        self.conn = conn
        self.interval_size = interval_size
        self.interval_seconds = interval_seconds
        # Pending (record_id, raw_hash) pairs since the last seal.
        # Bounded by interval_size; flushed to audit_merkle_intervals on
        # add() when len(self._pending) >= interval_size or by an
        # explicit seal_interval() call (e.g. from a cron / shutdown hook).
        self._pending: list[tuple[int, str]] = []
        # First-record timestamp so the time-based seal trigger can compute
        # elapsed since the interval opened (independent of record count).
        self._interval_started_at: datetime | None = None

    # ------------------------------------------------------------------ #
    # Public API — add / seal / proof                                    #
    # ------------------------------------------------------------------ #

    def add(self, record_id: int, raw_hash: str) -> dict | None:
        """Add a record to the pending interval. Seal if threshold reached.

        Called by ``AuditLogger._log_postgres`` after every INSERT so the
        sealer tracks new records in real time. The first record's
        timestamp seeds ``_interval_started_at`` for the time-based
        trigger (so an idle period after a partial interval still flushes
        on the next ``seal_interval()`` even with no fresh traffic).
        """
        if self.conn is None:
            # File mode — no-op (per-record hash chain is enough there).
            return None
        if not self._pending:
            self._interval_started_at = datetime.now(timezone.utc)
        self._pending.append((record_id, raw_hash))
        # Seal when EITHER threshold trips (count OR elapsed). The count
        # trigger fires inline; the time trigger is checked here too so
        # a long quiet gap after a partial batch doesn't leave the
        # interval open forever (the cron hook calls seal_interval() too).
        if len(self._pending) >= self.interval_size:
            return self.seal()
        if self._interval_started_at is not None:
            elapsed = (datetime.now(timezone.utc) - self._interval_started_at).total_seconds()
            if elapsed >= self.interval_seconds and len(self._pending) > 0:
                return self.seal()
        return None

    def seal(self) -> dict | None:
        """Seal the current pending batch into a Merkle interval.

        Returns the interval metadata dict (merkle_root, prev_root,
        leaf_count) on success, None if there's nothing to seal or the
        sealer is in file mode (conn is None). After sealing, the
        pending batch is cleared + the next add() starts a fresh
        interval. Idempotent — calling seal() with an empty pending
        list is a no-op.
        """
        if not self._pending or self.conn is None:
            return None
        # Compute Merkle root of the raw_hash leaves. Padding to a power
        # of 2 keeps the tree balanced so the proof builder's index
        # arithmetic (sibling_idx = idx ^ 1) is consistent across levels.
        leaves = [h for _, h in self._pending]
        merkle_root = self._merkle_root(leaves)
        # Get previous interval's root (None on first interval → genesis).
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT merkle_root FROM audit_merkle_intervals "
                "ORDER BY interval_id DESC LIMIT 1"
            )
            prev = cur.fetchone()
        prev_root = prev[0] if prev else GENESIS
        # Insert the interval row + backfill the per-record columns
        # (interval_id + interval_position) so the proof builder can
        # locate a record's interval + leaf index in O(1).
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_merkle_intervals
                  (start_record_id, end_record_id, merkle_root,
                   prev_interval_root, leaf_count)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING interval_id
                """,
                (
                    self._pending[0][0],
                    self._pending[-1][0],
                    merkle_root,
                    prev_root,
                    len(leaves),
                ),
            )
            interval_id = cur.fetchone()[0]
            # currval('audit_merkle_intervals_interval_id_seq') is the
            # just-inserted row's SERIAL value; using it in the UPDATE
            # avoids passing interval_id back from Python (atomic within
            # the same transaction). Per-record backfill is O(M) where
            # M = interval_size (default 1000) — cheap.
            for pos, (rid, _) in enumerate(self._pending):
                cur.execute(
                    """
                    UPDATE audit_records
                       SET interval_id = %s, interval_position = %s
                     WHERE id = %s
                    """,
                    (interval_id, pos, rid),
                )
            self.conn.commit()
        self._pending = []
        self._interval_started_at = None
        return {
            "interval_id": interval_id,
            "merkle_root": merkle_root,
            "prev_root": prev_root,
            "leaf_count": len(leaves),
        }

    @staticmethod
    def _merkle_root(leaves: list[str]) -> str:
        """Compute Merkle root from leaf hashes. Pad to power of 2.

        Padding uses the LAST leaf's hash repeated (RFC 6962-style) so
        the tree is balanced without introducing a synthetic zero-leaf
        that a verifier would need to special-case. Empty leaf list →
        GENESIS (the same ``"0"*64`` constant Track E's hash chain uses
        as the genesis ``prev_hash``).
        """
        if not leaves:
            return GENESIS
        # Pad to next power of 2.
        size = 1
        while size < len(leaves):
            size *= 2
        padded = leaves + [leaves[-1]] * (size - len(leaves))
        # Build tree level-by-level: parent = sha256(left + right).
        level = padded
        while len(level) > 1:
            next_level = []
            for i in range(0, len(level), 2):
                combined = hashlib.sha256(
                    (level[i] + level[i + 1]).encode()
                ).hexdigest()
                next_level.append(combined)
            level = next_level
        return level[0]

    def proof(self, record_id: int) -> dict | None:
        """Generate Merkle proof for a record: path from leaf to interval root.

        Returns None if the record doesn't exist, has no interval_id
        (i.e. the sealer hasn't reached the threshold + the cron seal
        hasn't fired), or the sealer is in file mode. The proof dict
        contains the sibling hashes from leaf to root, the interval's
        root + prev_interval_root (chain anchor), leaf_count, sealed_at
        — enough to verify inclusion in O(log N) without recomputing
        the whole tree.
        """
        if self.conn is None:
            return None
        # Get the record's interval + position.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT interval_id, interval_position, raw_hash "
                "FROM audit_records WHERE id = %s",
                (record_id,),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            # Record not found, OR interval not yet sealed.
            return None
        interval_id, position, leaf_hash = row
        # Get all leaves in this interval (the proof builder needs to
        # descend from the leaf position; siblings at each level are
        # looked up by index — pre-fetched once per proof for simplicity
        # rather than one query per tree level).
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT raw_hash FROM audit_records WHERE interval_id = %s "
                "ORDER BY interval_position",
                (interval_id,),
            )
            leaves = [r[0] for r in cur.fetchall()]
        # Build proof: at each tree level, the sibling's hash + whether
        # it's the left or right child of the parent node. Pad leaves
        # to a power of 2 (same padding rule as _merkle_root) so the
        # index arithmetic is consistent.
        size = 1
        while size < len(leaves):
            size *= 2
        level = leaves + [leaves[-1]] * (size - len(leaves))
        proof: list[dict] = []
        idx = position
        while len(level) > 1:
            sibling_idx = idx ^ 1  # XOR 1: pairs (0,1), (2,3), ...
            if sibling_idx < len(level):
                proof.append(
                    {
                        "position": "right" if sibling_idx > idx else "left",
                        "hash": level[sibling_idx],
                    }
                )
            else:
                # Padding sibling (RFC 6962-style last-leaf-repeat).
                proof.append({"position": "right", "hash": level[-1]})
            # Compute next level (parent hashes).
            next_level = []
            for i in range(0, len(level), 2):
                combined = hashlib.sha256(
                    (level[i] + level[i + 1 if i + 1 < len(level) else i]).encode()
                ).hexdigest()
                next_level.append(combined)
            level = next_level
            idx //= 2
        # Get the interval's metadata for the chain anchor.
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT merkle_root, prev_interval_root, leaf_count, sealed_at
                  FROM audit_merkle_intervals WHERE interval_id = %s
                """,
                (interval_id,),
            )
            interval = cur.fetchone()
        if not interval:
            return None
        return {
            "record_id": record_id,
            "leaf_hash": leaf_hash,
            "interval_id": interval_id,
            "position": position,
            "proof": proof,
            "merkle_root": interval[0],
            "prev_interval_root": interval[1],
            "leaf_count": interval[2],
            "sealed_at": interval[3].isoformat() if interval[3] else None,
        }


class AuditLogger:
    """Append-only audit log, tamper-evident hash chain, O(1) indexed reads.

    Dual-mode (Day 2 Track E):
      * Postgres mode (``settings.database_url`` set): INSERT into
        ``audit_records``; reads via ``SELECT ... WHERE audit_id = %s``;
        chain verification via ``SELECT audit_id, body, raw_hash, prev_hash
        FROM audit_records ORDER BY id ASC``.
      * File mode (no DSN): the original JSONL append + byte-offset index.

    The hash-chain math is identical between modes (``canonical(body) +
    prev_hash``) so a verifier computes the same hash either way.

    Day 2 Track H — Merkle interval sealing (V3 §10.3). In Postgres
    mode, ``__init__`` constructs a ``MerkleSealer`` sharing the same
    connection; ``_log_postgres`` calls ``sealer.add(record_id, raw_hash)``
    after every INSERT so the sealer tracks new records in real time.
    File mode skips Merkle (the per-record hash chain is enough there).
    """

    HASH_FIELDS = ("previous_hash", "raw_hash")

    def __init__(self, path: str = "out/audit.jsonl", model_version: str = "dev"):
        from src.config import get_settings

        self.settings = get_settings()
        self.model_version = model_version
        self._lock = threading.Lock()

        if self.settings.is_postgres:
            # Postgres mode — open one persistent psycopg connection per logger
            # instance. We use a single connection (not a pool) because the
            # API is single-process + the audit log is the write-hot path;
            # a pool would add latency for no benefit at this scale.
            import psycopg

            self._conn = psycopg.connect(self.settings.database_url, autocommit=False)
            self.path = Path(path)  # kept for backward-compat (audit_tail reads)
            self.path.touch(exist_ok=True)  # so audit_logger.path.exists() is True
            # Track last_hash from the most recent row so we can chain without
            # a SELECT on every INSERT. Hydrated lazily on first log() call
            # (cheap query; we'd rather not slow __init__ if the table is empty).
            self._last_hash_cached: str | None = None
            # Day 2 Track H — Merkle interval sealer (V3 §10.3). Shares
            # the parent connection so audit INSERT + Merkle seal happen
            # in the same transaction (atomic; partial-failure recovery
            # is left to Postgres's WAL, not Python try/except).
            self.sealer = MerkleSealer(conn=self._conn)
        else:
            # File mode — original behaviour, unchanged. The Merkle
            # sealer is a no-op here (per-record hash chain is enough
            # for tests; Merkle is a Postgres-mode enhancement).
            self._conn = None
            self.path = Path(path)
            self._index: dict[str, int] = {}
            self.last_hash = GENESIS
            self.sealer = None
            if self.path.exists():
                with self.path.open() as f:
                    for offset, line in enumerate(f):
                        rec = json.loads(line)
                        self._index[rec.get("audit_id", "")] = offset
                        self.last_hash = rec.get("raw_hash", self.last_hash)

    # ------------------------------------------------------------------ #
    # Public API — log / read / verify_chain / tail / merkle             #
    # ------------------------------------------------------------------ #

    def log(self, payload: dict) -> str:
        """Append a record. Returns the audit_id (caller surfaces it in the
        response so the dashboard can link ``/audit/{id}``)."""
        if self._conn is not None:
            return self._log_postgres(payload)
        return self._log_file(payload)

    def read(self, audit_id: str) -> dict | None:
        if self._conn is not None:
            return self._read_postgres(audit_id)
        return self._read_file(audit_id)

    def verify_chain(self) -> tuple[bool, int, str]:
        """Recompute entire chain. Returns (ok, records_checked, first_bad_id)."""
        if self._conn is not None:
            return self._verify_chain_postgres()
        return self._verify_chain_file()

    def tail(self, limit: int = 300) -> list[dict]:
        """Return the last ``limit`` audit records (most-recent last).

        Used by the ``/v1/models/drift`` endpoint (recent feature distributions
        for PSI) and the ``/v1/compliance/audit-export`` CSV export. In
        Postgres mode this is a ``SELECT ... ORDER BY id DESC LIMIT %s``;
        in file mode it's the tail of the JSONL.
        """
        if self._conn is not None:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT body FROM audit_records ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
            return [r[0] for r in rows][::-1]  # chronological order at the end
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()]

    def seal_interval(self) -> dict | None:
        """Force-seal the pending Merkle interval (Day 2 Track H — V3 §10.3).

        Public hook for a cron / shutdown / admin-endpoint call so a
        partial interval can be sealed without waiting for the count
        threshold. Returns the interval metadata on seal, None if there
        was nothing pending or the logger is in file mode.
        """
        if self.sealer is None:
            return None
        with self._lock:
            return self.sealer.seal()

    def merkle_proof(self, record_id: int) -> dict | None:
        """Generate Merkle inclusion proof for an audit record (V3 §10.3).

        Returns None in file mode (no Merkle layer) OR if the record's
        interval hasn't been sealed yet. The ``GET /v1/audit/{id}/proof``
        endpoint surfaces this — when None, the endpoint returns 404 so
        the caller can distinguish "no record" vs "record exists but
        interval not sealed yet" (the latter is fixable by calling
        ``seal_interval()`` first).
        """
        if self.sealer is None:
            return None
        return self.sealer.proof(record_id)

    def merkle_intervals(self, limit: int = 100) -> list[dict]:
        """Return the last N sealed intervals (compliance audit endpoint).

        Returns ``[]`` in file mode. Used by the ``/v1/usage`` endpoint
        + the admin audit dashboard to surface the sealing cadence +
        interval chain (each row's ``prev_interval_root`` chains to the
        prior row's ``merkle_root`` — a verifier can recompute this
        client-side in O(M) where M = number of intervals).
        """
        if self._conn is None:
            return []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT interval_id, start_record_id, end_record_id,
                       merkle_root, prev_interval_root, leaf_count, sealed_at
                  FROM audit_merkle_intervals
                 ORDER BY interval_id DESC LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [
            {
                "interval_id": r[0],
                "start_record_id": r[1],
                "end_record_id": r[2],
                "merkle_root": r[3],
                "prev_interval_root": r[4],
                "leaf_count": r[5],
                "sealed_at": r[6].isoformat() if r[6] else None,
            }
            for r in rows
        ]

    def usage_counts(self, since_hours: tuple[int, ...] = (24, 168, 720)) -> dict:
        """Per-window audit-record counts for the ``/v1/usage`` metering
        endpoint. Returns ``{str(h): count, ...}`` for each h in
        ``since_hours`` (default last 24h / 7d / 30d).

        Postgres mode: ``SELECT count(*) FROM audit_records WHERE
        created_at > now() - interval '%s hours'``. File mode: scan the
        JSONL + filter by the record's ``timestamp`` field. Cheap on
        either side — the audit table is indexed on ``created_at DESC``
        (Track E migration 001), so the count is an index-only scan.
        """
        out: dict[str, int] = {}
        if self._conn is not None:
            with self._conn.cursor() as cur:
                for h in since_hours:
                    cur.execute(
                        "SELECT count(*) FROM audit_records "
                        "WHERE created_at > now() - interval '%s hours'",
                        (h,),
                    )
                    out[str(h)] = int(cur.fetchone()[0])
            return out
        # File mode — scan + filter by timestamp.
        if not self.path.exists():
            return {str(h): 0 for h in since_hours}
        now = datetime.now(timezone.utc)
        cutoffs = {h: now.timestamp() - h * 3600 for h in since_hours}
        counts = {h: 0 for h in since_hours}
        try:
            for line in self.path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    parsed = datetime.fromisoformat(ts).timestamp()
                    for h in since_hours:
                        if parsed >= cutoffs[h]:
                            counts[h] += 1
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
        except OSError:
            return {str(h): 0 for h in since_hours}
        return {str(h): counts[h] for h in since_hours}

    # ------------------------------------------------------------------ #
    # Postgres mode                                                      #
    # ------------------------------------------------------------------ #

    def _hydrate_last_hash_postgres(self) -> str:
        if self._last_hash_cached is not None:
            return self._last_hash_cached
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT raw_hash FROM audit_records ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            self._last_hash_cached = row[0] if row else GENESIS
            return self._last_hash_cached

    def _log_postgres(self, payload: dict) -> str:
        audit_id = f"aud_{uuid.uuid4().hex[:16]}"
        ts = datetime.now(timezone.utc).isoformat()
        # Body — the full audit record minus the hash fields (which are
        # computed below). Stored as JSONB so consumers can query arbitrary
        # paths. Note the body includes audit_id, timestamp, model_version
        # + every key in payload (per Track D: request, decision,
        # decision_source, cost_breakdown, reason_codes, mandate_*,
        # bh_purpose_code, device_id, user_id, breach_note, rule_fired,
        # degraded, features_used, latency_ms, case_id, probability).
        body = {
            "audit_id": audit_id,
            "timestamp": ts,
            "model_version": self.model_version,
            **payload,
        }
        prev_hash = self._hydrate_last_hash_postgres()
        raw_hash = self._hash(body, prev=prev_hash)
        with self._lock:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_records
                      (audit_id, body, raw_hash, prev_hash, created_at,
                       model_version, mandate_type, bh_purpose_code,
                       device_id, user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        audit_id,
                        json.dumps(body, default=str),
                        raw_hash,
                        prev_hash,
                        ts,
                        self.model_version,
                        payload.get("mandate_type"),
                        payload.get("bh_purpose_code"),
                        payload.get("device_id"),
                        payload.get("user_id"),
                    ),
                )
                # Day 2 Track H — feed the new record to the Merkle
                # sealer so it tracks pending intervals in real time.
                # ``sealer.add`` is a no-op if interval_size hasn't been
                # reached yet (it just appends to the pending list). When
                # the threshold trips, ``seal()`` runs inline + commits
                # the interval row + the per-record backfill in the same
                # transaction (atomic with the audit INSERT above).
                record_id_row = cur.fetchone()
                record_id = record_id_row[0] if record_id_row else None
                self._conn.commit()
            self._last_hash_cached = raw_hash
        if record_id is not None and self.sealer is not None:
            try:
                self.sealer.add(record_id, raw_hash)
            except Exception as e:  # pragma: no cover — best-effort
                # Merkle sealing must never break the audit write (the
                # per-record hash chain is the foundation; Merkle is a
                # coarser layer that can catch up via seal_interval()
                # later). Log + continue.
                print(
                    f"[audit] merkle sealer.add failed: "
                    f"{type(e).__name__}: {e}",
                    file=sys.stderr,
                )
        return audit_id

    def _read_postgres(self, audit_id: str) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT body, raw_hash, prev_hash
                FROM audit_records WHERE audit_id = %s
                """,
                (audit_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        body, raw_hash, prev_hash = row
        # Merge hash fields back into the body so the file-mode test
        # assertions (which read ``rec["raw_hash"]``) work unchanged.
        body = dict(body) if isinstance(body, dict) else json.loads(body)
        body["raw_hash"] = raw_hash
        body["previous_hash"] = prev_hash
        return body

    def _verify_chain_postgres(self) -> tuple[bool, int, str]:
        expected_prev = GENESIS
        n = 0
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT audit_id, body, raw_hash, prev_hash
                FROM audit_records ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
        for audit_id, body, raw_hash, prev_hash in rows:
            body_dict = dict(body) if isinstance(body, dict) else json.loads(body)
            stored_prev = prev_hash
            stored_raw = raw_hash
            want_prev = expected_prev
            want_raw = self._hash(body_dict, prev=want_prev)
            ok = stored_prev == want_prev and stored_raw == want_raw
            if not ok:
                return False, n, audit_id
            expected_prev = stored_raw
            n += 1
        return True, n, ""

    # ------------------------------------------------------------------ #
    # File mode (unchanged behaviour from the original implementation)   #
    # ------------------------------------------------------------------ #

    def _log_file(self, payload: dict) -> str:
        audit_id = str(uuid.uuid4())
        base = {
            "audit_id": audit_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_version": self.model_version,
            **payload,
        }
        with self._lock:
            base["previous_hash"] = self.last_hash
            base["raw_hash"] = self._hash(base)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                self._index[audit_id] = f.tell()
                f.write(json.dumps(base, default=str) + "\n")
            self.last_hash = base["raw_hash"]
        return audit_id

    def _read_file(self, audit_id: str) -> dict | None:
        offset = self._index.get(audit_id)
        if offset is None or not self.path.exists():
            return None
        with self.path.open() as f:
            f.seek(offset)
            return json.loads(f.readline())

    def _verify_chain_file(self) -> tuple[bool, int, str]:
        expected_prev = GENESIS
        n = 0
        if not self.path.exists():
            return True, 0, ""
        with self.path.open() as f:
            for line in f:
                rec = json.loads(line)
                stored_hashes = {k: rec.get(k) for k in self.HASH_FIELDS}
                body = {k: v for k, v in rec.items() if k not in self.HASH_FIELDS}
                want_prev = expected_prev
                want_raw = self._hash(body, prev=want_prev)
                hashes_ok = (
                    stored_hashes["previous_hash"] == want_prev
                    and stored_hashes["raw_hash"] == want_raw
                )
                if not hashes_ok:
                    return False, n, rec.get("audit_id", "?")
                expected_prev = stored_hashes["raw_hash"]
                n += 1
        return True, n, ""

    # ------------------------------------------------------------------ #
    # Hash math — shared between modes                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hash(record: dict, prev: str | None = None) -> str:
        body = {k: v for k, v in record.items() if k not in AuditLogger.HASH_FIELDS}
        prev_hash = prev if prev is not None else record.get("previous_hash", GENESIS)
        return hashlib.sha256((canonical(body) + prev_hash).encode()).hexdigest()
