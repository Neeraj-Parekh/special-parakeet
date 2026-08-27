// Mock data for the RTO Trust Layer dashboard preview mode.
//
// Each value mirrors the shape the Python API returns (see
// /home/z/my-project/upload/RTO_Trust_Layer_FULL/src/api/routes.py for
// the source of truth). When the Python backend at API_BASE_URL is
// unreachable, the Next.js API routes return these mock values + set
// `X-Mock-Mode: true` so the frontend can badge the experience.
//
// Three demo orders cover the three decision classes (ACCEPT/REVIEW/
// REJECT) and the four Track-C decision_source values:
//   rules_engine_block, mandate_breach, cost_optimal_bmr,
//   cost_optimal_bmr_review_rule
//
// All numbers are derived from the 8-row cost table in
// docs/cost_table.md so the cost curves + cost breakdown look like the
// real model output, not random noise.

export type Decision = "ACCEPT" | "REVIEW" | "REJECT";
export type DecisionSource =
  | "rules_engine_block"
  | "mandate_breach"
  | "mandate_invalid"
  | "mandate_review_required"
  | "degraded_review"
  | "cost_optimal_bmr"
  | "cost_optimal_bmr_review_rule";

export interface OrderInput {
  order_id: string;
  amount_inr: number;
  category: string;
  customer_id: string;
  address_quality: "complete" | "partial" | "vague";
  city_tier: "tier_1" | "tier_2" | "tier_3";
  payment_method: "COD" | "Prepaid";
  prior_orders: number;
  prior_returns: number;
  items: number;
  order_hour: number;
  device: string;
}

export interface ReasonCode {
  feature: string;
  value: string | number;
  delta_prob: number;
  direction: "up" | "down";
}

export interface CostBreakdown {
  ACCEPT: number;
  REVIEW: number;
  REJECT: number;
}

export interface ScoreResponse {
  prediction_id: string;
  risk_score: number | null;
  probability: number | null;
  decision: Decision | null;
  decision_source: DecisionSource;
  cost_breakdown: CostBreakdown | null;
  explanation: ReasonCode[];
  rule_fired: string | null;
  degraded: boolean;
  policy_hint: string | null;
  model_version: string;
  latency_ms: number;
  case_id: string | null;
  mandate: {
    verdict: string;
    note: string | null;
    verdict_reason: string | null;
    mandate_type: string | null;
    bh_purpose_code: string | null;
  };
  audit_trail_url: string | null;
  timestamp: string;
  gate_thresholds: {
    policy: string;
    weights: Record<string, number>;
    legacy_accept_t: number;
    legacy_reject_t: number;
  };
  replayed?: boolean;
}

export interface DemoOrder {
  label: string;
  description: string;
  expected: Decision;
  order: OrderInput;
}

export const DEMO_ORDERS: DemoOrder[] = [
  {
    label: "Repeat customer",
    description: "Prepaid ₹2,400 · precise address · tier_1 · 12 prior orders · 0 returns",
    expected: "ACCEPT",
    order: {
      order_id: "ORD-REP-001",
      amount_inr: 2400,
      category: "Fashion",
      customer_id: "CUST-REP-7782",
      address_quality: "complete",
      city_tier: "tier_1",
      payment_method: "Prepaid",
      prior_orders: 12,
      prior_returns: 0,
      items: 1,
      order_hour: 14,
      device: "iOS App",
    },
  },
  {
    label: "High-value COD",
    description: "COD ₹52,000 · vague address · tier_3 · new customer · 0 returns",
    expected: "REJECT",
    order: {
      order_id: "ORD-HVC-002",
      amount_inr: 52000,
      category: "Electronics",
      customer_id: "CUST-NEW-0001",
      address_quality: "vague",
      city_tier: "tier_3",
      payment_method: "COD",
      prior_orders: 0,
      prior_returns: 0,
      items: 1,
      order_hour: 22,
      device: "Android App",
    },
  },
  {
    label: "Prior returns",
    description: "COD ₹8,400 · vague address · tier_2 · 3 prior returns · 5 prior orders",
    expected: "REVIEW",
    order: {
      order_id: "ORD-RET-003",
      amount_inr: 8400,
      category: "Health",
      customer_id: "CUST-RET-3022",
      address_quality: "vague",
      city_tier: "tier_2",
      payment_method: "COD",
      prior_orders: 5,
      prior_returns: 3,
      items: 2,
      order_hour: 19,
      device: "Web",
    },
  },
];

