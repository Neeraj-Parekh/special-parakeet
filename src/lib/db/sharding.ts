// src/lib/db/sharding.ts — sharding key router.
//
// G6 — Horizontal sharding by customer_id.
//
// Phase I (current): single SQLite database, no sharding actually
// applied. This router is the seam: every query that needs to land on
// the right shard calls `ShardRouter.route(customer_id)` first.
//
// Phase II (production): swap the single-shard map for a Postgres +
// pg_partman cluster, one logical database per merchant_tier, with
// partitions by `customer_id` hash. The router's interface stays the
// same — the change is contained to the `shardMap` configuration.
//
// Why FNV-1a 32-bit:
//   - Fast, deterministic, well-distributed for short ASCII keys like
//     `CUST-REP-7782` (the project's customer_id format).
//   - Already used in the rule-DSL tokenizer (src/lib/rule-dsl/grammar.ts
//     uses simple hashing for error positions; we use FNV-1a here for
//     production-grade distribution).
//   - Returns a 32-bit uint → modulo shardCount gives a stable index.
//
// Files to read alongside this: docs/MULTI_AZ.md (§4 Sharding),
// src/lib/db/multi-az.ts (per-AZ pool), src/lib/db/replica.ts (per-shard
// read-replica router).

/** A single shard's metadata + connection target. */
export interface Shard {
  /** Numeric shard id (0-indexed). */
  id: number;
  /** Human label, e.g. "shard-0". */
  label: string;
  /** Connection string the application opens. */
  connectionString: string;
  /** Which AZ this shard's primary lives in. */
  az: string;
}

/** Where a request for the given key should land. */
export interface ShardRoute {
  /** 0-indexed shard id. */
  shardId: number;
  /** Connection string for that shard's primary. */
  connectionString: string;
  /** AZ the shard's primary lives in (used by AzAwarePool). */
  az: string;
  /** Hash the router computed — exposed for audit logging. */
  hash: number;
}

/**
 * Route a customer_id to its shard.
 *
 * @example
 *   const r = new ShardRouter(4, [...]);
 *   r.route("CUST-REP-7782"); // → { shardId: 2, connectionString: ... }
 */
export class ShardRouter {
  readonly shardCount: number;
  private readonly shards: Shard[];

  constructor(shardCount: number, shards: Shard[]) {
    if (shardCount < 1) {
      throw new Error("shardCount must be ≥ 1");
    }
    if (shards.length !== shardCount) {
      throw new Error(
        `shardCount=${shardCount} but only ${shards.length} shard entries provided`,
      );
    }
    this.shardCount = shardCount;
    this.shards = shards;
  }

  /**
   * FNV-1a 32-bit hash of an arbitrary string. Deterministic — the
   * same key always lands on the same shard, so the demo is stable
   * across restarts.
   */
  hashKey(key: string): number {
    let h = 0x811c9dc5; // FNV-1a 32-bit offset basis
    for (let i = 0; i < key.length; i++) {
      h ^= key.charCodeAt(i);
      // h *= 0x01000193 (FNV prime) — emulate 32-bit overflow with `>>> 0`.
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h >>> 0;
  }

  /**
   * Route a customer_id to its shard.
   *
   * Phase I (this repo): all shards point at the same SQLite file, so
   * the routing decision is correct but the data is co-located. Phase II
   * (production): each shard is a separate Postgres database, partitioned
   * by `customer_id` via pg_partman. The router interface does NOT
   * change between phases.
   */
  route(customerId: string): ShardRoute {
    if (!customerId) {
      throw new Error("customerId is required for sharding");
    }
    const hash = this.hashKey(customerId);
    const shardId = hash % this.shardCount;
    const shard = this.shards[shardId];
    if (!shard) {
      // Defensive — should be unreachable given the constructor check.
      throw new Error(`shard ${shardId} not in shard map`);
    }
    return {
      shardId,
      connectionString: shard.connectionString,
      az: shard.az,
      hash,
    };
  }

  /** List every shard (for /api/v1/multi-az/shards debug surface). */
  list(): Shard[] {
    return [...this.shards];
  }
}

/**
 * Singleton shard router for the hackathon. Four shards, all pointing
 * at the same SQLite file, distributed across the three AZs so the
 * topologySpreadConstraints in the K8s manifests line up with the data
 * layout. In production, swap connectionString for real Postgres URLs
 * (see docs/MULTI_AZ.md §4 + §5).
 */
const SQLITE_PATH = process.env.DATABASE_URL?.replace(/^file:/, "") || "db/custom.db";
const CS = (az: string) => `file:${SQLITE_PATH}#shard-${az}`;
export const shardRouter = new ShardRouter(4, [
  { id: 0, label: "shard-0", connectionString: CS("az-a"), az: "az-a" },
  { id: 1, label: "shard-1", connectionString: CS("az-a"), az: "az-a" },
  { id: 2, label: "shard-2", connectionString: CS("az-b"), az: "az-b" },
  { id: 3, label: "shard-3", connectionString: CS("az-c"), az: "az-c" },
]);
