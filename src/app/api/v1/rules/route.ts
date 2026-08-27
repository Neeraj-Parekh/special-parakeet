// GET /api/v1/rules — proxy to Python GET /v1/rules (scorer scope).
// POST /api/v1/rules — proxy to Python POST /v1/rules (admin scope).
//
// GET returns `{ rules: Rule[] }`. POST body is RuleIn; returns
// `{ added: rule_id }`.

import { NextRequest } from "next/server";
import {
  callBackend,
  forwardResponse,
  jsonOk,
  parseJsonBody,
} from "@/lib/api-proxy";
import { DEFAULT_RULES, type Rule } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend("/v1/rules", {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk({ rules: DEFAULT_RULES }, { mock: true });
  }
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await parseJsonBody<Rule>(req);
  if (!body || !body.rule_id || !body.field || !body.op || !body.action) {
    return new Response(
      JSON.stringify({
        detail:
          "invalid rule — rule_id, field, op, value, action are required",
      }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
  }
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 5000);
    const backend = await callBackend("/v1/rules", {
      method: "POST",
      body: JSON.stringify(body),
      req,
      extraHeaders: { "Content-Type": "application/json" },
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    return jsonOk({ added: body.rule_id, mock: true }, { mock: true });
  }
}