// Default rules — mirror src/rules/engine.py's seed list so the Rules
// Manager page shows the same defaults the Python service ships with.
export interface Rule {
  rule_id: string;
  name: string;
  field: string;
  op: "gt" | "lt" | "eq" | "in";
  value: number | string | boolean | Array<string | number>;
  action: "BLOCK" | "REVIEW";
  priority: number;
  created_by?: string;
  active?: boolean;
}

export const DEFAULT_RULES: Rule[] = [
  {
    rule_id: "RULE-001",
    name: "Block COD > ₹50K from new customers",
    field: "amount_inr",
    op: "gt",
    value: 50000,
    action: "BLOCK",
    priority: 10,
    created_by: "admin",
    active: true,
  },
  {
    rule_id: "RULE-002",
    name: "High-value vague COD review",
    field: "address_quality",
    op: "eq",
    value: "vague",
    action: "REVIEW",
    priority: 50,
    created_by: "admin",
    active: true,
  },
  {
    rule_id: "RULE-003",
    name: "Repeat-returner review (>2 returns)",
    field: "prior_returns",
    op: "gt",
    value: 2,
    action: "REVIEW",
    priority: 60,
    created_by: "admin",
    active: true,
  },
  {
    rule_id: "RULE-004",
    name: "Block tier_3 night COD (anti-fraud)",
    field: "order_hour",
    op: "gt",
    value: 22,
    action: "BLOCK",
    priority: 20,
    created_by: "admin",
    active: false,
  },
];

// Sample audit records — one per demo order plus a few historical
// entries so the Audit Explorer has a real list to render. Each record
// carries the full Track-C/D/H enrichment: decision_source,
// cost_breakdown, mandate_type, bh_purpose_code, device_id, user_id,
// raw_hash + prev_hash (the per-record chain).
export interface AuditRecord {
  audit_id: string;
  prediction_id: string;
  raw_hash: string;
  prev_hash: string;
  created_at: string;
  body: {
    request: {
      order_id: string;
      amount_inr: number;
      category: string;
      customer_id: string;
      address_quality: string;
      city_tier: string;
      payment_method: string;
      prior_orders: number;
      prior_returns: number;
      items: number;
    };
    probability: number | null;
    decision: string;
    decision_source: string;
    cost_breakdown: CostBreakdown | null;
    reason_codes: ReasonCode[];
    mandate_verdict: string;
    mandate_verdict_reason: string | null;
    mandate_type: string | null;
    bh_purpose_code: string | null;
    device_id: string | null;
    user_id: string | null;
    breach_note: string | null;
    rule_fired: string | null;
    degraded: boolean;
    features_used: Record<string, number>;
    latency_ms: number;
    case_id: string | null;
    prediction_id: string;
    model_version: string;
  };
}

function fakeHash(seed: string): string {
  // Deterministic 64-hex-char hash so the chain looks valid but is
  // obviously synthetic. NOT real SHA-256 — preview-only.
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = (h * 0x01000193) >>> 0;
  }
  let out = "";
  let n = h;
  while (out.length < 64) {
    n = (n * 0x01000193 + 0xb5297a4d) >>> 0;
    out += n.toString(16).padStart(8, "0").slice(-8);
  }
  return out.slice(0, 64);
}

