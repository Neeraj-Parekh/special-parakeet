// Story scenes — the "one order, three views" demo data.
//
// DESIGN RULES (text congestion discipline):
//   Shopper copy   → consumer words only. No risk, no fraud, no numbers
//                    except prices. Warm, short lines.
//   Merchant copy  → money + actions. One-line reasons, plain English.
//   Platform copy  → network aggregates. Trust words, no per-customer
//                    detail without consent.
//
// HONESTY: every scene's VERDICT comes from the live /api/risk/score API
// on this deployment (mock fallback included). The scene inputs are tuned
// so the verdict lands in the intended class under the demo scorer. The
// fictional brands (UrbanKart / PayGrid) and ambient network numbers are
// illustrative — the disclaimer says exactly that.

import type { OrderInput, ReasonCode } from "@/lib/mock-data";

// ---------------------------------------------------------------------------
// Products & people
// ---------------------------------------------------------------------------

export interface StoryProduct {
  name: string;
  price: number;
  blurb: string;
  emoji: string;
  tile: string; // gradient classes for the product tile
}

export interface StoryAddress {
  name: string;
  phoneMasked: string;
  line1: string;
  city: string;
  eta: string;
}

// ---------------------------------------------------------------------------
// Scenes
// ---------------------------------------------------------------------------

export type SceneId = "safe" | "verify" | "recover" | "ring";

export interface StoryScene {
  id: SceneId;
  /** Discreet picker label. */
  label: string;
  /** One-line director's note (shown only in the topbar tooltip). */
  note: string;
  product: StoryProduct;
  address: StoryAddress;
  order: OrderInput;
  /** What the tuned inputs are expected to produce (display hint only). */
  expected: "ACCEPT" | "REVIEW" | "REJECT";
}

