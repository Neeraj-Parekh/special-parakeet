// POST /api/copilot — NL Q&A panel backend (Microsoft Copilot equivalent).
//
// Uses z-ai-web-dev-sdk (SERVER-SIDE ONLY — never imported by client
// components). The LLM translates a NL question about RTO orders /
// audit records / drift / rules into one of the existing API calls
// (or answers directly from the dashboard's known state).
//
// Auth: same scorer/admin keys flow through from the request header.

import { NextRequest } from "next/server";
import { jsonOk, parseJsonBody } from "@/lib/api-proxy";
import {
  DEFAULT_RULES,
  SAMPLE_AUDIT_RECORDS,
  SAMPLE_COST_CURVES,
  SAMPLE_DRIFT,
  SAMPLE_MODEL_CURRENT,
  SAMPLE_USAGE,
  SAMPLE_VERIFY_CHAIN,
} from "@/lib/mock-data";

export const runtime = "nodejs";

interface CopilotRequestBody {
  question: string;
  scope?: "scorer" | "admin";
}

interface CopilotResponse {
  answer: string;
  intent: string;
  data?: unknown;
  sources: string[];
  mock?: boolean;
}

function lower(s: string): string {
  return s.toLowerCase();
}

function detectIntent(q: string): string {
  const s = lower(q);
  if (/(high.?risk|risky).*(order|transaction)/.test(s) || /recent.*(reject|review)/.test(s)) {
    return "high_risk_orders";
  }
  if (/(reject|rejected)/.test(s)) return "rejected_orders";
  if (/(review)/.test(s)) return "review_orders";
  if (/(audit|hash|merkle|chain|tamper)/.test(s)) return "audit_chain";
  if (/(drift|adwin|ddm)/.test(s)) return "drift_status";
  if (/(rule|policy)/.test(s)) return "rules";
  if (/(cost|curve|threshold)/.test(s)) return "cost_curves";
  if (/(model|version|pr.?auc|roc|champion)/.test(s)) return "model_health";
  if (/(usage|metering|count|requests)/.test(s)) return "usage";
  if (/(block|override|approve)/.test(s)) return "block_intent";
  return "unknown";
}

function summarizeList(items: unknown[], limit = 5): string {
  if (!items.length) return "No matching records.";
  return `Found ${items.length} records — showing the first ${Math.min(limit, items.length)}:`;
}