function buildAuditChain(): AuditRecord[] {
  const now = Date.now();
  const samples: Array<Partial<AuditRecord["body"]> & {
    order_id: string;
    customer_id: string;
  }> = [
    {
      order_id: "ORD-REP-001",
      customer_id: "CUST-REP-7782",
      decision: "ACCEPT",
      decision_source: "cost_optimal_bmr",
      probability: 0.082,
      mandate_verdict: "VALID",
      rule_fired: null,
      cost_breakdown: { ACCEPT: 41, REVIEW: 46, REJECT: 410 },
      request: {
        order_id: "ORD-REP-001",
        amount_inr: 2400,
        category: "Fashion",
        customer_id: "CUST-REP-7782",
        address_quality: "complete",
        city_tier: "tier_1",
        payment_method: "Prepaid",
        prior_orders: 12,
        prior_returns: 0,
        items: 1,
      },
    },
    {
      order_id: "ORD-HVC-002",
      customer_id: "CUST-NEW-0001",
      decision: "REJECT",
      decision_source: "rules_engine_block",
      probability: null,
      rule_fired: "RULE-001",
      mandate_verdict: "VALID",
      breach_note: null,
      cost_breakdown: null,
      request: {
        order_id: "ORD-HVC-002",
        amount_inr: 52000,
        category: "Electronics",
        customer_id: "CUST-NEW-0001",
        address_quality: "vague",
        city_tier: "tier_3",
        payment_method: "COD",
        prior_orders: 0,
        prior_returns: 0,
        items: 1,
      },
    },
    {
      order_id: "ORD-RET-003",
      customer_id: "CUST-RET-3022",
      decision: "REVIEW",
      decision_source: "cost_optimal_bmr_review_rule",
      probability: 0.487,
      rule_fired: "RULE-002",
      mandate_verdict: "VALID",
      cost_breakdown: { ACCEPT: 292, REVIEW: 36, REJECT: 488 },
      request: {
        order_id: "ORD-RET-003",
        amount_inr: 8400,
        category: "Health",
        customer_id: "CUST-RET-3022",
        address_quality: "vague",
        city_tier: "tier_2",
        payment_method: "COD",
        prior_orders: 5,
        prior_returns: 3,
        items: 2,
      },
    },
    {
      order_id: "ORD-MND-101",
      customer_id: "CUST-UPI-9912",
      decision: "REJECT",
      decision_source: "mandate_breach",
      probability: null,
      rule_fired: null,
      mandate_verdict: "BREACH",
      mandate_verdict_reason: "amount_exceeds_mandate_cap",
      mandate_type: "upi_circle_delegation",
      bh_purpose_code: "P02",
      device_id: "dev_8a4f",
      user_id: "usr_77c1",
      breach_note: "mandate_amount_breach",
      cost_breakdown: null,
      request: {
        order_id: "ORD-MND-101",
        amount_inr: 6800,
        category: "Accessories",
        customer_id: "CUST-UPI-9912",
        address_quality: "complete",
        city_tier: "tier_2",
        payment_method: "COD",
        prior_orders: 4,
        prior_returns: 1,
        items: 1,
      },
    },
    {
      order_id: "ORD-DGR-205",
      customer_id: "CUST-NEW-0142",
      decision: "REVIEW",
      decision_source: "degraded_review",
      probability: null,
      rule_fired: null,
      mandate_verdict: "VALID",
      degraded: true,
      cost_breakdown: null,
      request: {
        order_id: "ORD-DGR-205",
        amount_inr: 12300,
        category: "Electronics",
        customer_id: "CUST-NEW-0142",
        address_quality: "partial",
        city_tier: "tier_3",
        payment_method: "COD",
        prior_orders: 1,
        prior_returns: 0,
        items: 1,
      },
    },
    {
      order_id: "ORD-RVW-418",
      customer_id: "CUST-RET-3022",
      decision: "REVIEW",
      decision_source: "cost_optimal_bmr_review_rule",
      probability: 0.512,
      rule_fired: "RULE-003",
      mandate_verdict: "VALID",
      cost_breakdown: { ACCEPT: 307, REVIEW: 41, REJECT: 488 },
      request: {
        order_id: "ORD-RVW-418",
        amount_inr: 4600,
        category: "Fashion",
        customer_id: "CUST-RET-3022",
        address_quality: "complete",
        city_tier: "tier_2",
        payment_method: "COD",
        prior_orders: 5,
        prior_returns: 3,
        items: 1,
      },
    },
    {
      order_id: "ORD-ACC-530",
      customer_id: "CUST-REP-7782",
      decision: "ACCEPT",
      decision_source: "cost_optimal_bmr",
      probability: 0.118,
      mandate_verdict: "VALID",
      rule_fired: null,
      cost_breakdown: { ACCEPT: 59, REVIEW: 50, REJECT: 500 },
      request: {
        order_id: "ORD-ACC-530",
        amount_inr: 980,
        category: "Accessories",
        customer_id: "CUST-REP-7782",
        address_quality: "complete",
        city_tier: "tier_1",
        payment_method: "Prepaid",
        prior_orders: 12,
        prior_returns: 0,
        items: 1,
      },
    },
    {
      order_id: "ORD-REJ-642",
      customer_id: "CUST-NEW-0998",
      decision: "REJECT",
      decision_source: "rules_engine_block",
      probability: null,
      rule_fired: "RULE-001",
      mandate_verdict: "VALID",
      breach_note: null,
      cost_breakdown: null,
      request: {
        order_id: "ORD-REJ-642",
        amount_inr: 54500,
        category: "Electronics",
        customer_id: "CUST-NEW-0998",
        address_quality: "vague",
        city_tier: "tier_3",
        payment_method: "COD",
        prior_orders: 0,
        prior_returns: 0,
        items: 1,
      },
    },
  ];

  const records: AuditRecord[] = [];
  let prevHash = "0".repeat(64);
  samples.forEach((s, i) => {
    const audit_id = `aud_${(1000 + i).toString(36)}${i.toString().padStart(2, "0")}`;
    const prediction_id = `pred-${(0x4d3a + i * 0x1111).toString(16)}-${i
      .toString()
      .padStart(4, "0")}`;
    const created_at = new Date(now - (samples.length - i) * 1000 * 60 * 17).toISOString();
    const body = {
      request: {
        ...s.request!,
        customer_id: s.request!.customer_id, // already redacted-shaped
      },
      probability: s.probability ?? null,
      decision: s.decision!,
      decision_source: s.decision_source!,
      cost_breakdown: s.cost_breakdown ?? null,
      reason_codes: [],
      mandate_verdict: s.mandate_verdict ?? "VALID",
      mandate_verdict_reason: s.mandate_verdict_reason ?? null,
      mandate_type: s.mandate_type ?? null,
      bh_purpose_code: s.bh_purpose_code ?? null,
      device_id: s.device_id ?? null,
      user_id: s.user_id ?? null,
      breach_note: s.breach_note ?? null,
      rule_fired: s.rule_fired ?? null,
      degraded: s.degraded ?? false,
      features_used: {},
      latency_ms: 12 + i,
      case_id: s.decision === "REVIEW" ? `case_${i}` : null,
      prediction_id,
      model_version: "v2.1",
    };
    const raw_hash = fakeHash(audit_id + prevHash + JSON.stringify(body));
    records.push({
      audit_id,
      prediction_id,
      raw_hash,
      prev_hash: prevHash,
      created_at,
      body,
    });
    prevHash = raw_hash;
  });
  return records;
}

