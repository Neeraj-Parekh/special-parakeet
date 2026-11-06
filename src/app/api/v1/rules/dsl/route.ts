// POST /api/v1/rules/dsl — compile + store a declarative rule.
// GET  /api/v1/rules/dsl — export all stored rules as their DSL JSON.
//
// The DSL spec (see docs/RULE_DSL.md):
//   {
//     "rule_name": "HighValueCOD",
//     "condition": "payment_method == 'COD' AND amount_inr > 50000",
//     "action": "REJECT",
//     "priority": 1
//   }
//
// POST returns 200 { rule_name, action, priority, compiled_at } on
// success, or 422 { detail, pos? } on parse/compile failure. The 422
// carries the 1-indexed `pos` of the offending token so the editor UI
// can render a precise caret.
//
// Production swap: replace `upsertCompiledRule` with a Prisma
// `db.rule.create({ ... })` call. See docs/RULE_DSL.md § Production.

import { NextRequest, NextResponse } from "next/server";
import { compileRule, errorToBody, type DslRuleInput } from "@/lib/rule-dsl/compiler";
import { listCompiledRules, upsertCompiledRule } from "@/lib/rule-dsl/store";
import { parseJsonBody } from "@/lib/api-proxy";

export const runtime = "nodejs";

/**
 * POST handler — compile a DSL rule spec, validate it, store it.
 *
 * Responds 200 with the compiled rule's metadata, or 422 with a
 * precise error body.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await parseJsonBody<DslRuleInput>(req);
  if (!body) {
    return NextResponse.json(
      { detail: "invalid request — JSON body required" },
      { status: 422 },
    );
  }
  try {
    const compiled = compileRule(body);
    upsertCompiledRule(compiled, body);
    return NextResponse.json(
      {
        rule_name: compiled.rule_name,
        action: compiled.action,
        priority: compiled.priority,
        compiled_at: compiled.compiled_at,
        mock: false,
      },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    );
  } catch (err) {
    return NextResponse.json(errorToBody(err), {
      status: 422,
      headers: { "Cache-Control": "no-store" },
    });
  }
}

/**
 * GET handler — export every stored rule as its original DSL JSON.
 *
 * Returns `{ rules: DslRuleInput[], count: number }`.
 */
export async function GET(): Promise<NextResponse> {
  const stored = listCompiledRules();
  const rules = stored.map((r) => r.dsl);
  return NextResponse.json(
    { rules, count: rules.length, mock: false },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}