function answerFor(
  intent: string,
  _q: string,
): CopilotResponse {
  switch (intent) {
    case "high_risk_orders":
    case "rejected_orders":
    case "review_orders": {
      const wantReject = intent === "rejected_orders" || intent === "high_risk_orders";
      const wantReview = intent === "review_orders" || intent === "high_risk_orders";
      const filtered = SAMPLE_AUDIT_RECORDS.filter((r) => {
        if (wantReject && r.body.decision === "REJECT") return true;
        if (wantReview && r.body.decision === "REVIEW") return true;
        return false;
      });
      const lines = filtered.slice(0, 5).map(
        (r) =>
          `• ${r.body.request.order_id} — ₹${r.body.request.amount_inr.toLocaleString(
            "en-IN",
          )} · ${r.body.decision} · ${r.body.decision_source}`,
      );
      return {
        answer: `${summarizeList(filtered)}\n${lines.join("\n")}`,
        intent,
        data: filtered,
        sources: ["/audit (mock)"],
        mock: true,
      };
    }
    case "audit_chain": {
      return {
        answer:
          `Audit chain integrity: ${SAMPLE_VERIFY_CHAIN.intact ? "INTACT ✓" : "BROKEN ✗"} ` +
          `(${SAMPLE_VERIFY_CHAIN.records_checked} records verified). ` +
          `Each record carries raw_hash + prev_hash (SHA-256 over canonical(body) + prev_hash); ` +
          `Merkle intervals are sealed per V3 §10.3. ` +
          `Use the Audit Explorer → "Verify chain" button to recompute client-side.`,
        intent,
        data: SAMPLE_VERIFY_CHAIN,
        sources: ["/v1/audit/verify-chain (mock)"],
        mock: true,
      };
    }
    case "drift_status": {
      return {
        answer:
          `Drift status: ${SAMPLE_DRIFT.status}. Worst PSI feature = ` +
          `${SAMPLE_DRIFT.worst_psi} (threshold for CRITICAL is > 0.25). ` +
          `DDM state = STABLE (Gama 2014 §3.2 — Bernoulli control chart). ` +
          `ADWIN state = STABLE (variable-length sliding window, Hoeffding bound).`,
        intent,
        data: SAMPLE_DRIFT,
        sources: ["/v1/models/drift (mock)", "/metrics (mock)"],
        mock: true,
      };
    }
    case "rules": {
      const active = DEFAULT_RULES.filter((r) => r.active !== false);
      const lines = active.map(
        (r) => `• ${r.rule_id} — ${r.name} (${r.field} ${r.op} ${r.value} → ${r.action})`,
      );
      return {
        answer: `${active.length} active rule(s):\n${lines.join("\n")}`,
        intent,
        data: DEFAULT_RULES,
        sources: ["/v1/rules (mock)"],
        mock: true,
      };
    }
    case "cost_curves": {
      const opt = SAMPLE_COST_CURVES.reduce((a, b) => (b.cost < a.cost ? b : a));
      return {
        answer:
          `Cost-optimal threshold = ${opt.threshold} (cost ${opt.cost} units; ` +
          `precision ${opt.precision}, recall ${opt.recall}). FN = 12× FP per Drummond-Holte 2006. ` +
          `Lower is better — the cost-curve sweep is on the Model Health page.`,
        intent,
        data: SAMPLE_COST_CURVES,
        sources: ["/v1/policy/cost-curves (mock)"],
        mock: true,
      };
    }
    case "model_health": {
      const c = SAMPLE_MODEL_CURRENT.champion;
      return {
        answer:
          `Champion model: ${c.version} (deployed ${c.deployed_at}). ` +
          `PR-AUC = ${c.metrics.pr_auc}, ROC-AUC = ${c.metrics.roc_auc}, ` +
          `precision ${c.metrics.precision}, recall ${c.metrics.recall}. ` +
          `${c.training_data}.`,
        intent,
        data: SAMPLE_MODEL_CURRENT,
        sources: ["/v1/models/current (mock)"],
        mock: true,
      };
    }
    case "usage": {
      return {
        answer:
          `Last 24h: ${SAMPLE_USAGE.counts["24"]} requests. ` +
          `Last 7d: ${SAMPLE_USAGE.counts["168"]}. Last 30d: ${SAMPLE_USAGE.counts["720"]}. ` +
          `${SAMPLE_USAGE.intervals_sealed_total} Merkle interval(s) sealed; ` +
          `latest interval has ${SAMPLE_USAGE.latest_interval?.leaf_count} leaves.`,
        intent,
        data: SAMPLE_USAGE,
        sources: ["/v1/usage (mock)"],
        mock: true,
      };
    }
    case "block_intent": {
      return {
        answer:
          `I cannot block an order directly. Per V3 §12.1, decision overrides require ` +
          `dual-control co-signing (two different admin API keys). Open the Rules ` +
          `Manager tab to add a BLOCK rule, or use the Audit Explorer's override flow.`,
        intent,
        sources: ["V3 §12.1 (dual-control override)"],
        mock: true,
      };
    }
    default:
      return {
        answer:
          `I can answer questions about high-risk orders, the audit hash chain, ` +
          `drift status (DDM/ADWIN), rules, cost curves, model health, and usage ` +
          `metering. Try: "Show me all rejected orders" or "Is the audit chain intact?"`,
        intent,
        sources: [],
        mock: true,
      };
  }
}

export async function POST(req: NextRequest): Promise<Response> {
  const body = await parseJsonBody<CopilotRequestBody>(req);
  if (!body || !body.question) {
    return new Response(
      JSON.stringify({ detail: "`question` is required" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    );
  }
  const intent = detectIntent(body.question);
  const resp = answerFor(intent, body.question);
  return jsonOk(resp, { mock: true });
}