export const SAMPLE_AUDIT_RECORDS: AuditRecord[] = buildAuditChain();

// Merkle proof mock — single interval sealing all 8 records above.
export interface MerkleProof {
  record_id: number;
  leaf_hash: string;
  interval_id: number;
  position: number;
  proof: Array<{ position: "left" | "right"; hash: string }>;
  merkle_root: string;
  prev_interval_root: string;
  leaf_count: number;
  sealed_at: string | null;
}

export function mockMerkleProof(recordId: number): MerkleProof | null {
  const idx = SAMPLE_AUDIT_RECORDS.findIndex(
    (r) => r.audit_id === `aud_${(1000 + recordId - 1).toString(36)}${(recordId - 1)
      .toString()
      .padStart(2, "0")}`
  );
  if (idx < 0) return null;
  const record = SAMPLE_AUDIT_RECORDS[idx];
  // RFC 6962-style proof: walk the tree, recording siblings.
  const size = 8;
  const leaves = SAMPLE_AUDIT_RECORDS.slice(0, size).map((r) => r.raw_hash);
  let level = leaves;
  let i = idx;
  const proof: MerkleProof["proof"] = [];
  while (level.length > 1) {
    const siblingIdx = i ^ 1;
    const sibling = level[Math.min(siblingIdx, level.length - 1)];
    proof.push({
      position: siblingIdx > i ? "right" : "left",
      hash: sibling,
    });
    const next: string[] = [];
    for (let k = 0; k < level.length; k += 2) {
      const a = level[k];
      const b = level[Math.min(k + 1, level.length - 1)];
      next.push(fakeHash(a + b));
    }
    level = next;
    i = Math.floor(i / 2);
  }
  return {
    record_id: recordId,
    leaf_hash: record.raw_hash,
    interval_id: 1,
    position: idx,
    proof,
    merkle_root: level[0],
    prev_interval_root: "0".repeat(64),
    leaf_count: size,
    sealed_at: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
  };
}

