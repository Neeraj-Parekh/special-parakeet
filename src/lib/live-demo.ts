// Live-demo order stream — one simulated "day" of COD orders.
//
// HONESTY CONTRACT (the demo's whole point):
// - The ORDERS below are synthetic and labeled SIMULATION in the UI.
// - Every one is scored by the REAL /api/risk/score endpoint at run time —
//   the feed, probability, cost breakdown, latency and verdict are actual
//   API output, never scripted. On a deployment without the Python
//   scorer, the API's mock fallback answers and is badged (X-Mock-Mode).
// - Profiles are calibrated so the story lands under BOTH scorers
//   (mock Bahnsen-BMR and the trained model): loyal prepaid → ACCEPT,
//   ordinary COD → REVIEW (the OTP gate), engineered fraud → REJECT.
//
// DETERMINISTIC: a fixed profile list (no RNG) means every run tells the
// same story — safe to record the demo on the first take.
//
// Story arc (25 orders):
//   1-5   calm daytime flow (prepaid + healthy COD)
//   6     first fraud block (100% returner, night order)
//   7-13  mixed flow (incl. rule-driven REVIEWs)
//   14-15 hard blocks (repeat-returner ₹34K, RULE-001 ₹52K COD)
//   16-22 recovery flow
//   23-25 shared-device ring burst (3 customers, one device)

import type { OrderInput } from "@/lib/mock-data";

export interface LiveOrder extends OrderInput {
  /** Risk-context note rendered as a chip in the live feed. */
  note?: string;
}

type Address = OrderInput["address_quality"];
type Tier = OrderInput["city_tier"];
type Pay = OrderInput["payment_method"];

interface Profile {
  pay: Pay;
  amt: number;
  addr: Address;
  tier: Tier;
  priors: number;
  returns: number;
  hour: number;
  items: number;
  cat: string;
  device?: string;
  note?: string;
}

/** The fraud-ring's shared handset — 3 "customers", one device. */
const RING_DEVICE = "D-SHR-042";

