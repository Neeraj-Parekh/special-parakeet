// src/lib/db/multi-az.ts — AZ-aware connection-pool router.
//
// G6 — Multi-AZ / Multi-Region / Sharding.
//
// In production this module holds N Prisma client pools keyed by AZ label
// (`az-a`, `az-b`, `az-c`) and routes each query to the closest healthy
// pool: writes go to the current leader AZ (leader election via a Consul
// session or a Postgres advisory-lock stub), reads round-robin among the
// healthy read-replicas. On failure of a pool the router marks it
// unhealthy and retries the next AZ.
//
// **Hackathon reality check:** this Vercel deployment runs against a
// single SQLite database (see `src/lib/db.ts` — the production singleton).
// The pool router here is therefore backed by IN-MEMORY STUB POOLS so the
// demo can show the routing decisions + health table without paying for
// three RDS instances. The routing logic is real: in production the only
// change is to swap the `StubPool` class for a thin `PrismaClient` wrapper
// (see docs/MULTI_AZ.md §5 — Production swap).
//
// Files you should also read: docs/MULTI_AZ.md, infra/k8s/multi-az/*.yaml,
// infra/terraform/main.tf, src/lib/db/replica.ts (read-replica fallback
// with circuit-breaker), src/lib/db/sharding.ts (customer_id sharding).

/** A single AZ-labeled connection pool. */
export interface AzPool {
  /** AZ label, e.g. "az-a". */
  readonly az: string;
  /** Connection string the pool would target in production. */
  readonly connectionString: string;
  /** Ping the pool once; resolve to latency in ms or throw on failure. */
  ping(): Promise<number>;
  /** Execute a query string (no params — this is a stub). Returns ms. */
  query(sql: string): Promise<{ latencyMs: number; rows: unknown[] }>;
}

/** In-memory stub pool used by the hackathon deployment. */
export class StubPool implements AzPool {
  readonly az: string;
  readonly connectionString: string;
  /** Toggle to simulate a down AZ (set by tests / health-checker). */
  public down = false;
  /** Toggle to simulate a slow AZ (latency in ms added to each query). */
  public latencyMs = 8;

  constructor(az: string, connectionString: string) {
    this.az = az;
    this.connectionString = connectionString;
  }

  async ping(): Promise<number> {
    if (this.down) {
      throw new Error(`AZ ${this.az} is down (stub)`);
    }
    // Simulate a TCP RTT — under the configured latency budget.
    return this.latencyMs;
  }

  async query(sql: string): Promise<{ latencyMs: number; rows: unknown[] }> {
    if (this.down) {
      throw new Error(`AZ ${this.az} refused query: ${sql.slice(0, 60)}`);
    }
    // No real rows — this is the stub. Return an empty result.
    return { latencyMs: this.latencyMs, rows: [] };
  }
}

/** Health probe result for one AZ. */
export interface AzHealth {
  az: string;
  healthy: boolean;
  latencyMs: number;
}

/** Round-robin read-router + leader-routed write-router. */
export class AzAwarePool {
  private readonly pools: Map<string, AzPool> = new Map();
  private readonly azOrder: string[];
  private leaderAz: string;
  private rrCursor = 0;

  constructor(pools: AzPool[], opts: { leaderAz?: string } = {}) {
    if (pools.length === 0) {
      throw new Error("AzAwarePool requires at least one pool");
    }
    for (const p of pools) {
      this.pools.set(p.az, p);
    }
    this.azOrder = pools.map((p) => p.az);
    this.leaderAz = opts.leaderAz ?? this.azOrder[0];
    if (!this.pools.has(this.leaderAz)) {
      throw new Error(`leaderAz ${this.leaderAz} not in pool list`);
    }
  }

  /** Get the pool handle for a specific AZ. */
  getPool(az: string): AzPool {
    const p = this.pools.get(az);
    if (!p) {
      throw new Error(`unknown AZ: ${az} — known: ${this.azOrder.join(", ")}`);
    }
    return p;
  }

  /** The AZ currently elected leader (receives writes). */
  currentLeader(): string {
    return this.leaderAz;
  }