// Cost-curve sweep — mirrors docs/cost_table.md's 8 threshold rows
// (Drummond-Holte 2006). The real backend returns these via
// /v1/policy/cost-curves after bootstrap-CI'ing the labeled dataset;
// here we just paste the published numbers.
export interface CostCurvePoint {
  threshold: number;
  cost: number;
  precision: number;
  recall: number;
  fp: number;
  fn: number;
  flagged: number;
  ci_lower?: number;
  ci_upper?: number;
}

export const SAMPLE_COST_CURVES: CostCurvePoint[] = [
  { threshold: 0.15, cost: 1258, precision: 0.406, recall: 0.789, fp: 394, fn: 72, flagged: 663, ci_lower: 1180, ci_upper: 1330 },
  { threshold: 0.2, cost: 1374, precision: 0.443, recall: 0.742, fp: 318, fn: 88, flagged: 571, ci_lower: 1300, ci_upper: 1455 },
  { threshold: 0.25, cost: 1542, precision: 0.465, recall: 0.689, fp: 270, fn: 106, flagged: 505, ci_lower: 1470, ci_upper: 1620 },
  { threshold: 0.3, cost: 1718, precision: 0.485, recall: 0.636, fp: 230, fn: 124, flagged: 447, ci_lower: 1640, ci_upper: 1795 },
  { threshold: 0.35, cost: 1898, precision: 0.506, recall: 0.584, fp: 194, fn: 142, flagged: 393, ci_lower: 1820, ci_upper: 1980 },
  { threshold: 0.4, cost: 2090, precision: 0.533, recall: 0.528, fp: 158, fn: 161, flagged: 338, ci_lower: 2010, ci_upper: 2180 },
  { threshold: 0.5, cost: 2592, precision: 0.581, recall: 0.39, fp: 96, fn: 208, flagged: 229, ci_lower: 2500, ci_upper: 2680 },
  { threshold: 0.6, cost: 3003, precision: 0.651, recall: 0.279, fp: 51, fn: 246, flagged: 146, ci_lower: 2910, ci_upper: 3095 },
];

export const SAMPLE_OPTIMAL_THRESHOLD = 0.15;

// Model health — champion + drift state.
export const SAMPLE_MODEL_CURRENT = {
  champion: {
    version: "v2.1",
    deployed_at: "2026-08-25T09:14:00Z",
    metrics: {
      pr_auc: 0.72,
      roc_auc: 0.808,
      precision: 0.547,
      recall: 0.648,
      f1: 0.593,
      brier: 0.183,
    },
    training_data: "CODScore synthetic-but-realistic (7235 rows)",
    notes: "HistGradientBoostingClassifier (sklearn 1.5). Group leakage asserted 0.",
  },
};

export const SAMPLE_DRIFT = {
  status: "OK",
  n_observed: 412,
  psi: {
    amount_inr: 0.018,
    prior_returns: 0.024,
    prior_orders: 0.013,
    items: 0.009,
    order_hour: 0.022,
  },
  worst_psi: 0.024,
};

export const SAMPLE_USAGE = {
  counts: { "24": 142, "168": 968, "720": 4120 },
  since_hours: [24, 168, 720],
  intervals_sealed_total: 1,
  intervals_sealed_in_window: 1,
  latest_interval: {
    interval_id: 1,
    start_record_id: 1,
    end_record_id: 8,
    merkle_root: SAMPLE_AUDIT_RECORDS[7]
      ? SAMPLE_AUDIT_RECORDS[7].raw_hash
      : "0".repeat(64),
    prev_interval_root: "0".repeat(64),
    leaf_count: 8,
    sealed_at: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
  },
  note: "aggregate counts (multi-tenant merchant_id not yet implemented — audit_records.body carries merchant_id in JSONB, ready for a GROUP BY once /risk/score wires the X-Merchant-Id header)",
};

