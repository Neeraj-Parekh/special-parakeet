// POST /api/risk/score — proxy to Python POST /risk/score.
//
// Body shape (OrderIn — see src/api/routes.py::OrderIn):
//   { order_id, amount_inr, category, customer_id, address_quality,
//     city_tier, payment_method, prior_orders, prior_returns, items,
//     order_hour, device }
//
// Headers forwarded: Authorization (scorer-scope Bearer), Idempotency-Key,
// X-Mandate, X-Device-Id, X-User-Id (per Track D V3 §13 / NPCI OC-201B).
//
// Mock mode: when the Python API at API_BASE_URL is unreachable, the
// route returns a mock ScoreResponse computed by mockScore() (mirrors
// the Track C decision precedence: rules → mandate → cost-optimizer).

import { NextRequest } from "next/server";
import {
  callBackend,
  forwardResponse,
  jsonOk,
  parseJsonBody,
} from "@/lib/api-proxy";
import { mockScore, type OrderInput } from "@/lib/mock-data";

export const runtime = "nodejs";

function badRequest(detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status: 422,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await parseJsonBody<
    OrderInput & { mandate_type?: string }
  >(req);
  if (!body || !body.order_id || !body.amount_inr) {
    return badRequest("invalid request — order_id and amount_inr are required");
  }
  const mandateHeader = req.headers.get("x-mandate");
  const mandateType =
    body.mandate_type || (mandateHeader ? "cod_order" : null);

  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 5000);
    const backend = await callBackend("/risk/score", {
      method: "POST",
      body: JSON.stringify(body),
      req,
      extraHeaders: { "Content-Type": "application/json" },
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    const value = mockScore(body, undefined, mandateHeader, mandateType ?? undefined);
    return jsonOk(value, { mock: true });
  }
}
