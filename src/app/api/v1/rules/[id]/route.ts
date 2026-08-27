// DELETE /api/v1/rules/[id] — proxy to Python DELETE /v1/rules/{rule_id} (admin).
// Returns `{ removed: bool }`.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function DELETE(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params;
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend(`/v1/rules/${encodeURIComponent(id)}`, {
      method: "DELETE",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk({ removed: true, mock: true }, { mock: true });
  }
}
