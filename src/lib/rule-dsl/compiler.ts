// Rule DSL — compiler core.
//
// Walks the AST produced by grammar.ts and emits a TypeScript predicate
// `(ctx: RuleContext) => boolean` that evaluates the condition against a
// merchant order at scoring time. This is the layer Razorpay AdaDSL calls
// "the compiled rule" — it stores a JSON spec at write-time, then
// materializes a function pointer at runtime.
//
// Two compile-time validations:
//   1. Every identifier referenced in the condition MUST be a known field
//      from the OrderInput schema (the field registry below). Unknown
//      fields cause a 422 — we refuse to ship a rule that compares an
//      undefined column.
//   2. Type-comparison sanity: a string literal may only be compared with
//      `==` or `!=` (no ordering on strings). A number may use any
//      operator. An identifier resolves at runtime so we accept all ops.
//
// The compiled predicate NEVER throws — a missing field on ctx resolves
// to `undefined` and a comparison returns `false` rather than `null`-ing
// out. This matches the Python rule engine's `safe_eval` semantics.

import {
  GrammarError,
  parseCondition,
  type AstNode,
} from "./grammar";

/**
 * The set of fields a rule condition may legally reference. Anything
 * outside this set is a typo / unknown column and the compiler rejects
 * it at 422. Mirrors src/lib/mock-data.ts::OrderInput.
 */
export const ALLOWED_FIELDS: ReadonlySet<string> = new Set([
  "order_id",
  "amount_inr",
  "category",
  "customer_id",
  "address_quality",
  "city_tier",
  "prior_orders",
  "prior_returns",
  "items",
  "order_hour",
  "device",
  "payment_method",
]);

/** Untyped evaluation context — the merchant order plus any derived columns. */
export type RuleContext = Record<string, unknown>;

/** The action a rule emits when its predicate returns true. */
export type RuleAction = "ACCEPT" | "REVIEW" | "REJECT";

/** The DSL JSON shape accepted by POST /api/v1/rules/dsl. */
export interface DslRuleInput {
  rule_name: string;
  condition: string;
  action: RuleAction;
  priority?: number;
}

/** A rule after compilation — the original DSL + the live predicate. */
export interface CompiledRule {
  rule_name: string;
  condition: string;
  action: RuleAction;
  priority: number;
  /** Compiled predicate; never throws. */
  predicate: (ctx: RuleContext) => boolean;
  /** ISO timestamp of compilation. */
  compiled_at: string;
}

/** Error thrown by the compiler; carries a 1-indexed position when known. */
export class CompileError extends Error {
  pos?: number;
  constructor(message: string, pos?: number) {
    super(pos ? `${message} at pos ${pos}` : message);
    this.name = "CompileError";
    if (pos !== undefined) this.pos = pos;
  }
}

/** True iff `v` is a non-empty trimmed string. */
function isNonEmptyString(v: unknown): v is string {
  return typeof v === "string" && v.trim().length > 0;
}

/**
 * Compile a DSL rule spec into a live predicate.
 *
 * Steps:
 *   1. Validate the input shape (rule_name, condition, action required).
 *   2. Parse the condition into an AST (GrammarError on syntax failure).
 *   3. Walk the AST: validate identifiers + types, returning a closure.
 *
 * @param spec - the DSL JSON
 * @returns the compiled rule (DSL + predicate + meta)
 * @throws CompileError on validation/compilation failure
 */
export function compileRule(spec: DslRuleInput): CompiledRule {
  if (!isNonEmptyString(spec.rule_name)) {
    throw new CompileError("rule_name is required");
  }
  if (!isNonEmptyString(spec.condition)) {
    throw new CompileError("condition is required");
  }
  if (spec.action !== "ACCEPT" && spec.action !== "REVIEW" && spec.action !== "REJECT") {
    throw new CompileError(
      `invalid action '${String(spec.action)}' — expected ACCEPT | REVIEW | REJECT`,
    );
  }
  const priority =
    typeof spec.priority === "number" && Number.isFinite(spec.priority)
      ? Math.trunc(spec.priority)
      : 0;

  // Parse may throw GrammarError — we surface it unchanged so the route
  // handler can read the `pos` field.
  const ast = parseCondition(spec.condition);

  // Walk the AST once for validation, then once to build the predicate.
  // Two passes is simpler than threading a closure builder through the
  // validator; the ASTs are tiny (dozens of nodes max).
  validateAst(ast);

  const predicate = buildPredicate(ast);

  return {
    rule_name: spec.rule_name,
    condition: spec.condition,
    action: spec.action,
    priority,
    predicate,
    compiled_at: new Date().toISOString(),
  };
}

/**
 * Validate the AST: every identifier must be a known field, and string
 * operands may not appear under ordering operators (`>` `<` `>=` `<=`).
 *
 * @param node - the AST root
 * @throws CompileError on the first violation
 */
function validateAst(node: AstNode): void {
  switch (node.type) {
    case "and":
    case "or":
      validateAst(node.left);
      validateAst(node.right);
      return;
    case "not":
      validateAst(node.operand);
      return;
    case "comparison":
      validateComparison(node);
      return;
    case "ident":
      if (!ALLOWED_FIELDS.has(node.name)) {
        throw new CompileError(
          `unknown field '${node.name}' — allowed: ${[...ALLOWED_FIELDS].join(", ")}`,
          node.pos,
        );
      }
      return;
    case "number":
    case "string":
      return;
  }
}

