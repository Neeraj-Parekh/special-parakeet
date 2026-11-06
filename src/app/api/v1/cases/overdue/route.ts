// GET /api/v1/cases/overdue — cases past their SLA, not yet resolved.
//
// Drives the analyst "overdue queue" view. Sorted by due_at ascending
// (most overdue first). Triggers an SLA sweep before listing so the
// sla_breached flag is fresh.

import { NextRequest, NextResponse } from "next/server";
import { withScope } from "@/lib/auth/guard";
import { overdueCases, sweepSla } from "@/lib/cases/service";
import { jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<NextResponse> {
  return withScope(req, ["cases:write", "audit:read"], async () => {
    const url = new URL(req.url);
    const limit = Number(url.searchParams.get("limit") ?? 100);
    await sweepSla();
    const rows = await overdueCases(limit);
    return jsonOk({
      overdue: rows,
      total: rows.length,
      sla_policy: { high: "4h", medium: "24h", low: "72h" },
    });
  }) as NextResponse;
}
