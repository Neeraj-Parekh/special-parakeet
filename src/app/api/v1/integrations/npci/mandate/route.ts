// POST /api/v1/integrations/npci/mandate
//
// Creates a UPI Circle mandate via NPCI, enforcing OC-201B caps
// (₹50,000/mandate, ₹5,000/txn, 24h cooling, 5-device, 180-day TTL,
// monthly frequency only). In the hackathon the response is mock
// data; in production set `NPCI_CLIENT_ID` + `NPCI_CLIENT_SECRET`
// + `NPCI_SIGNING_KEY` and the lib will POST to the real NPCI API.
//
// OC-201B reference (mirrored from the Python backend at
// `upload/RTO_Trust_Layer_FULL/src/api/mandates.py:699-705`):
//   • amount_cap_inr   ≤ ₹50,000 per mandate
//   • frequency        = 'monthly' (no daily/weekly allowed)
//   • per_txn_cap_inr  ≤ ₹5,000 (per-transaction hard cap)
//   • cooling_period_h = 24h after the first txn
//   • max_devices      = 5
//   • mandate_ttl_days = 180 (auto-revoke after 6 months)
//
// Use case: when the score path returns REVIEW on a high-value COD
// order, the Trust Layer offers the merchant an "OTP-gate via UPI
// Circle" alternative — the customer registers a ₹5,000 mandate,
// pays the first ₹5,000 via UPI, and the merchant ships the order
// knowing the customer is financially committed.

import { NextRequest, NextResponse } from "next/server";
import { createMandate } from "@/lib/integrations/npci";
import { parseJsonBody } from "@/lib/api-proxy";

export const runtime = "nodejs";

interface MandateRequest {
  customer_id: string;
  amount_cap_inr: number;
  frequency: "monthly";
  purpose?: string;
}

/** POST — create a UPI Circle mandate with OC-201B cap enforcement. */
export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await parseJsonBody<MandateRequest>(req);
  if (!body || !body.customer_id || typeof body.amount_cap_inr !== "number") {
    return NextResponse.json(
      { detail: "invalid request — customer_id and amount_cap_inr required" },
      { status: 422 },
    );
  }
  try {
    const result = await createMandate({
      customer_id: body.customer_id,
      amount_cap_inr: body.amount_cap_inr,
      frequency: body.frequency,
      purpose: body.purpose,
    });
    const headers: Record<string, string> = { "Cache-Control": "no-store" };
    if (result.mock) {
      headers["X-Mock-Mode"] = "true";
    }
    return NextResponse.json(result, { status: 200, headers });
  } catch (err) {
    // OC-201B violations throw with a descriptive message — surface
    // them as 422 so the caller can show the merchant why the cap
    // was breached.
    return NextResponse.json(
      {
        detail: err instanceof Error ? err.message : "mandate creation failed",
      },
      { status: 422 },
    );
  }
}
