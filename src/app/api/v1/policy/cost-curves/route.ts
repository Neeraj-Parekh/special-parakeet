// GET /api/v1/policy/cost-curves — proxy to Python GET /v1/policy/cost-curves.
// Returns the Drummond-Holte cost-curve sweep with bootstrap CIs.
//
// Query params forwarded: n_resamples (default 500), confidence (default 0.90).

import { NextRequest } from "next/server";
import { callBackend, forwardResponse, jsonOk } from "@/lib/api-proxy";
import {
  SAMPLE_COST_CURVES,
  SAMPLE_OPTIMAL_THRESHOLD,
} from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  const url = new URL(req.url);
  const n_resamples = url.searchParams.get("n_resamples") || "500";
  const confidence = url.searchParams.get("confidence") || "0.90";
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 6000);
    const backend = await callBackend("/v1/policy/cost-curves", {
      method: "GET",
      req,
      query: { n_resamples, confidence },
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk(
      {
        curves: SAMPLE_COST_CURVES,
        optimal_threshold: SAMPLE_OPTIMAL_THRESHOLD,
        n_samples: 7235,
        n_pos: 1664,
        n_neg: 5571,
        n_resamples: Number(n_resamples),
        confidence: Number(confidence),
        data_source: "mock — docs/cost_table.md (real backend runs ≥500 bootstrap CIs)",
      },
      { mock: true },
    );
  }
}
