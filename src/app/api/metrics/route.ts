// GET /api/metrics — proxy to Python GET /metrics.
//
// SEC-2 (part 2): if METRICS_SCRAPER_TOKEN is set, require a matching
// `Authorization: Bearer <token>` header (the Prometheus
// scrape_config pattern). If the env var is unset, the endpoint is
// open — the demo dashboard reads it client-side. Production MUST set
// METRICS_SCRAPER_TOKEN; a logged warning is emitted when it's unset.
//
// Returns Prometheus text format. The dashboard parses
// rto_drift_ddm_state + rto_drift_adwin_state gauges (0=STABLE,
// 1=WARNING, 2=DRIFT) for the live Model Health drift panel.

import { NextRequest, NextResponse } from "next/server";
import { callBackend, forwardResponse, textOk } from "@/lib/api-proxy";
import { SAMPLE_METRICS_TEXT } from "@/lib/mock-data";
import { bearerFrom, safeEqual } from "@/lib/auth/jwt";

export const runtime = "nodejs";

function unauthorized(): Response {
  return new Response(
    JSON.stringify({ detail: "metrics endpoint requires a valid scraper token" }),
    {
      status: 401,
      headers: {
        "Content-Type": "application/json",
        "WWW-Authenticate": 'Bearer realm="metrics"',
        "Cache-Control": "no-store",
      },
    },
  );
}

export async function GET(req: NextRequest): Promise<Response> {
  // SEC-2 — opt-in scraper-token auth.
  const scraperToken = process.env.METRICS_SCRAPER_TOKEN;
  if (scraperToken) {
    const bearer = bearerFrom(req.headers.get("authorization"));
    if (!bearer || !safeEqual(bearer, scraperToken)) {
      return unauthorized();
    }
  } else if (process.env.NODE_ENV === "production") {
    // Soft warning — the route still serves (the dashboard needs it)
    // but the operator gets a log line.
    console.warn(
      "SEC-2 warning: METRICS_SCRAPER_TOKEN unset — /api/metrics is open. " +
        "Set it in production to require Prometheus scraper auth.",
    );
  }

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
