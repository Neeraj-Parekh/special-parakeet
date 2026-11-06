// GET /api/v1/integrations/shiprocket/validate-pincode/[pincode]
//
// Returns the serviceability of an Indian PIN code via the Shiprocket
// API. In the hackathon the response is mock data derived from a
// stable hash of the pincode (see src/lib/integrations/shiprocket.ts).
//
// In production, set `SHIPROCKET_TOKEN` and the lib will fetch from
// the real Shiprocket serviceability endpoint — the route handler
// shape doesn't change.
//
// Use case: a COD checkout flow calls this BEFORE rendering the COD
// option. If `cod_available:false`, the UI hides COD and forces
// prepaid — saving the merchant an RTO loss on a non-serviceable
// pincode.

import { NextRequest, NextResponse } from "next/server";
import { validatePincode } from "@/lib/integrations/shiprocket";

export const runtime = "nodejs";

/** GET — validate serviceability of a pincode. */
export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ pincode: string }> },
): Promise<NextResponse> {
  const { pincode } = await ctx.params;
  try {
    const result = await validatePincode(pincode);
    const headers: Record<string, string> = { "Cache-Control": "no-store" };
    if (result.mock) {
      headers["X-Mock-Mode"] = "true";
    }
    return NextResponse.json(result, { status: 200, headers });
  } catch (err) {
    return NextResponse.json(
      {
        detail: err instanceof Error ? err.message : "invalid pincode",
      },
      { status: 422 },
    );
  }
}
