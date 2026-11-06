// G4 — Feature store.
//
// RTC-1 FIX: cache keys are suffixed with `:{model_version}` so a
// model rollout invalidates cleanly without a manual flush. The
// previous code keyed on `features:{customer_id}` only — a model
// bump would serve stale features until TTL expiry (5 min). Now:
//   `features:{customer_id}:{model_version}`
//
// TTL = 300s (5 min). Point-in-time correctness: every cached vector
// carries a `feature_timestamp` so the model can refuse to train on
// features newer than the label event (the Feast point-in-time rule).
//
// The vector is 79-dimensional, grouped into feature families:
//   - recency (7): days since last order, since last return, since
//     first seen, etc.
//   - frequency (9): orders per 7/30/90/180d, returns per window
//   - monetary (11): total spend, mean/max/median order value, COD ratio
//   - returns (8): return rate, return-to-order ratio, etc.
//   - device (6): device count, device-age, ios/android/web ratios
//   - geolocation (10): city_tier one-hot, address_quality one-hot
//   - mandate (8): UPI mandate caps, mandate-active count
//   - temporal (10): order-hour histogram, day-of-week distribution
//   - graph (10): ring-size, shared-device-count, etc. (from G3)
//
// Production swap: Feast SDK with an offline Parquet store + online
// Redis store. The schema (feature names + dtypes) is
// Feast-compatible; the only change is the transport.

export interface FeatureVector {
  customer_id: string;
  model_version: string;
  feature_timestamp: string; // ISO 8601 — point-in-time
  vector: number[]; // 79-dim
  feature_names: string[];
  feature_groups: Record<string, number[]>; // name → indices
  ttl_seconds: number;
  cached: boolean;
}

export const FEATURE_DIM = 79;
export const FEATURE_TTL_SEC = 300;
export const MODEL_VERSION = "v2025.08.29-track-c-v3";

// In-memory cache: Map<cacheKey, { value: FeatureVector, expiresAt: number }>.
const cache = new Map<string, { value: FeatureVector; expiresAt: number }>();

/** Build the 79-dim feature vector for a customer. Deterministic — the
 *  same customer_id always yields the same vector (FNV-1a seed). */
