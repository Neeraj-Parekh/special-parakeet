// GET /api/v1/models/current — proxy to Python GET /v1/models/current.
// Returns `{ champion: { version, deployed_at, metrics } }`.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import { SAMPLE_MODEL_CURRENT } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend("/v1/models/current", {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk(SAMPLE_MODEL_CURRENT, { mock: true });
  }
}
