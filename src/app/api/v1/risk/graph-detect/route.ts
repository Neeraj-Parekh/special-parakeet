// POST /api/v1/risk/graph-detect
//   Body: { customer_id: string }
//   Returns: { fraud_ring_detected, connected_accounts, shared_*, ... }
//
// GET  /api/v1/risk/graph-detect
//   Returns: { rings: RingDetectionResult[] } — every ring in the roster.
//
// Both require scope "score" (fraud detection is a scoring-adjacent
// capability) OR "admin".

import { NextRequest, NextResponse } from "next/server";
import { withScope } from "@/lib/auth/guard";
import { detectRing, allRings } from "@/lib/graph/detector";
import { parseJsonBody, jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return withScope(req, ["score", "admin"], async () => {
    const body = await parseJsonBody<{ customer_id?: string }>(req);
    if (!body?.customer_id) {
      return NextResponse.json(
        { detail: "customer_id is required" },
        { status: 422 },
      );
    }
    const result = detectRing(body.customer_id);
    return jsonOk(result);
  }) as NextResponse;
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  return withScope(req, ["score", "admin"], async () => {
    const url = new URL(req.url);
    const customerId = url.searchParams.get("customer_id");
    if (customerId) {
      return jsonOk(detectRing(customerId));
    }
    return jsonOk({ rings: allRings(), total: allRings().length });
  }) as NextResponse;
}
