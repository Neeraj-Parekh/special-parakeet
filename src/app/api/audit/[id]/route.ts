// GET /api/audit/[id] — proxy to Python GET /audit/{audit_id} (admin).
//
// Path param `id` is the string audit_id returned by /risk/score
// (e.g. `aud_4f0b2982`). Mock mode: when the backend is unreachable
// this route searches the in-memory SAMPLE_AUDIT_RECORDS list + falls
// back to a 404 if no record matches.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import { SAMPLE_AUDIT_RECORDS } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params;
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend(`/audit/${encodeURIComponent(id)}`, {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    const found = SAMPLE_AUDIT_RECORDS.find((r) => r.audit_id === id);
    if (!found) {
      return new Response(
        JSON.stringify({ detail: "audit record not found", audit_id: id }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    return jsonOk(found, { mock: true });
  }
}