export const SCENES: StoryScene[] = [
  {
    id: "safe",
    label: "1 · Smooth order",
    note: "Trusted customer → instant confirmation, all three panes stay calm.",
    expected: "ACCEPT",
    product: {
      name: "Cotton Kurta Set",
      price: 2399,
      blurb: "Breathable summer cotton",
      emoji: "🧵",
      tile: "from-rose-100 to-amber-100",
    },
    address: {
      name: "Priya Sharma",
      phoneMasked: "+91 98••• •••45",
      line1: "Flat 12B, Lakeview Residency",
      city: "Bengaluru",
      eta: "Thursday",
    },
    order: {
      order_id: "ORD-STORY-SAFE",
      amount_inr: 2399,
      category: "Fashion",
      customer_id: "CUST-STORY-SAFE",
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
    id: "verify",
    label: "2 · One quick check",
    note: "Grey-zone order → shopper gets a friendly OTP step, merchant waits.",
    expected: "REVIEW",
    product: {
      name: "Street Sneakers",
      price: 8399,
      blurb: "Everyday comfort, all-day wear",
      emoji: "👟",
      tile: "from-sky-100 to-indigo-100",
    },
    address: {
      name: "Aman Verma",
      phoneMasked: "+91 88••• •••20",
      line1: "Row house 4, Sector 21",
      city: "Indore",
      eta: "Saturday",
    },
    order: {
      order_id: "ORD-STORY-VERIFY",
      amount_inr: 8399,
      category: "Footwear",
      customer_id: "CUST-STORY-VFY",
      address_quality: "vague",
      city_tier: "tier_2",
      payment_method: "COD",
      prior_orders: 5,
      prior_returns: 3,
      items: 1,
      order_hour: 19,
      device: "Web",
    },
  },
  {
    id: "recover",
    label: "3 · COD unavailable",
    note: "Blocked COD → soft decline → pay online + verify → sale recovered.",
    expected: "REJECT",
    product: {
      name: "Galaxy Smartphone",
      price: 33999,
      blurb: "5G · 128 GB · factory warranty",
      emoji: "📱",
      tile: "from-violet-100 to-fuchsia-100",
    },
    address: {
      name: "Rohit Kumar",
      phoneMasked: "+91 73••• •••08",
      line1: "Near bus stand, post office road",
      city: "Chhindwara",
      eta: "Tuesday",
    },
    order: {
      order_id: "ORD-STORY-RECOVER",
      amount_inr: 33999,
      category: "Mobiles",
      customer_id: "CUST-STORY-REC",
      address_quality: "vague",
      city_tier: "tier_3",
      payment_method: "COD",
      prior_orders: 5,
      prior_returns: 4,
      items: 1,
      order_hour: 21,
      device: "Android App",
    },
  },
  {
    id: "ring",
    label: "4 · Fraud ring",
    note: "3 orders, 1 device → merchant feed blocks all, platform raises the ring.",
    expected: "REJECT",
    product: {
      name: "Wireless Earbuds",
      price: 5199,
      blurb: "Noise-cancelling · 30h battery",
      emoji: "🎧",
      tile: "from-teal-100 to-cyan-100",
    },
    address: {
      name: "Same device, 3 identities",
      phoneMasked: "+91 ••••• •••77",
      line1: "Three accounts · one device",
      city: "Multiple cities",
      eta: "—",
    },
    order: {
      order_id: "ORD-STORY-RING-1",
      amount_inr: 5199,
      category: "Electronics",
      customer_id: "CUST-STORY-R1",
      address_quality: "partial",
      city_tier: "tier_2",
      payment_method: "COD",
      prior_orders: 3,
      prior_returns: 3,
      items: 1,
      order_hour: 22,
      device: "D-SHR-042",
    },
  },
];

/** The burst orders for the ring scene — same device, 3 identities. */
export const RING_ORDERS: OrderInput[] = [
  {
    order_id: "ORD-STORY-RING-1",
    amount_inr: 5199,
    category: "Electronics",
    customer_id: "CUST-STORY-R1",
    address_quality: "partial",
    city_tier: "tier_2",
    payment_method: "COD",
    prior_orders: 3,
    prior_returns: 3,
    items: 1,
    order_hour: 22,
    device: "D-SHR-042",
  },
  {
    order_id: "ORD-STORY-RING-2",
    amount_inr: 6099,
    category: "Electronics",
    customer_id: "CUST-STORY-R2",
    address_quality: "vague",
    city_tier: "tier_3",
    payment_method: "COD",
    prior_orders: 4,
    prior_returns: 4,
    items: 1,
    order_hour: 22,
    device: "D-SHR-042",
  },
  {
    order_id: "ORD-STORY-RING-3",
    amount_inr: 4699,
    category: "Mobiles",
    customer_id: "CUST-STORY-R3",
    address_quality: "vague",
    city_tier: "tier_3",
    payment_method: "COD",
    prior_orders: 5,
    prior_returns: 4,
    items: 1,
    order_hour: 23,
    device: "D-SHR-042",
  },
];

// ---------------------------------------------------------------------------
// Plain-English reason mapping (merchant "Why?" — NEVER raw deltas)
// ---------------------------------------------------------------------------

export interface PlainReason {
  text: string;
  tone: "risk" | "protect";
}

export function plainReasons(reasons: ReasonCode[], amountInr: number): PlainReason[] {
  const out: PlainReason[] = [];
  for (const r of reasons) {
    const v = r.value;
    switch (r.feature) {
      case "payment_method":
        out.push(
          v === "COD"
            ? { text: "Cash on delivery", tone: "risk" }
            : { text: "Paid online", tone: "protect" },
        );
        break;
      case "address_quality":
        if (v === "vague") out.push({ text: "Incomplete address", tone: "risk" });
        else if (v === "partial") out.push({ text: "Partially filled address", tone: "risk" });
        else out.push({ text: "Complete address", tone: "protect" });
        break;
      case "city_tier":
        if (v === "tier_3") out.push({ text: "Remote delivery area", tone: "risk" });
        else if (v === "tier_2") out.push({ text: "Tier-2 town", tone: "risk" });
        else out.push({ text: "Metro delivery", tone: "protect" });
        break;
      case "prior_orders":
        if (Number(v) === 0) out.push({ text: "First order from this customer", tone: "risk" });
        else if (Number(v) >= 10) out.push({ text: "Regular customer", tone: "protect" });
        break;
      case "prior_returns": {
        const n = Number(v);
        if (n > 0) out.push({ text: `Returned ${n} of their last orders`, tone: "risk" });
        break;
      }
      case "amount_inr":
        if (amountInr > 30000)
          out.push({ text: `₹${amountInr.toLocaleString("en-IN")} order value`, tone: "risk" });
        else if (amountInr < 3000) out.push({ text: "Small order value", tone: "protect" });
        break;
      case "order_hour":
        if (Number(r.delta_prob) > 0) out.push({ text: "Late-night order", tone: "risk" });
        break;
    }
  }
  // Risk factors first, protective last; cap at 4 for zero congestion.
  return [
    ...out.filter((x) => x.tone === "risk"),
    ...out.filter((x) => x.tone === "protect"),
  ].slice(0, 4);
}

// ---------------------------------------------------------------------------
// Ambient (fictional) feed content — the "already alive" texture
// ---------------------------------------------------------------------------

export interface AmbientOrder {
  orderId: string;
  amount: number;
  status: "ok" | "check" | "blocked";
  line: string;
  ago: string;
}

export const AMBIENT_MERCHANT_ORDERS: AmbientOrder[] = [
  { orderId: "URB-1042", amount: 1499, status: "ok", line: "Prepaid · regular customer", ago: "2m ago" },
  { orderId: "URB-1041", amount: 649, status: "ok", line: "COD · complete address", ago: "9m ago" },
  { orderId: "URB-1040", amount: 2999, status: "check", line: "COD · first order · partial address", ago: "26m ago" },
];

export interface AmbientNetworkRow {
  merchant: string;
  amount: number;
  outcome: "ok" | "gated" | "blocked";
  ago: string;
}

export const AMBIENT_NETWORK_ROWS: AmbientNetworkRow[] = [
  { merchant: "StyleHub", amount: 4299, outcome: "ok", ago: "11:44" },
  { merchant: "TrendKart", amount: 1299, outcome: "gated", ago: "11:43" },
  { merchant: "StyleHub", amount: 2999, outcome: "ok", ago: "11:41" },
  { merchant: "VillageCart", amount: 7599, outcome: "blocked", ago: "11:39" },
];

export interface PlatformMerchant {
  name: string;
  consent: "shared" | "locked";
  note: string;
}

export const PLATFORM_MERCHANTS: PlatformMerchant[] = [
  { name: "TrendKart", consent: "locked", note: "Aggregate view only" },
  { name: "StyleHub", consent: "shared", note: "Audit key shared" },
  { name: "VillageCart", consent: "locked", note: "Aggregate view only" },
];

// ---------------------------------------------------------------------------
// Fictional network baselines (illustrative, covered by the disclaimer)
// ---------------------------------------------------------------------------

export const NETWORK_BASE = {
  ordersToday: 2341,
  rtoPreventedBase: 412000, // ₹ — real session blockage gets added live
  fraudRings: 0,
};

export const MERCHANT_SAVED_WEEK = 118200; // ₹ — ties to the console demo number
export const RING_DEVICE = "D-SHR-042";
