// Rule DSL — in-memory store for compiled rules.
//
// In the hackathon the Next.js process is the only "scorer" the
// dashboard sees, so a process-local Map keyed by rule_name is the
// simplest honest store. In production this swaps to a row in the
// `rules` Prisma table (see docs/RULE_DSL.md § Production swap) — the
// GET / POST handlers in route.ts are the only callers of this module,
// so the swap is a single-file change.

import type { CompiledRule, DslRuleInput, RuleContext } from "./compiler";

/** A compiled rule plus the predicate evaluated against contexts. */
interface StoredRule extends CompiledRule {
  /** Original DSL JSON, returned verbatim by GET /export. */
  dsl: DslRuleInput;
}

const store = new Map<string, StoredRule>();

/**
 * Insert (or replace) a compiled rule keyed by rule_name.
 *
 * @param rule - the compiled rule + its original DSL
 */
export function upsertCompiledRule(rule: CompiledRule, dsl: DslRuleInput): void {
  store.set(rule.rule_name, { ...rule, dsl });
}

/** Remove a compiled rule by name; returns true if a rule was removed. */
export function deleteCompiledRule(name: string): boolean {
  return store.delete(name);
}

/** Fetch one compiled rule by name. */
export function getCompiledRule(name: string): StoredRule | undefined {
  return store.get(name);
}

/** Return all stored rules, sorted by priority ascending then rule_name. */
export function listCompiledRules(): StoredRule[] {
  return Array.from(store.values()).sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority;
    return a.rule_name.localeCompare(b.rule_name);
  });
}

/**
 * Evaluate all stored rules against an order context, in priority order.
 * Returns the first matching rule's action (or null if none match).
 *
 * This is the seam the score path calls to apply DSL rules alongside
 * the cost-optimizer / mandate precedence. See docs/RULE_DSL.md §
 * Integration with /risk/score.
 *
 * @param ctx - merchant order fields
 * @returns the action of the first matching rule, or null
 */
export function evaluateRules(ctx: RuleContext): {
  rule_name: string;
  action: string;
} | null {
  for (const rule of listCompiledRules()) {
    try {
      if (rule.predicate(ctx)) {
        return { rule_name: rule.rule_name, action: rule.action };
      }
    } catch {
      // The compiled predicate is built to never throw, but we stay
      // defensive — a bad rule must not break the score path.
      continue;
    }
  }
  return null;
}

/** Reset the store — exposed for tests and the dev console. */
export function resetStore(): void {
  store.clear();
}
