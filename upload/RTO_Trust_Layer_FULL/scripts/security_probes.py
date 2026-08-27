"""Mechanical security probes against the Risk API (v2-aware). Evidence over claims."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes import create_app  # noqa: E402

VALID = {
    "order_id": "SEC-001",
    "amount_inr": 899,
    "category": "Fashion",
    "customer_id": "CUST-1",
}
SCORER_H = {"Authorization": "Bearer score-demo-key"}
ADMIN_H = {"Authorization": "Bearer admin-demo-key"}


def main() -> int:
    findings: list[dict] = []
    with TestClient(create_app(scorer_rate_per_min=1000)) as c:

        r = c.post("/risk/score", json=VALID)
        findings.append(_f("AUTHN-MISSING", "POST /risk/score, zero credentials",
                           f"HTTP {r.status_code}", "CRITICAL", {401, 403}))

        r = c.post("/risk/score", json={**VALID, "amount_inr": 1e15}, headers=SCORER_H)
        obs = f"HTTP {r.status_code}"
        if r.status_code == 200:
            obs += f", scored p={r.json().get('probability')}"
        findings.append(_f("INPUT-UNBOUNDED", "amount_inr=1e15 (Rs 1 quadrillion)",
                           obs, "HIGH", {422}))

        r = c.get("/audit/nonexistent-id", headers=SCORER_H)
        scored = c.post(
            "/risk/score", json={**VALID, "order_id": "SEC-AUD"}, headers=SCORER_H
        ).json()
        audit_url = scored["audit_trail_url"]
        as_scorer = c.get(audit_url, headers=SCORER_H)
        as_admin = c.get(audit_url, headers=ADMIN_H)
        rec = as_admin.json() if as_admin.status_code == 200 else {}
        raw_pii = "CUST-1" in str(rec)
        findings.append(
            _f(
                "AUDIT-AUTHZ-PII",
                "GET /audit/:id with scorer key vs admin key; PII scan",
                (
                    f"scorer->HTTP {as_scorer.status_code} (expect 401), "
                    f"admin->HTTP {as_admin.status_code}; raw_customer_leak={raw_pii}"
                ),
                "CRITICAL",
                set(),
            )
        )
        findings[-1]["mitigated"] = as_scorer.status_code == 401 and not raw_pii

        t0 = time.time()
        codes = []
        for i in range(60):
            payload = {**VALID, "order_id": f"SEC-B-{i}"}
            codes.append(c.post("/risk/score", json=payload, headers=SCORER_H).status_code)
        dt = time.time() - t0
        findings.append(
            _f("RATELIMIT-MISSING", "60 requests back-to-back (limit raised for probe)",
               f"all HTTP {set(codes)} in {dt:.2f}s ({60/dt:.0f} req/s)",
               "INFO", set())
        )

        h = {**SCORER_H, "Idempotency-Key": "probe-dup"}
        r1 = c.post("/risk/score", json={**VALID, "order_id": "SEC-C"}, headers=h).json()
        r2 = c.post("/risk/score", json={**VALID, "order_id": "SEC-C"}, headers=h).json()
        dup = r1["prediction_id"] != r2["prediction_id"]
        replay_flag = r2.get("replayed") is True
        findings.append(
            _f("IDEMPOTENCY-MISSING", "identical request twice w/ same key",
               f"duplicate_ids={dup}, replay_flag={replay_flag}", "MEDIUM", set())
        )
        findings[-1]["mitigated"] = not dup and replay_flag

        err = c.post("/risk/score", json={**VALID, "amount_inr": -5}, headers=SCORER_H)
        leak = "/etc/" in str(err.json()) or "traceback" in str(err.json()).lower()
        findings.append(
            _f("ERROR-LEAKAGE", "negative amount -> validation error body",
               f"HTTP {err.status_code}, internal_paths_leaked={leak}", "MEDIUM", set())
        )
        findings[-1]["mitigated"] = not leak

    print("# Security probe results (v2)\n")
    open_count = 0
    for f in findings:
        is_http = str(f["observed"]).split()[0] in {"HTTP"}
        codes = f.pop("_codes", set())
        code_hit = any(str(code) in f["observed"] for code in codes)
        mitigated = f.get("mitigated") or (is_http and code_hit)
        verdict = "MITIGATED" if mitigated else "OPEN"
        if verdict == "OPEN" and f["id"] == "RATELIMIT-MISSING":
            verdict = "BY-DESIGN"
        if verdict == "OPEN":
            open_count += 1
        print(f"[{f['severity']:8s}] [{verdict:9s}] {f['id']}: {f['observed']}")
    print(f"\nopen_findings={open_count}")
    return 0


def _f(fid: str, probe: str, observed: str, severity: str, mitigated_codes: set) -> dict:
    return {
        "id": fid,
        "probe": probe,
        "observed": observed,
        "severity": severity,
        "_codes": mitigated_codes,
    }


if __name__ == "__main__":
    raise SystemExit(main())