  /**
   * Promote a new AZ to leader. In production this is the leader-election
   * stub: a Consul session + a `SELECT pg_advisory_lock(...)` or a
   * Raft-style vote decides. Here we just trust the operator.
   */
  electLeader(az: string): void {
    if (!this.pools.has(az)) {
      throw new Error(`cannot elect unknown AZ: ${az}`);
    }
    this.leaderAz = az;
  }

  /**
   * Route a READ to the closest healthy read-replica. Round-robins among
   * healthy AZs so the demo load spreads. Throws if every AZ is down —
   * callers MUST catch and degrade to a stale cache or rules-only REVIEW
   * (never fail-open into a write to a stale replica).
   */
  async routeRead(query: string): Promise<{ az: string; latencyMs: number; rows: unknown[] }> {
    const tried = new Set<string>();
    const startAz = this.rrCursor % this.azOrder.length;
    for (let i = 0; i < this.azOrder.length; i++) {
      const az = this.azOrder[(startAz + i) % this.azOrder.length];
      tried.add(az);
      const pool = this.pools.get(az);
      if (!pool) continue;
      try {
        const res = await pool.query(query);
        this.rrCursor = (startAz + i + 1) % this.azOrder.length;
        return { az, latencyMs: res.latencyMs, rows: res.rows };
      } catch {
        // try the next AZ
        continue;
      }
    }
    throw new Error(
      `routeRead: every AZ failed (${[...tried].join(", ")}). ` +
        "Caller MUST degrade to a cached response or rules-only REVIEW — never fail-open.",
    );
  }

  /**
   * Route a WRITE to the current leader AZ. The leader is single — writes
   * to followers would diverge. If the leader is down, the caller is
   * expected to wait for the operator to `electLeader(...)`; we do NOT
   * auto-promote a follower because that requires a quorum check we
   * cannot perform from a single Next.js process.
   */
  async routeWrite(query: string): Promise<{ az: string; latencyMs: number; rows: unknown[] }> {
    const pool = this.pools.get(this.leaderAz);
    if (!pool) {
      throw new Error(`leader AZ ${this.leaderAz} not found in pool map`);
    }
    const res = await pool.query(query);
    return { az: this.leaderAz, latencyMs: res.latencyMs, rows: res.rows };
  }

  /**
   * Ping every pool and return the health table. The dashboard exposes
   * this at GET /api/v1/multi-az/health (NOT built in this tier — this
   * module is the seam). A pool is healthy iff ping() resolves within
   * 1s; anything that rejects or exceeds the budget is marked unhealthy.
   */
  async healthCheck(): Promise<AzHealth[]> {
    const out: AzHealth[] = [];
    for (const az of this.azOrder) {
      const pool = this.pools.get(az);
      if (!pool) continue;
      try {
        const t0 = Date.now();
        const latency = await Promise.race([
          pool.ping(),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error("ping timeout 1s")), 1000),
          ),
        ]);
        out.push({ az, healthy: true, latencyMs: latency });
        // Date.now() - t0 is wall-clock; pool.ping() returns the stub's
        // advertised latency. Use the stub's value so the table matches
        // the configured budget.
        void t0;
      } catch {
        out.push({ az, healthy: false, latencyMs: -1 });
      }
    }
    return out;
  }
}

/**
 * Singleton AzAwarePool for the hackathon deployment.
 * Three in-memory stub pools — `az-a`, `az-b`, `az-c` — pointing at the
 * same SQLite file. In production swap the StubPool array for a
 * `pools: PrismaClient[]` array initialised from `DATABASE_URL_A`,
 * `DATABASE_URL_B`, `DATABASE_URL_C` env vars (see docs/MULTI_AZ.md §5).
 */
const SQLITE_PATH = process.env.DATABASE_URL?.replace(/^file:/, "") || "db/custom.db";
export const azPool = new AzAwarePool(
  [
    new StubPool("az-a", `file:${SQLITE_PATH}#az-a`),
    new StubPool("az-b", `file:${SQLITE_PATH}#az-b`),
    new StubPool("az-c", `file:${SQLITE_PATH}#az-c`),
  ],
  { leaderAz: "az-a" },
);
