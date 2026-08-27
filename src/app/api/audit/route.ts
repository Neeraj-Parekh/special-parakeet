// GET /api/audit — list recent audit records (preview only).
//
// The Python API does NOT expose a JSON list endpoint. The audit
// export route (GET /v1/compliance/audit-export) returns CSV (admin
// scope); the single-record route (GET /audit/{audit_id}) requires an
// id. To render the Audit Explorer's table without first pasting an
// audit_id, this route returns mock records (from src/lib/mock-data).
//
// When the dashboard wants a single LIVE record, it calls /api/audit/[id]
// which DOES proxy to /audit/{audit_id} on the Python backend.

import { jsonOk } from "@/lib/api-proxy";
import { SAMPLE_AUDIT_RECORDS } from "@/lib/mock-data";

export const runtime = "nodejs";

export async function GET(): Promise<Response> {
  // Always mock — the Python backend has no list endpoint. The
  // individual /audit/{id} route handles live single-record fetch.
  return jsonOk(
    { records: SAMPLE_AUDIT_RECORDS, source: "mock" },
    { mock: true },
  );
}