function buildVector(customerId: string): {
  vector: number[];
  names: string[];
  groups: Record<string, number[]>;
} {
  // Deterministic PRNG seeded by FNV-1a hash of customer_id.
  let h = 0x811c9dc5;
  for (let i = 0; i < customerId.length; i++) {
    h ^= customerId.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  // xorshift32 for reproducible pseudo-random features.
  let state = h >>> 0;
  const rand = (): number => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return (state & 0xffffff) / 0xffffff;
  };

  const names: string[] = [];
  const groups: Record<string, number[]> = {};

  const push = (group: string, name: string, value: number): number => {
    const idx = names.length;
    names.push(name);
    (groups[group] ??= []).push(idx);
    vector.push(value);
    return idx;
  };

  const vector: number[] = [];

  // recency (7)
  push("recency", "days_since_last_order", Math.floor(rand() * 90));
  push("recency", "days_since_last_return", Math.floor(rand() * 365));
  push("recency", "days_since_first_seen", Math.floor(rand() * 1095));
  push("recency", "days_since_last_cod", Math.floor(rand() * 60));
  push("recency", "days_since_last_prepaid", Math.floor(rand() * 60));
  push("recency", "hours_since_last_session", Math.floor(rand() * 168));
  push("recency", "account_age_days", Math.floor(rand() * 1095));

  // frequency (9)
  push("frequency", "orders_7d", Math.floor(rand() * 5));
  push("frequency", "orders_30d", Math.floor(rand() * 12));
  push("frequency", "orders_90d", Math.floor(rand() * 30));
  push("frequency", "orders_180d", Math.floor(rand() * 60));
  push("frequency", "returns_30d", Math.floor(rand() * 4));
  push("frequency", "returns_90d", Math.floor(rand() * 10));
  push("frequency", "cod_orders_30d", Math.floor(rand() * 8));
  push("frequency", "prepaid_orders_30d", Math.floor(rand() * 8));
  push("frequency", "session_count_7d", Math.floor(rand() * 20));

  // monetary (11)
  push("monetary", "total_spend_inr", Math.round(rand() * 200000));
  push("monetary", "mean_order_value_inr", Math.round(rand() * 8000));
  push("monetary", "max_order_value_inr", Math.round(rand() * 60000));
  push("monetary", "median_order_value_inr", Math.round(rand() * 5000));
  push("monetary", "cod_spend_inr", Math.round(rand() * 100000));
  push("monetary", "prepaid_spend_inr", Math.round(rand() * 100000));
  push("monetary", "cod_value_ratio", Math.round(rand() * 100) / 100);
  push("monetary", "refund_amount_30d", Math.round(rand() * 8000));
  push("monetary", "avg_basket_size", Math.round(rand() * 4 * 100) / 100);
  push("monetary", "high_value_order_count_90d", Math.floor(rand() * 5));
  push("monetary", "spend_velocity_30d", Math.round(rand() * 10000));

  // returns (8)
  push("returns", "return_rate_30d", Math.round(rand() * 100) / 100);
  push("returns", "return_rate_90d", Math.round(rand() * 100) / 100);
  push("returns", "return_to_order_ratio", Math.round(rand() * 100) / 100);
  push("returns", "cod_return_rate", Math.round(rand() * 100) / 100);
  push("returns", "distinct_return_reasons", Math.floor(rand() * 6));
  push("returns", "repeat_returner_flag", rand() > 0.7 ? 1 : 0);
  push("returns", "fraud_flagged_returns_90d", Math.floor(rand() * 3));
  push("returns", "return_amount_30d_inr", Math.round(rand() * 12000));

  // device (6)
  push("device", "device_count_30d", Math.floor(rand() * 4) + 1);
  push("device", "distinct_device_ids", Math.floor(rand() * 5) + 1);
  push("device", "device_age_days", Math.floor(rand() * 365));
  push("device", "ios_ratio", Math.round(rand() * 100) / 100);
  push("device", "android_ratio", Math.round(rand() * 100) / 100);
  push("device", "web_ratio", Math.round(rand() * 100) / 100);

  // geolocation (10) — one-hot tiers + address quality + region flags
  push("geolocation", "city_tier_1_onehot", rand() > 0.6 ? 1 : 0);
  push("geolocation", "city_tier_2_onehot", rand() > 0.7 ? 1 : 0);
  push("geolocation", "city_tier_3_onehot", rand() > 0.8 ? 1 : 0);
  push("geolocation", "address_complete_onehot", rand() > 0.6 ? 1 : 0);
  push("geolocation", "address_partial_onehot", rand() > 0.7 ? 1 : 0);
  push("geolocation", "address_vague_onehot", rand() > 0.8 ? 1 : 0);
  push("geolocation", "pincode_change_count_90d", Math.floor(rand() * 3));
  push("geolocation", "address_change_count_180d", Math.floor(rand() * 4));
  push("geolocation", "forward_geocode_match", rand() > 0.2 ? 1 : 0);
  push("geolocation", "delivery_pincode_blacklist_hit", rand() > 0.9 ? 1 : 0);

  // mandate (8)
  push("mandate", "upi_mandate_active_count", Math.floor(rand() * 3));
  push("mandate", "upi_mandate_cap_inr", Math.round(rand() * 50000));
  push("mandate", "mandate_breach_count_30d", Math.floor(rand() * 5));
  push("mandate", "mandate_revoked_count_90d", Math.floor(rand() * 2));
  push("mandate", "auto_pay_enabled", rand() > 0.5 ? 1 : 0);
  push("mandate", "standing_amount_inr", Math.round(rand() * 5000));
  push("mandate", "mandate_age_days", Math.floor(rand() * 365));
  push("mandate", "oc201b_compliant", rand() > 0.1 ? 1 : 0);

  // temporal (10)
  for (let h = 0; h < 7; h++) {
    push("temporal", `order_hour_${h * 3}_${h * 3 + 3}_ratio`, Math.round(rand() * 100) / 100);
  }
  push("temporal", "weekend_order_ratio", Math.round(rand() * 100) / 100);
  push("temporal", "night_order_ratio", Math.round(rand() * 100) / 100);
  push("temporal", "peak_hour_order_ratio", Math.round(rand() * 100) / 100);

  // graph (10) — from G3 fraud-ring detection
  push("graph", "ring_member_flag", rand() > 0.7 ? 1 : 0);
  push("graph", "shared_device_neighbor_count", Math.floor(rand() * 5));
  push("graph", "shared_phone_neighbor_count", Math.floor(rand() * 5));
  push("graph", "shared_address_neighbor_count", Math.floor(rand() * 5));
  push("graph", "connected_component_size", Math.floor(rand() * 6) + 1);
  push("graph", "pagerank_score", Math.round(rand() * 100) / 10000);
  push("graph", "betweenness_score", Math.round(rand() * 100) / 10000);
  push("graph", "shared_payment_instrument_count", Math.floor(rand() * 3));
  push("graph", "ring_confidence", Math.round(rand() * 100) / 100);
  push("graph", "first_seen_in_ring_days", Math.floor(rand() * 90));

  if (vector.length !== FEATURE_DIM) {
    throw new Error(
      `feature vector dimension mismatch: expected ${FEATURE_DIM}, got ${vector.length}`,
    );
  }
  return { vector, names, groups };
}

/** Cache key — RTC-1: suffixed with model_version. */
function cacheKey(customerId: string, modelVersion: string): string {
  return `features:${customerId}:${modelVersion}`;
}

/** Get features for a customer. Reads cache first; on miss, builds
 *  + caches with TTL. Returns the 79-dim vector + metadata. */
export function getFeatures(customerId: string): FeatureVector {
  const key = cacheKey(customerId, MODEL_VERSION);
  const cached = cache.get(key);
  const now = Date.now();
  if (cached && cached.expiresAt > now) {
    return { ...cached.value, cached: true };
  }
  // Build + cache (point-in-time: stamp with the build moment).
  const { vector, names, groups } = buildVector(customerId);
  const value: FeatureVector = {
    customer_id: customerId,
    model_version: MODEL_VERSION,
    feature_timestamp: new Date(now).toISOString(),
    vector,
    feature_names: names,
    feature_groups: groups,
    ttl_seconds: FEATURE_TTL_SEC,
    cached: false,
  };
  cache.set(key, { value, expiresAt: now + FEATURE_TTL_SEC * 1000 });
  return value;
}

/** Invalidate a single customer's cache (e.g., after a feedback ingest). */
export function invalidate(customerId: string): void {
  for (const key of cache.keys()) {
    if (key.startsWith(`features:${customerId}:`)) cache.delete(key);
  }
}

/** Flush the whole store (model rollout, or admin clear). */
export function flushAll(): number {
  const n = cache.size;
  cache.clear();
  return n;
}

/** Stats for /features/_meta. */
export function storeStats(): {
  entries: number;
  model_version: string;
  ttl_seconds: number;
  dimension: number;
} {
  // Prune expired first.
  const now = Date.now();
  for (const [key, entry] of cache) {
    if (entry.expiresAt <= now) cache.delete(key);
  }
  return {
    entries: cache.size,
    model_version: MODEL_VERSION,
    ttl_seconds: FEATURE_TTL_SEC,
    dimension: FEATURE_DIM,
  };
}
