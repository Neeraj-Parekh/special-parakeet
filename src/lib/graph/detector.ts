// G3 — Graph fraud-ring detection.
//
// Fraud rings share identity attributes across accounts: same device,
// same phone, same address hash, same payment instrument. We build an
// adjacency graph where two customers are connected iff they share at
// least one of these attributes, then detect connected components of
// size >= 3 (the heuristic threshold for "ring").
//
// Production: NetworkX adjacency list + PageRank anomaly score +
// community detection (Louvain). For the hackathon we ship the same
// adjacency query in TypeScript over an in-memory roster seeded with a
// known 3-customer ring. The seam — detectRing(customerId) — is
// identical; only the backing store swaps.
//
// Seed design: 8 customers. Customers C1/C2/C3 share a device + phone
// (a synthetic fraud ring). C4 shares an address with C5 (couple,
// not a ring — 2 nodes). C6/C7/C8 are isolated. This gives the demo a
// true positive (C1→ring of 3) and a true negative (C4→pair, not a ring).

export interface CustomerNode {
  customer_id: string;
  device_id: string;
  phone_hash: string;
  address_hash: string;
  payment_instrument_hash: string;
  prior_returns: number;
  risk_score: number;
}

export interface RingDetectionResult {
  customer_id: string;
  fraud_ring_detected: boolean;
  ring_size: number;
  connected_accounts: Array<{
    customer_id: string;
    shared_attributes: string[];
    risk_score: number;
  }>;
  shared_devices: string[];
  shared_phones: string[];
  shared_addresses: string[];
  shared_payment_instruments: string[];
  ring_confidence: number; // 0..1 heuristic
  detection_method: string;
}

// In-memory customer roster. Production swaps to a Postgres
// `customer_fingerprints` table + a GIN index on the hash columns.
const ROSTER: CustomerNode[] = [
  // Ring: C1, C2, C3 share device D-EVIL-1 + phone P-EVIL-1.
  {
    customer_id: "CUST-RING-001",
    device_id: "D-EVIL-1",
    phone_hash: "P-EVIL-1",
    address_hash: "A-RING-1",
    payment_instrument_hash: "PA-001",
    prior_returns: 4,
    risk_score: 0.78,
  },
  {
    customer_id: "CUST-RING-002",
    device_id: "D-EVIL-1",
    phone_hash: "P-EVIL-1",
    address_hash: "A-RING-2",
    payment_instrument_hash: "PA-002",
    prior_returns: 3,
    risk_score: 0.71,
  },
  {
    customer_id: "CUST-RING-003",
    device_id: "D-EVIL-2",
    phone_hash: "P-EVIL-2",
    address_hash: "A-RING-3",
    payment_instrument_hash: "PA-003",
    prior_returns: 5,
    risk_score: 0.82,
  },
  // Bridge: C3 also shares address with C2 — closes the triangle.
  // (handled in the address_hash edges below via the roster)
  // Couple: C4 + C5 share an address only.
  {
    customer_id: "CUST-COUPLE-004",
    device_id: "D-004",
    phone_hash: "P-004",
    address_hash: "A-COUPLE",
    payment_instrument_hash: "PA-004",
    prior_returns: 0,
    risk_score: 0.12,
  },
  {
    customer_id: "CUST-COUPLE-005",
    device_id: "D-005",
    phone_hash: "P-005",
    address_hash: "A-COUPLE",
    payment_instrument_hash: "PA-005",
    prior_returns: 1,
    risk_score: 0.18,
  },
  // Isolated.
  {
    customer_id: "CUST-REP-7782",
    device_id: "D-7782",
    phone_hash: "P-7782",
    address_hash: "A-7782",
    payment_instrument_hash: "PA-7782",
    prior_returns: 0,
    risk_score: 0.08,
  },
  {
    customer_id: "CUST-NEW-0001",
    device_id: "D-0001",
    phone_hash: "P-0001",
    address_hash: "A-0001",
    payment_instrument_hash: "PA-0001",
    prior_returns: 0,
    risk_score: 0.65,
  },
  {
    customer_id: "CUST-RET-3022",
    device_id: "D-3022",
    phone_hash: "P-3022",
    address_hash: "A-3022",
    payment_instrument_hash: "PA-3022",
    prior_returns: 3,
    risk_score: 0.42,
  },
];

// Edge closure for the ring: C2 + C3 share address A-RING-BRIDGE.
// We patch C3's address_hash so the triangle closes.
ROSTER[2].address_hash = "A-RING-1"; // C3 now shares address with C1

const BY_ID = new Map(ROSTER.map((c) => [c.customer_id, c]));

const SHARED_ATTRS: Array<keyof CustomerNode> = [
  "device_id",
  "phone_hash",
  "address_hash",
  "payment_instrument_hash",
];

/** Find all customers that share at least one fingerprint attribute
 *  with the given customer. Returns the adjacency list. */
