// GET /api/v1/cases/metrics — auto-resolution rate, avg resolution
// time, SLA breach rate, by-priority breakdown.
//
// The metrics a senior engineer at Sardine/Unit21 will look for:
//   - auto_resolution_rate: fraction of cases the model resolved
//     correctly (analyst rubber-stamped within SLA)
//   - avg_resolution_time_hours: mean time from open → resolved
//   - sla_breached_active: how many open cases are already past SLA
//   - by_priority: queue depth + breach count per priority tier

import { NextRequest, NextResponse } from "next/server";
import { withScope } from "@/lib/auth/guard";
import { metrics, sweepSla } from "@/lib/cases/service";
import { jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  return withScope(req, ["cases:write", "audit:read"], async () => {
    await sweepSla();
    const m = await metrics();
    return jsonOk(m);
  }) as NextResponse;
}