/**
 * Type-check a comparison node. Strings may only use `==`/`!=`; numbers
 * may use any operator; identifiers are runtime-typed so all ops are
 * accepted (the predicate applies the same string/number rules at
 * evaluation time).
 */
function validateComparison(node: {
  operator: string;
  left: AstNode;
  right: AstNode;
}): void {
  const { operator, left, right } = node;
  // String-vs-ordering check — apply on whichever side is a literal.
  for (const side of [left, right]) {
    if (side.type === "string" && (operator === ">" || operator === "<" || operator === ">=" || operator === "<=")) {
      throw new CompileError(
        `cannot apply '${operator}' to a string literal`,
        side.pos,
      );
    }
  }
  validateAst(left);
  validateAst(right);
}

/**
 * Build the runtime predicate by recursively lowering the AST into a
 * chain of small typed closures. No `eval`, no `new Function` — we
 * return closures that capture nothing but their child predicates.
 *
 * @param node - the AST root
 * @returns a function `(ctx) => boolean`
 */
function buildPredicate(node: AstNode): (ctx: RuleContext) => boolean {
  switch (node.type) {
    case "and": {
      const l = buildPredicate(node.left);
      const r = buildPredicate(node.right);
      return (ctx) => l(ctx) && r(ctx);
    }
    case "or": {
      const l = buildPredicate(node.left);
      const r = buildPredicate(node.right);
      return (ctx) => l(ctx) || r(ctx);
    }
    case "not": {
      const inner = buildPredicate(node.operand);
      return (ctx) => !inner(ctx);
    }
    case "comparison": {
      const lEval = buildValue(node.left);
      const rEval = buildValue(node.right);
      const op = node.operator;
      return (ctx) => compare(op, lEval(ctx), rEval(ctx));
    }
    // A bare operand at expression root: truthy check (non-zero / non-empty
    // / boolean-true). Matches how Python's rule engine handles `active`
    // or `is_new` style boolean fields.
    case "ident":
    case "number":
    case "string": {
      const v = buildValue(node);
      return (ctx) => isTruthy(v(ctx));
    }
  }
}

/** Compile a leaf node to a value-extractor (no comparison logic). */
function buildValue(node: AstNode): (ctx: RuleContext) => unknown {
  switch (node.type) {
    case "ident":
      return (ctx) => ctx[node.name];
    case "number":
      return () => node.value;
    case "string":
      return () => node.value;
    default:
      // An inner logical node used as a value — coerce via the predicate
      // path. This shouldn't happen for well-formed rules but we degrade
      // gracefully.
      const inner = buildPredicate(node);
      return (ctx) => inner(ctx);
  }
}

/** Compare two runtime values under the given operator. Returns false
 * on type mismatch instead of throwing — see header docstring. */
function compare(op: string, a: unknown, b: unknown): boolean {
  switch (op) {
    case "==":
      return looseEq(a, b);
    case "!=":
      return !looseEq(a, b);
    case ">":
      return numericCompare(a, b) > 0;
    case "<":
      return numericCompare(a, b) < 0;
    case ">=":
      return numericCompare(a, b) >= 0;
    case "<=":
      return numericCompare(a, b) <= 0;
    default:
      return false;
  }
}

/** Loose equality — numeric strings coerce to numbers, so `"1" == 1`. */
function looseEq(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  // Coerce string<->number when both sides are primitive.
  if (typeof a === "number" && typeof b === "string") {
    const n = Number(b);
    return !Number.isNaN(n) && a === n;
  }
  if (typeof b === "number" && typeof a === "string") {
    const n = Number(a);
    return !Number.isNaN(n) && b === n;
  }
  // Booleans compared as themselves.
  if (typeof a === "boolean" || typeof b === "boolean") {
    return Boolean(a) === Boolean(b);
  }
  return false;
}

/**
 * Numeric comparison — returns NaN-like (negative infinity) when types
 * are incompatible so the outer predicate just returns false.
 */
function numericCompare(a: unknown, b: unknown): number {
  const an = toNumber(a);
  const bn = toNumber(b);
  if (an === null || bn === null) return Number.NaN;
  if (an > bn) return 1;
  if (an < bn) return -1;
  return 0;
}

/** Coerce a value to a number, returning null when impossible. */
function toNumber(v: unknown): number | null {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  if (typeof v === "boolean") return v ? 1 : 0;
  return null;
}

/** Truthiness — undefined/null/empty/0/NaN/false all falsy. */
function isTruthy(v: unknown): boolean {
  if (v === undefined || v === null) return false;
  if (typeof v === "number") return !Number.isNaN(v) && v !== 0;
  if (typeof v === "string") return v.trim().length > 0;
  if (typeof v === "boolean") return v;
  return true;
}

/**
 * Wrap a CompileError or GrammarError into a JSON-serializable body for
 * the 422 response. Used by the route handler.
 */
export function errorToBody(err: unknown): {
  detail: string;
  pos?: number;
} {
  if (err instanceof GrammarError || err instanceof CompileError) {
    const pos = err.pos;
    return pos !== undefined
      ? { detail: err.message, pos }
      : { detail: err.message };
  }
  return { detail: err instanceof Error ? err.message : "unknown error" };
}

export { GrammarError };
