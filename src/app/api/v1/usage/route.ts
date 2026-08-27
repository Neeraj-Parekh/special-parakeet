// GET /api/v1/usage — proxy to Python GET /v1/usage (admin).
// Returns per-window counts + Merkle interval sealing cadence.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import { SAMPLE_USAGE } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const since = url.searchParams.get("since_hours") || "24,168,720";
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend("/v1/usage", {
      method: "GET",
      req,
      query: { since_hours: since },
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk({ ...SAMPLE_USAGE, since_hours: since.split(",").map(Number) }, {
      mock: true,
    });
  }
}