export function neighbors(
  customerId: string,
  roster: CustomerNode[] = ROSTER,
): Map<string, Set<string>> {
  const seed = BY_ID.get(customerId);
  if (!seed) return new Map();
  const out = new Map<string, Set<string>>();
  for (const other of roster) {
    if (other.customer_id === customerId) continue;
    const shared = new Set<string>();
    for (const attr of SHARED_ATTRS) {
      const a = seed[attr];
      const b = other[attr];
      if (a && b && a === b) {
        shared.add(attr);
      }
    }
    if (shared.size > 0) {
      out.set(other.customer_id, shared);
    }
  }
  return out;
}

/** BFS the connected component containing the seed customer. */
export function connectedComponent(
  customerId: string,
  roster: CustomerNode[] = ROSTER,
): Set<string> {
  const visited = new Set<string>();
  const queue = [customerId];
  visited.add(customerId);
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const [neighbor] of neighbors(cur, roster)) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        queue.push(neighbor);
      }
    }
  }
  return visited;
}

/**
 * Detect a fraud ring around a customer. Threshold: component size
 * >= 3 → ring. Confidence is a heuristic combining (a) component
 * size, (b) average risk score of the component, (c) attribute overlap
 * density (how many distinct attributes are shared).
 */
export function detectRing(customerId: string): RingDetectionResult {
  const seed = BY_ID.get(customerId);
  if (!seed) {
    return {
      customer_id: customerId,
      fraud_ring_detected: false,
      ring_size: 0,
      connected_accounts: [],
      shared_devices: [],
      shared_phones: [],
      shared_addresses: [],
      shared_payment_instruments: [],
      ring_confidence: 0,
      detection_method: "shared-attribute-adjacency-BFS",
    };
  }
  const component = connectedComponent(customerId);
  const ringSize = component.size;
  const ringDetected = ringSize >= 3;

  // Build the connected_accounts payload with per-edge shared attrs.
  const connectedAccounts: RingDetectionResult["connected_accounts"] = [];
  const sharedDevices = new Set<string>();
  const sharedPhones = new Set<string>();
  const sharedAddresses = new Set<string>();
  const sharedPis = new Set<string>();
  let attrOverlapCount = 0;
  for (const member of component) {
    if (member === customerId) continue;
    const node = BY_ID.get(member)!;
    const shared: string[] = [];
    if (node.device_id === seed.device_id) {
      shared.push("device_id");
      sharedDevices.add(node.device_id);
      attrOverlapCount++;
    }
    if (node.phone_hash === seed.phone_hash) {
      shared.push("phone_hash");
      sharedPhones.add(node.phone_hash);
      attrOverlapCount++;
    }
    if (node.address_hash === seed.address_hash) {
      shared.push("address_hash");
      sharedAddresses.add(node.address_hash);
      attrOverlapCount++;
    }
    if (node.payment_instrument_hash === seed.payment_instrument_hash) {
      shared.push("payment_instrument_hash");
      sharedPis.add(node.payment_instrument_hash);
      attrOverlapCount++;
    }
    connectedAccounts.push({
      customer_id: member,
      shared_attributes: shared,
      risk_score: node.risk_score,
    });
  }
  // Compute the component's mean risk score.
  const componentScores = [...component].map((id) => BY_ID.get(id)!.risk_score);
  const meanScore =
    componentScores.reduce((a, b) => a + b, 0) / (componentScores.length || 1);
  // Confidence heuristic: size factor + risk factor + overlap factor.
  const sizeFactor = Math.min(1, (ringSize - 2) / 3); // 3→0, 4→0.33, 5→0.66, 6→1
  const riskFactor = Math.min(1, meanScore);
  const overlapFactor = Math.min(1, attrOverlapCount / 6); // 6 = 3 members × 2 attrs
  const confidence = ringDetected
    ? Math.round((0.4 * sizeFactor + 0.4 * riskFactor + 0.2 * overlapFactor) * 100) / 100
    : 0;

  return {
    customer_id: customerId,
    fraud_ring_detected: ringDetected,
    ring_size: ringSize,
    connected_accounts: connectedAccounts,
    shared_devices: [...sharedDevices],
    shared_phones: [...sharedPhones],
    shared_addresses: [...sharedAddresses],
    shared_payment_instruments: [...sharedPis],
    ring_confidence: confidence,
    detection_method: "shared-attribute-adjacency-BFS",
  };
}

/** List all rings in the roster (for the /graph-detect GET all mode). */
export function allRings(): RingDetectionResult[] {
  const seen = new Set<string>();
  const out: RingDetectionResult[] = [];
  for (const c of ROSTER) {
    if (seen.has(c.customer_id)) continue;
    const r = detectRing(c.customer_id);
    if (r.fraud_ring_detected) {
      out.push(r);
      for (const m of r.connected_accounts) seen.add(m.customer_id);
      seen.add(c.customer_id);
    }
  }
  return out;
}