// Mock /metrics — Prometheus text format. The dashboard parses the
// rto_drift_ddm_state + rto_drift_adwin_state gauges (Track G).
export const SAMPLE_METRICS_TEXT = `# HELP rto_decisions_total Total /risk/score decisions by verdict.
# TYPE rto_decisions_total counter
rto_decisions_total{decision="ACCEPT",degraded="False"} 248
rto_decisions_total{decision="REVIEW",degraded="False"} 96
rto_decisions_total{decision="REJECT",degraded="False"} 41
rto_decisions_total{decision="REVIEW",degraded="True"} 7
# HELP rto_latency_seconds Latency for /risk/score in seconds.
# TYPE rto_latency_seconds summary
rto_latency_seconds_count 392
rto_latency_seconds_sum 4.518
rto_latency_seconds{quantile="0.5"} 0.011
rto_latency_seconds{quantile="0.95"} 0.024
rto_latency_seconds{quantile="0.99"} 0.038
# HELP rto_circuit_state Circuit-breaker state (0=CLOSED, 1=HALF_OPEN, 2=OPEN).
# TYPE rto_circuit_state gauge
rto_circuit_state 0
# HELP rto_drift_ddm_state DDM drift detector state (0=STABLE, 1=WARNING, 2=DRIFT).
# TYPE rto_drift_ddm_state gauge
rto_drift_ddm_state 0
# HELP rto_drift_adwin_state ADWIN drift detector state (0=STABLE, 1=WARNING, 2=DRIFT).
# TYPE rto_drift_adwin_state gauge
rto_drift_adwin_state 0
# HELP rto_drift_ddm_error_rate DDM running error rate.
# TYPE rto_drift_ddm_error_rate gauge
rto_drift_ddm_error_rate 0.183
# HELP rto_drift_adwin_window_len ADWIN current window length.
# TYPE rto_drift_adwin_window_len gauge
rto_drift_adwin_window_len 412
`;

export interface VerifyChainResult {
  intact: boolean;
  records_checked: number;
  first_bad_audit_id: string | null;
}

export const SAMPLE_VERIFY_CHAIN: VerifyChainResult = {
  intact: true,
  records_checked: SAMPLE_AUDIT_RECORDS.length,
  first_bad_audit_id: null,
};

// ----------------------------------------------------------------------------
// Mock scorer — mirrors the Track C decision precedence (rules → mandate
// → circuit → cost-optimizer). The probability is a hand-tuned function
// of the order so the three demo orders land in their expected decision
// classes. This is for preview only — the real Python service ships
// HistGradientBoostingClassifier + Bahnsen BMR.
// ----------------------------------------------------------------------------

function ruleMatches(order: OrderInput, rule: Rule): boolean {
  const v = (order as unknown as Record<string, unknown>)[rule.field];
  if (v === undefined) return false;
  const target = rule.value;
  switch (rule.op) {
    case "gt":
      return typeof v === "number" && typeof target === "number" && v > target;
    case "lt":
      return typeof v === "number" && typeof target === "number" && v < target;
    case "eq":
      return String(v) === String(target);
    case "in":
      return Array.isArray(target) && target.some((t) => String(t) === String(v));
    default:
      return false;
  }
}

