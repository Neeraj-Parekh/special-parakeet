// GET /api/metrics — proxy to Python GET /metrics (no auth).
//
// Returns Prometheus text format. The dashboard parses
// rto_drift_ddm_state + rto_drift_adwin_state gauges (0=STABLE,
// 1=WARNING, 2=DRIFT) for the live Model Health drift panel.

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, textOk } from "@/lib/api-proxy";
import { SAMPLE_METRICS_TEXT } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend("/metrics", {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return textOk(SAMPLE_METRICS_TEXT, { mock: true });
  }
}
