// GET /api/v1/features/[customer_id] — the 79-dim feature vector
//                            + timestamp + model_version.
// GET /api/v1/features/_meta   — store stats (entries, model version, TTL).
//
// Requires scope "score" or "admin" — feature vectors are model
// inputs; leaking them to a scorer-only caller is fine, to anyone
// else it's a data-exfiltration surface.

import { NextRequest, NextResponse } from "next/server";
import { withScope } from "@/lib/auth/guard";
import { getFeatures, storeStats } from "@/lib/feature-store/store";
import { jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ customer_id: string }> },
): Promise<NextResponse> {
  const { customer_id } = await params;
  // _meta is a reserved pseudo-customer for store stats.
  if (customer_id === "_meta") {
    return withScope(req, ["score", "admin"], async () => {
      return jsonOk(storeStats());
    }) as NextResponse;
  }
  return withScope(req, ["score", "admin"], async () => {
    const vec = getFeatures(customer_id);
    return jsonOk(vec);
  }) as NextResponse;
}