export function mockScore(
  order: OrderInput,
  rules: Rule[] = DEFAULT_RULES,
  mandateHeader?: string | null,
  mandateType: string | null = null,
): ScoreResponse {
  const t0 = Date.now();
  const activeRules = rules.filter((r) => r.active !== false);

  // Step 1 — rules-engine BLOCK short-circuits.
  const blockRule = activeRules.find(
    (r) => r.action === "BLOCK" && ruleMatches(order, r),
  );
  let decision: Decision | null;
  let decision_source: DecisionSource;
  let probability: number | null;
  let cost_breakdown: CostBreakdown | null = null;
  let rule_fired: string | null = null;
  let breach_note: string | null = null;
  let mandate_verdict = "VALID";
  let mandate_verdict_reason: string | null = null;
  let bh_purpose_code: string | null = null;

  // Step 2 — mandate breach if the X-Mandate header was provided AND
  // amount exceeds a small threshold (mimics the mandate_amount_breach
  // path so the Mandate demo flow is reproducible).
  if (mandateHeader) {
    if (mandateType === "upi_circle_delegation") {
      // UPI Circle hard cap ₹5,000/txn (OC-201B)
      if (order.amount_inr > 5000) {
        decision = "REJECT";
        decision_source = "mandate_breach";
        mandate_verdict = "BREACH";
        mandate_verdict_reason = "amount_exceeds_per_txn_cap";
        bh_purpose_code = "P02";
        probability = null;
        breach_note = "mandate_amount_breach";
        return finalizeMock(
          order,
          decision,
          decision_source,
          probability,
          cost_breakdown,
          [],
          rule_fired,
          false,
          t0,
          {
            verdict: mandate_verdict,
            note: breach_note,
            verdict_reason: mandate_verdict_reason,
            mandate_type: mandateType,
            bh_purpose_code,
          },
        );
      }
    } else if (order.amount_inr > 100000) {
      decision = "REJECT";
      decision_source = "mandate_breach";
      mandate_verdict = "BREACH";
      mandate_verdict_reason = "amount_exceeds_mandate_cap";
      breach_note = "mandate_amount_breach";
      probability = null;
      return finalizeMock(
        order,
        decision,
        decision_source,
        probability,
        cost_breakdown,
        [],
        rule_fired,
        false,
        t0,
        {
          verdict: mandate_verdict,
          note: breach_note,
          verdict_reason: mandate_verdict_reason,
          mandate_type: mandateType,
          bh_purpose_code,
        },
      );
    }
  }

  if (blockRule) {
    decision = "REJECT";
    decision_source = "rules_engine_block";
    rule_fired = blockRule.rule_id;
    probability = null;
    return finalizeMock(
      order,
      decision,
      decision_source,
      probability,
      cost_breakdown,
      [],
      rule_fired,
      false,
      t0,
      {
        verdict: mandate_verdict,
        note: breach_note,
        verdict_reason: mandate_verdict_reason,
        mandate_type: mandateType,
        bh_purpose_code,
      },
    );
  }

  // Step 4 — Bahnsen BMR (mock probability).
  // Bahnsen-style probability — calibrated so the 3 demo orders land
  // in their expected decision classes (Repeat customer → ACCEPT,
  // High-value COD → REJECT via RULE-001, Prior returns → REVIEW).
  // Base rate ~0.10 (CODScore synthetic dataset's ~23% positive rate
  // minus the obvious-good cases). Each flag nudges the probability.
  let p = 0.1;
  if (order.payment_method === "COD") p += 0.18;
  else if (order.payment_method === "Prepaid") p -= 0.05;
  if (order.address_quality === "vague") p += 0.14;
  else if (order.address_quality === "partial") p += 0.05;
  else if (order.address_quality === "complete") p -= 0.04;
  if (order.city_tier === "tier_3") p += 0.12;
  else if (order.city_tier === "tier_2") p += 0.03;
  else if (order.city_tier === "tier_1") p -= 0.02;
  const priorReturnRate =
    order.prior_orders > 0 ? order.prior_returns / order.prior_orders : 0;
  p += priorReturnRate * 0.5; // repeat-returner penalty
  if (order.prior_orders === 0) p += 0.06; // new customer
  else if (order.prior_orders >= 10) p -= 0.03; // loyal customer
  if (order.amount_inr > 10000) p += 0.04;
  if (order.amount_inr > 30000) p += 0.06;
  if (order.order_hour >= 21 || order.order_hour <= 4) p += 0.04;
  p = Math.max(0.02, Math.min(0.97, p));
  probability = p;

  // Per-decision expected cost (Bahnsen Eq.1) with the proper
  // REVIEW cost: c_otp + (1-p)*c_fp + (1-otp_eff)*c_fn*p.
  // The (1-p)*c_fp term is the friction cost on legit orders that the
  // OTP gate annoys. Without it the cost-optimizer always prefers
  // REVIEW at low p (which would mean the Repeat customer demo
  // wouldn't score ACCEPT — the expected demo verdict).
  const c_fp = 50;
  const c_fn = 600;
  const c_otp = 5;
  const c_block = 1000;
  const otp_eff = 0.82;
  const cost_accept = c_fn * p;
  const cost_review = c_otp + (1 - otp_eff) * c_fn * p + (1 - p) * c_fp;
  const cost_reject = c_block * (1 - p);
  cost_breakdown = {
    ACCEPT: Math.round(cost_accept),
    REVIEW: Math.round(cost_review),
    REJECT: Math.round(cost_reject),
  };

  // Argmin — cost-optimal action.
  const min = Math.min(cost_accept, cost_review, cost_reject);
  let policy_hint: Decision;
  if (min === cost_accept) policy_hint = "ACCEPT";
  else if (min === cost_review) policy_hint = "REVIEW";
  else policy_hint = "REJECT";
  decision = policy_hint;
  decision_source = "cost_optimal_bmr";

  // REVIEW rule gate — if a REVIEW rule fired AND cost-optimal said
  // ACCEPT, force REVIEW (Track C precedence).
  const reviewRule = activeRules.find(
    (r) => r.action === "REVIEW" && ruleMatches(order, r),
  );
  if (reviewRule && decision === "ACCEPT") {
    decision = "REVIEW";
    decision_source = "cost_optimal_bmr_review_rule";
    rule_fired = reviewRule.rule_id;
  } else if (reviewRule) {
    rule_fired = reviewRule.rule_id;
  }

  const reasons: ReasonCode[] = buildReasonCodes(order, p);
  return finalizeMock(
    order,
    decision,
    decision_source,
    probability,
    cost_breakdown,
    reasons,
    rule_fired,
    false,
    t0,
    {
      verdict: mandate_verdict,
      note: breach_note,
      verdict_reason: mandate_verdict_reason,
      mandate_type: mandateType,
      bh_purpose_code,
    },
  );
}

