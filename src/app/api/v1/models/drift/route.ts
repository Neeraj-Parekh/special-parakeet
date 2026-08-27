// GET /api/v1/models/drift — proxy to Python GET /v1/models/drift (admin).
// Returns `{ status, n_observed, psi: { feature: value } }`.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import { SAMPLE_DRIFT } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend("/v1/models/drift", {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk(SAMPLE_DRIFT, { mock: true });
  }
}
