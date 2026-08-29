// GET  /api/v1/stream/events — return recent decision events (last 50).
// POST /api/v1/stream/events — push a test event onto the stream.
//
// This is the demo surface for the streaming transport. The dashboard's
// "Recent Decisions" panel calls GET; the dev console's "Fire test
// event" button calls POST. In production the POST is unused (real
// events come from /risk/score via `publishDecisionEvent`) but we keep
// the route so the demo can run without a running scorer.

import { NextRequest, NextResponse } from "next/server";
import { stream, type DecisionEvent } from "@/lib/streaming/redis-stream";
import { parseJsonBody } from "@/lib/api-proxy";
import type { Decision } from "@/lib/mock-data";

export const runtime = "nodejs";

/**
 * GET — return recent decision events, newest first.
 *
 * Query params:
 *   limit  — number of events (default 50, max 500)
 *   customer — filter by customer_id (optional; if present, also runs
 *              detectRapidRejects on that customer and returns the
 *              result in `cep_alert`).
 */
export async function GET(req: NextRequest): Promise<NextResponse> {
  const sp = req.nextUrl.searchParams;
  const limit = Math.min(
    Math.max(parseInt(sp.get("limit") ?? "50", 10) || 50, 1),
    500,
  );
  const customer = sp.get("customer") ?? undefined;
  const events = stream.recent(limit);
  const cepAlert =
    customer !== undefined
      ? stream.detectRapidRejects(customer)
      : undefined;
  return NextResponse.json(
    {
      events,
      total: stream.totalPublished,
      cep_alert: cepAlert,
      mock: false,
    },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}

/**
 * POST — push a synthetic test event onto the stream. Used by the
 * demo console to prove the CEP engine fires on rapid REJECTs.
 *
 * Body shape (all optional except customer_id + decision):
 *   { customer_id, order_id, decision, probability, reason }
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await parseJsonBody<
    Partial<DecisionEvent> & { customer_id?: string; decision?: Decision | null }
  >(req);
  if (!body || !body.customer_id) {
    return NextResponse.json(
      { detail: "invalid request — customer_id is required" },
      { status: 422 },
    );
  }
  const ev = stream.publishDecisionEvent({
    customer_id: body.customer_id,
    order_id: body.order_id ?? `ORD-TEST-${Date.now().toString(36)}`,
    decision: body.decision ?? "REJECT",
    probability: body.probability ?? 0.95,
    reason: body.reason ?? "synthetic test event from /api/v1/stream/events",
    timestamp: new Date().toISOString(),
  });
  const cepAlert = stream.detectRapidRejects(body.customer_id);
  return NextResponse.json(
    {
      event: ev,
      cep_alert: cepAlert,
      total: stream.totalPublished,
      mock: false,
    },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}