function buildReasonCodes(order: OrderInput, p: number): ReasonCode[] {
  const out: ReasonCode[] = [];
  const base = 0.18;
  if (order.payment_method === "COD") {
    out.push({
      feature: "payment_method",
      value: "COD",
      delta_prob: 0.16,
      direction: "up",
    });
  }
  if (order.address_quality === "vague") {
    out.push({
      feature: "address_quality",
      value: "vague",
      delta_prob: 0.14,
      direction: "up",
    });
  }
  if (order.city_tier === "tier_3") {
    out.push({
      feature: "city_tier",
      value: "tier_3",
      delta_prob: 0.12,
      direction: "up",
    });
  }
  if (order.prior_orders === 0) {
    out.push({
      feature: "prior_orders",
      value: 0,
      delta_prob: 0.06,
      direction: "up",
    });
  }
  if (order.prior_returns > 0) {
    const r = (order.prior_returns / Math.max(order.prior_orders, 1)) * 0.35;
    out.push({
      feature: "prior_returns",
      value: order.prior_returns,
      delta_prob: Number(r.toFixed(3)),
      direction: "up",
    });
  }
  if (order.amount_inr > 10000) {
    out.push({
      feature: "amount_inr",
      value: order.amount_inr,
      delta_prob: 0.05,
      direction: "up",
    });
  }
  if (p < base && out.length === 0) {
    out.push({
      feature: "payment_method",
      value: "Prepaid",
      delta_prob: -0.16,
      direction: "down",
    });
  }
  return out.slice(0, 5);
}

function finalizeMock(
  order: OrderInput,
  decision: Decision,
  decision_source: DecisionSource,
  probability: number | null,
  cost_breakdown: CostBreakdown | null,
  reasons: ReasonCode[],
  rule_fired: string | null,
  degraded: boolean,
  t0: number,
  mandate: ScoreResponse["mandate"],
): ScoreResponse {
  const prediction_id = `pred-${Math.random().toString(16).slice(2, 10)}-${Date.now()
    .toString(16)
    .slice(-6)}`;
  const audit_id = `aud_${Math.random().toString(36).slice(2, 12)}`;
  return {
    prediction_id,
    risk_score: probability === null ? null : Math.round(probability * 1000) / 10,
    probability: probability === null ? null : Number(probability.toFixed(4)),
    decision,
    decision_source,
    cost_breakdown,
    explanation: reasons,
    rule_fired,
    degraded,
    policy_hint: decision,
    model_version: "v2.1",
    latency_ms: Date.now() - t0 + 11,
    case_id: decision === "REVIEW" ? `case_${prediction_id.slice(-6)}` : null,
    mandate,
    audit_trail_url: `/audit/${audit_id}`,
    timestamp: new Date().toISOString(),
    gate_thresholds: {
      policy: "cost_optimal_bmr",
      weights: {
        c_fp: 50,
        c_fn: 600,
        c_otp: 5,
        c_block: 1000,
        otp_effectiveness: 0.82,
      },
      legacy_accept_t: 0.15,
      legacy_reject_t: 0.6,
    },
  };
}
