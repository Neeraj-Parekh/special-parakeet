// GET /api/v1/audit/verify-chain — proxy to Python GET /v1/audit/verify-chain.
//
// Returns `{ intact: bool, records_checked: int, first_bad_audit_id: string|null }`.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import { SAMPLE_VERIFY_CHAIN } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend("/v1/audit/verify-chain", {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk(SAMPLE_VERIFY_CHAIN, { mock: true });
  }
}
