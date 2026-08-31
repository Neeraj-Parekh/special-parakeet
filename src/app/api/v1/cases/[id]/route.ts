// GET   /api/v1/cases/[id] — case detail.
// PATCH /api/v1/cases/[id] — transition status / reassign / resolve.

import { NextRequest, NextResponse } from "next/server";
import { withScope } from "@/lib/auth/guard";
import {
  getCase,
  updateCase,
  sweepSla,
  type CaseStatus,
  type CaseResolution,
} from "@/lib/cases/service";
import { parseJsonBody, jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  return withScope(req, ["cases:write", "audit:read"], async () => {
    await sweepSla();
    const record = await getCase(id);
    if (!record) {
      return NextResponse.json({ detail: "case not found" }, { status: 404 });
    }
    return jsonOk(record);
  }) as NextResponse;
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  return withScope(req, "cases:write", async () => {
    const body = await parseJsonBody<{
      status?: CaseStatus;
      assigned_to?: string | null;
      qa_reviewer?: string | null;
      resolution?: CaseResolution;
      resolution_note?: string;
    }>(req);
    if (!body) {
      return NextResponse.json({ detail: "empty patch body" }, { status: 422 });
    }
    const updated = await updateCase(id, {
      status: body.status,
      assignedTo: body.assigned_to,
      qaReviewer: body.qa_reviewer,
      resolution: body.resolution,
      resolutionNote: body.resolution_note,
    });
    if (!updated) {
      return NextResponse.json({ detail: "case not found" }, { status: 404 });
    }
    return jsonOk(updated);
  }) as NextResponse;
}
