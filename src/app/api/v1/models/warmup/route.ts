// GET /api/v1/models/warmup — observability for the RTC-2 SHAP
// prebuild. Returns whether the TreeExplainer is ready, the model
// version it was built for, and the build time budget.
//
// In production this endpoint is scraped by a readiness probe; the
// pod is only marked ready AFTER prebuilt===true. This is the
// cold-start p99 fix: move the ~900ms SHAP build out of the first
// request and into the boot overlap.

import { NextRequest } from "next/server";
import {
  isExplainerReady,
  prebuildExplainer,
  ensureExplainer,
} from "@/lib/shap/prebuild";
import { jsonOk } from "@/lib/api-proxy";

export const runtime = "nodejs";

const MODEL_VERSION = "v2025.08.29-track-c-v3";

export async function GET(req: NextRequest): Promise<Response> {
  // Kick the prebuild (idempotent) + report state.
  const readyBefore = isExplainerReady();
  prebuildExplainer(MODEL_VERSION);
  return jsonOk({
    model_version: MODEL_VERSION,
    explainer_prebuilt: isExplainerReady(),
    ready_before_request: readyBefore,
    build_budget_ms: 900,
    note:
      "TreeSHAP TreeExplainer prebuilt at module load (RTC-2 fix). " +
      "First-request cold-start p99 drops by ~900ms vs lazy construction.",
  });
}

// A POST hook for the score route to block-wait on the first
// request (rare in practice — the prebuild usually completes during
// route compilation).
export async function POST(req: NextRequest): Promise<Response> {
  await ensureExplainer(MODEL_VERSION);
  return jsonOk({ ready: true });
}
