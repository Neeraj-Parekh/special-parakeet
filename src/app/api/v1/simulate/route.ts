// POST /api/v1/simulate — proxy to Python POST /v1/simulate (scorer).
// Body: { order: OrderIn, mandate?: string, dry_run?: bool }.
// Returns the same shape as /risk/score minus audit_trail_url/case_id
// (both null — nothing persisted).

import { NextRequest } from "next/server";
import {
  callBackend,
  forwardResponse,
  jsonOk,
  parseJsonBody,
} from "@/lib/api-proxy";
import { mockScore, type OrderInput } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<Response> {
  const body = await parseJsonBody<{
    order: OrderInput;
    mandate?: string;
    dry_run?: boolean;
  }>(req);
  if (!body || !body.order || !body.order.order_id) {
    return new Response(
      JSON.stringify({
        detail: "invalid simulate body — `order` with `order_id` is required",
      }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
  }
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 5000);
    const backend = await callBackend("/v1/simulate", {
      method: "POST",
      body: JSON.stringify({ ...body, dry_run: true }),
      req,
      extraHeaders: { "Content-Type": "application/json" },
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    const value = mockScore(
      body.order,
      undefined,
      body.mandate ?? null,
      body.mandate ? "cod_order" : undefined,
    );
    return jsonOk(
      {
        ...value,
        audit_trail_url: null,
        case_id: null,
        prediction_id: null,
        rule_trace: [],
        dry_run: true,
      },
      { mock: true },
    );
  }
}
