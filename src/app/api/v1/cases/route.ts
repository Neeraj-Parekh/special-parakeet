// POST /api/v1/cases — open a case from a REVIEW verdict.
// GET  /api/v1/cases — list cases (filter by status/priority/assignee/customer_id).
//
// Both require scope "cases:write" (POST) or "cases:write" (GET; an
// analyst reading their queue is a cases:write scope per our model —
// the auditor-only scope is audit:read).

import { NextRequest, NextResponse } from "next/server";
import { withScope } from "@/lib/auth/guard";
import {
  openCase,
  listCases,
  sweepSla,
  type CasePriority,
  type CaseStatus,
} from "@/lib/cases/service";
import { parseJsonBody, jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<NextResponse> {
  return withScope(req, "cases:write", async () => {
    const body = await parseJsonBody<{
      prediction_id: string;
      customer_id: string;
      order_id: string;
      amount_inr: number;
      risk_score: number;
      priority?: CasePriority;
      assignee?: string;
    }>(req);
    if (!body?.prediction_id || !body?.customer_id) {
      return NextResponse.json(
        { detail: "prediction_id and customer_id are required" },
        { status: 422 },
      );
    }
    // SLA sweep on every open — cheap and keeps breach flags fresh.
    await sweepSla();
    const record = await openCase({
      predictionId: body.prediction_id,
      customerId: body.customer_id,
      orderId: body.order_id,
      amountInr: body.amount_inr,
      riskScore: body.risk_score,
      priority: body.priority,
      assignee: body.assignee,
    });
    return jsonOk(record);
  }) as NextResponse;
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  return withScope(req, ["cases:write", "audit:read"], async () => {
    const url = new URL(req.url);
    const status = url.searchParams.get("status") as CaseStatus | null;
    const priority = url.searchParams.get("priority") as CasePriority | null;
    const assignedTo = url.searchParams.get("assigned_to");
    const customerId = url.searchParams.get("customer_id");
    const limit = url.searchParams.get("limit");
    await sweepSla();
    const rows = await listCases({
      status: status ?? undefined,
      priority: priority ?? undefined,
      assignedTo: assignedTo ?? undefined,
      customerId: customerId ?? undefined,
      limit: limit ? Number(limit) : undefined,
    });
    return jsonOk({ cases: rows, total: rows.length });
  }) as NextResponse;
}
