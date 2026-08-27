// GET /api/v1/compliance/audit-export — proxy to Python
// GET /v1/compliance/audit-export (admin). Returns a CSV attachment.
//
// Mock mode: when the backend is unreachable, returns a synthetic CSV
// built from SAMPLE_AUDIT_RECORDS so the dashboard's "Download CSV"
// button still works in the preview.

import { NextRequest } from "next/server";
import { callBackend, csvOk, forwardResponse } from "@/lib/api-proxy";
import { SAMPLE_AUDIT_RECORDS } from "@/lib/mock-data";

export const runtime = "nodejs";

function buildCsv(): string {
  const headers = [
    "audit_id",
    "prediction_id",
    "raw_hash",
    "prev_hash",
    "created_at",
    "order_id",
    "amount_inr",
    "category",
    "customer_id",
    "address_quality",
    "city_tier",
    "payment_method",
    "prior_orders",
    "prior_returns",
    "decision",
    "decision_source",
    "probability",
    "rule_fired",
    "mandate_verdict",
    "mandate_type",
    "bh_purpose_code",
    "device_id",
    "user_id",
    "model_version",
  ];
  const rows = SAMPLE_AUDIT_RECORDS.map((r) => [
    r.audit_id,
    r.prediction_id,
    r.raw_hash,
    r.prev_hash,
    r.created_at,
    r.body.request.order_id,
    r.body.request.amount_inr,
    r.body.request.category,
    r.body.request.customer_id,
    r.body.request.address_quality,
    r.body.request.city_tier,
    r.body.request.payment_method,
    r.body.request.prior_orders,
    r.body.request.prior_returns,
    r.body.decision,
    r.body.decision_source,
    r.body.probability ?? "",
    r.body.rule_fired ?? "",
    r.body.mandate_verdict,
    r.body.mandate_type ?? "",
    r.body.bh_purpose_code ?? "",
    r.body.device_id ?? "",
    r.body.user_id ?? "",
    r.body.model_version,
  ]);
  const esc = (v: unknown) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [headers, ...rows]
    .map((row) => row.map(esc).join(","))
    .join("\n");
}

export async function GET(req: NextRequest): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 5000);
    const backend = await callBackend("/v1/compliance/audit-export", {
      method: "GET",
      req,
      signal: ctrl.signal,
    });
    clearTimeout(timeout);
    return forwardResponse(backend);
  } catch {
    const stamp = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15) + "Z";
    return csvOk(buildCsv(), `audit-export-mock-${stamp}.csv`, { mock: true });
  }
}
