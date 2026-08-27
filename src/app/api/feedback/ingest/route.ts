// POST /api/feedback/ingest — proxy to Python POST /v1/feedback/ingest (admin).
// Body: { prediction_id: str, is_returned: bool, returned_at?: str }.
// Returns the current drift-detector state.

import { NextRequest } from "next/server";
import {
  callBackend,
  forwardResponse,
  jsonOk,
  parseJsonBody,
} from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<Response> {
  const body = await parseJsonBody<{
    prediction_id: string;
    is_returned: boolean;
    returned_at?: string | null;
  }>(req);
  if (!body || !body.prediction_id || typeof body.is_returned !== "boolean") {
    return new Response(
      JSON.stringify({
        detail:
          "invalid feedback body — prediction_id (str) + is_returned (bool) required",
      }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
  }
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 5000);
    const backend = await callBackend("/v1/feedback/ingest", {
      method: "POST",
      body: JSON.stringify(body),
      req,
      extraHeaders: { "Content-Type": "application/json" },
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk(
      {
        status: "ingested",
        prediction_id: body.prediction_id,
        is_returned: body.is_returned,
        error_indicator: body.is_returned ? 1 : 0,
        ddm_state: "STABLE",
        adwin_state: "STABLE",
        ddm_p: 0.183,
        ddm_p_min: 0.176,
        ddm_sigma_min: 0.018,
        adwin_window_len: 412,
        mock: true,
      },
      { mock: true },
    );
  }
}
