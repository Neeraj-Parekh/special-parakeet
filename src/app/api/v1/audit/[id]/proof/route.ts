// GET /api/v1/audit/[id]/proof — proxy to Python GET /v1/audit/{record_id}/proof.
//
// Path param `id` is the integer record_id (the SERIAL PK, NOT the
// string audit_id). Returns the Merkle inclusion proof dict from
// src/audit/logger.py::MerkleSealer.proof().
//
// Mock mode: in the sandbox the Python backend is unreachable + there's
// no Postgres to seal intervals against, so the route returns a mock
// Merkle proof (computed from SAMPLE_AUDIT_RECORDS) so the Audit
// Explorer can demonstrate the proof UX.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import { mockMerkleProof } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await ctx.params;
  const recordId = Number(id);
  if (Number.isNaN(recordId) || recordId < 1) {
    return new Response(
      JSON.stringify({ detail: "record_id must be a positive integer" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
  }
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend(
      `/v1/audit/${recordId}/proof`,
      { method: "GET", req, signal: ctrl.signal },
    );
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    const proof = mockMerkleProof(recordId);
    if (!proof) {
      return new Response(
        JSON.stringify({
          detail:
            "no Merkle interval sealed for this record (run seal_interval() first, or wait for the count/elapsed threshold to trip). file-mode audit has no Merkle layer — use GET /v1/audit/verify-chain for the per-record hash chain.",
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    return jsonOk(proof, { mock: true });
  }
}