const PROFILES: Profile[] = [
  // --- calm daytime flow ---------------------------------------------------
  { pay: "Prepaid", amt: 1899, addr: "complete", tier: "tier_1", priors: 14, returns: 0, hour: 11, items: 1, cat: "Fashion" },
  { pay: "COD", amt: 1249, addr: "complete", tier: "tier_1", priors: 11, returns: 0, hour: 12, items: 2, cat: "Beauty" },
  { pay: "Prepaid", amt: 3499, addr: "complete", tier: "tier_2", priors: 6, returns: 0, hour: 13, items: 1, cat: "Home & Kitchen" },
  { pay: "COD", amt: 899, addr: "complete", tier: "tier_2", priors: 9, returns: 0, hour: 13, items: 3, cat: "Grocery" },
  { pay: "Prepaid", amt: 2499, addr: "complete", tier: "tier_1", priors: 18, returns: 0, hour: 14, items: 1, cat: "Electronics" },

  // --- first fraud block ---------------------------------------------------
  { pay: "COD", amt: 6800, addr: "partial", tier: "tier_3", priors: 1, returns: 1, hour: 23, items: 1, cat: "Fashion", note: "100% returner · night order" },

  // --- mixed flow ----------------------------------------------------------
  { pay: "Prepaid", amt: 1599, addr: "complete", tier: "tier_1", priors: 22, returns: 0, hour: 15, items: 1, cat: "Books" },
  { pay: "COD", amt: 2199, addr: "partial", tier: "tier_2", priors: 8, returns: 1, hour: 16, items: 2, cat: "Footwear" },
  { pay: "Prepaid", amt: 4299, addr: "partial", tier: "tier_2", priors: 4, returns: 1, hour: 16, items: 1, cat: "Electronics" },
  { pay: "Prepaid", amt: 799, addr: "vague", tier: "tier_2", priors: 2, returns: 0, hour: 17, items: 1, cat: "Fashion", note: "vague address · review rule" },
  { pay: "COD", amt: 1649, addr: "complete", tier: "tier_1", priors: 15, returns: 0, hour: 17, items: 2, cat: "Home & Kitchen" },
  { pay: "COD", amt: 3499, addr: "complete", tier: "tier_2", priors: 7, returns: 0, hour: 18, items: 1, cat: "Mobiles" },
  { pay: "COD", amt: 2999, addr: "complete", tier: "tier_1", priors: 13, returns: 0, hour: 18, items: 1, cat: "Fashion" },

  // --- hard blocks ---------------------------------------------------------
  { pay: "COD", amt: 34000, addr: "vague", tier: "tier_3", priors: 5, returns: 4, hour: 20, items: 1, cat: "Mobiles", note: "repeat returner · ₹34K COD" },
  { pay: "COD", amt: 52000, addr: "complete", tier: "tier_1", priors: 0, returns: 0, hour: 14, items: 1, cat: "Electronics", note: "RULE-001 · COD > ₹50K new customer" },

  // --- recovery flow -------------------------------------------------------
  { pay: "Prepaid", amt: 1249, addr: "complete", tier: "tier_1", priors: 30, returns: 1, hour: 19, items: 1, cat: "Beauty" },
  { pay: "COD", amt: 999, addr: "complete", tier: "tier_2", priors: 12, returns: 0, hour: 19, items: 3, cat: "Grocery" },
  { pay: "Prepaid", amt: 5499, addr: "complete", tier: "tier_1", priors: 10, returns: 0, hour: 20, items: 1, cat: "Electronics" },
  { pay: "COD", amt: 9400, addr: "vague", tier: "tier_3", priors: 3, returns: 2, hour: 21, items: 1, cat: "Fashion", note: "2 of 3 orders returned" },
  { pay: "COD", amt: 1899, addr: "complete", tier: "tier_2", priors: 16, returns: 1, hour: 20, items: 2, cat: "Home & Kitchen" },
  { pay: "Prepaid", amt: 2999, addr: "complete", tier: "tier_2", priors: 5, returns: 0, hour: 21, items: 1, cat: "Books" },
  { pay: "COD", amt: 749, addr: "complete", tier: "tier_1", priors: 20, returns: 0, hour: 21, items: 4, cat: "Grocery" },

  // --- shared-device ring burst -------------------------------------------
  { pay: "COD", amt: 5200, addr: "partial", tier: "tier_2", priors: 3, returns: 3, hour: 22, items: 1, cat: "Mobiles", device: RING_DEVICE, note: `shared device ${RING_DEVICE}` },
  { pay: "COD", amt: 6100, addr: "vague", tier: "tier_3", priors: 4, returns: 4, hour: 22, items: 1, cat: "Electronics", device: RING_DEVICE, note: `shared device ${RING_DEVICE}` },
  { pay: "COD", amt: 4700, addr: "vague", tier: "tier_3", priors: 5, returns: 4, hour: 23, items: 1, cat: "Electronics", device: RING_DEVICE, note: `shared device ${RING_DEVICE}` },
];

/**
 * The demo stream. Fixed order, fixed values — the same story every run.
 * Order/customer IDs are sequential (ORD-LIVE-0001…, CUST-LIVE-0042…).
 */
export function generateLiveOrders(): LiveOrder[] {
  return PROFILES.map((p, i) => ({
    order_id: `ORD-LIVE-${String(i + 1).padStart(4, "0")}`,
    amount_inr: p.amt,
    category: p.cat,
    customer_id: `CUST-LIVE-${String(41 + i).padStart(4, "0")}`,
    address_quality: p.addr,
    city_tier: p.tier,
    payment_method: p.pay,
    prior_orders: p.priors,
    prior_returns: p.returns,
    items: p.items,
    order_hour: p.hour,
    device: p.device ?? (p.pay === "Prepaid" ? "iOS App" : "Android App"),
    note: p.note,
  }));
}

/** Total orders in the stream (progress denominators). */
export const LIVE_STREAM_TOTAL = PROFILES.length;
