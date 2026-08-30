// POST /api/v1/integrations/delhivery/track
//
// Returns the tracking milestones for a Delhivery AWB. In the
// hackathon the response is mock data derived from a stable hash of
// the AWB (see src/lib/integrations/delhivery.ts).
//
// In production, set `DELHIVERY_TOKEN` and the lib will fetch from
// the real Delhivery tracking endpoint — the route handler shape
// doesn't change.
//
// Use case: a merchant clicks "Track shipment" on the order detail
// page. The route returns the milestone history; the UI renders a
// timeline. If `current_status === 'delivered'`, the order's RTO
// risk window closes — the RTO Trust Layer's audit trail logs the
// `delivery_confirmed` milestone.

import { NextRequest, NextResponse } from "next/server";
import { track } from "@/lib/integrations/delhivery";
import { parseJsonBody } from "@/lib/api-proxy";

export const runtime = "nodejs";

interface TrackRequest {
  awb: string;
}

/** POST — fetch tracking milestones for an AWB. */
export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await parseJsonBody<TrackRequest>(req);
  if (!body || !body.awb) {
    return NextResponse.json(
      { detail: "invalid request — awb is required" },
      { status: 422 },
    );
  }
  try {
    const result = await track(body.awb);
    const headers: Record<string, string> = { "Cache-Control": "no-store" };
    if (result.mock) {
      headers["X-Mock-Mode"] = "true";
    }
    return NextResponse.json(result, { status: 200, headers });
  } catch (err) {
    return NextResponse.json(
      {
        detail: err instanceof Error ? err.message : "invalid AWB",
      },
      { status: 422 },
    );
  }
}
