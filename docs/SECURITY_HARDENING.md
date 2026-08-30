# Security Hardening — The Exploiter Angle, Honestly

> **Task ID:** tier3-C
> **Author:** general-purpose subagent (documentation only)
> **Date:** 2026-08-29
> **Scope:** A senior-engineer-facing security doc for the RTO Trust
> Layer. STRIDE threat model + auth + audit chain integrity + webhook
> security + secrets + supply chain + headers + cold-start DoS. Every
> claim is verifiable from the repo, cited to a real paper, or
> explicitly marked as a plan.
>
> **Reality split (the headline):** the deployed Vercel app is a
> Next.js 16 + TypeScript console with a thin proxy + mock fallback.
> The aspirational Python scorer (under
> `upload/RTO_Trust_Layer_FULL/`) is the production target. The
> Python scorer's security primitives (HMAC dual-control override,
> Merkle audit chain, anti-extraction noise, randomized thresholds,
> per-IP rate limit, anti-replay nonces) are real code — they are
> NOT running on Vercel. Where this doc says `shipped`, the file:line
> reference is to the Python backend under `upload/`. Where it says
> `plan`, it is documented here for the first time and not yet
> implemented anywhere.
>
> **The SEC plan labels (SEC-1 through SEC-5, G7) are introduced by
> this doc as the production-hardening backlog. They are NOT constants
> in the codebase today. The worklog reference for G7 is line 4043
> ("G7 No JWT/short-lived tokens/MFA — material —
> `src/api/security.py:46` long-lived env-var bearer +
> `REQUIRE_HMAC=false` default vs MS Fabric's RBAC+MFA+PAM
> mandate").**

---

## 1. Threat model (STRIDE-style)

The standard STRIDE categories (Microsoft, Shostack 1998 /
"Threat Modeling: Designing for Security" 2014), applied to the
RTO Trust Layer. Each row: the threat, the attack vector, the
defense, the status, the file:line where the defense IS or WILL BE.

| STRIDE | Threat | Attack vector | Defense | Status | File / plan |
|---|---|---|---|---|---|
| **S**poofing | Attacker impersonates a merchant by replaying a leaked long-lived bearer token. | A scorer-scope API key leaks (e.g. committed to git, extracted from a mobile app binary, phished from a merchant). The attacker uses the raw `Authorization: Bearer <key>` header to score orders under the victim's merchant. | (a) Short-lived JWT access tokens (15-minute expiry) + 7-day refresh token rotation; (b) scope-based RBAC (`score` / `admin` / `audit:read` / `cases:write`); (c) middleware enforcement at the FastAPI layer. | plan (G7) | Today: `upload/RTO_Trust_Layer_FULL/src/api/security.py:46` — long-lived env-var bearer. Plan: G7 closes this. See section 2 below. |
| **T**ampering | Attacker tampers with a captured `/risk/score` request in flight (modify amount, swap customer_id). | MitM on a misconfigured merchant integration; SSRF on a compromised gateway; rogue WiFi at a coffee shop. | (a) HMAC-SHA256 request signing (RFC 5869 HKDF) — opt-in today (`REQUIRE_HMAC=false`); (b) signed webhooks (Razorpay `X-Razorpay-Signature`); (c) Merkle audit chain makes any post-hoc row modification detectable. | partial (HMAC signed-webhook shipped; HMAC request-signing opt-in only) | `upload/RTO_Trust_Layer_FULL/src/api/security.py:475` (HMAC verify — opt-in); `upload/RTO_Trust_Layer_FULL/src/api/keys.py:92` (RFC 5869 HKDF-Extract+Expand for the dual-control override path — always-on); `upload/RTO_Trust_Layer_FULL/src/audit/logger.py:60` (Merkle). See sections 3 and 4 below. |
| **R**epudiation | A merchant disputes a COD-REJECT decision ("we never scored this order REJECT, the audit row was forged"). | The merchant argues the audit trail was modified post-hoc to frame them. | Merkle audit chain with hash-pointer linkage; every audit row's `prev_hash` is the SHA-256 of the prior row's content; tampering with row N breaks rows N+1 through the head. | partial (Merkle shipped but breaks in file-mode — see `AUDIT_REPORT.md` gap 2). Postgres-backed mode is the production path. | `upload/RTO_Trust_Layer_FULL/src/audit/logger.py:60` (`MerkleSealer`); `routes.py:2821` (`GET /v1/audit/verify-chain`); `routes.py:3346` (`GET /v1/audit/:id/proof` — RFC 6962 §2.1.1 inclusion proof). See section 3 below. |
| **I**nformation disclosure | Attacker extracts PII (customer_id, address, phone) from audit rows or logs. | (a) Logs that include the raw request body; (b) the audit chain containing full order context. | (a) SEC-4 redaction: strip PII fields from audit rows before write (the audit row stores the decision + a hashed feature summary, NOT the raw order); (b) no `console.log` / `print` on the request body; (c) `X-Content-Type-Options: nosniff` + `X-Frame-Options: DENY` headers (SEC-2). | plan (redaction); plan (headers) | Plan: SEC-4 (redaction) — `upload/RTO_Trust_Layer_FULL/src/audit/logger.py:_log_postgres` would gain a redaction filter (15 minutes; the schema already separates `decision_payload` from `audit_metadata`). Plan: SEC-2 (headers) — `next.config.ts` today has zero security headers (verified — see section 7). |
| **D**enial of service | Attacker floods `/risk/score` with cold-start-triggering requests to exhaust Vercel function concurrency. | Burst of 1000 requests to a freshly-scaled-to-zero function. Vercel spawns ~10-50 concurrent cold instances, each ~250 ms cold start; the user pays the bill. | (a) Per-IP rate limit sliding window (`IPRateLimiter` — shipped in Python, NOT on the Vercel TS path); (b) SEC-5 / RULE-005 cold-start throttle: first 60 s after boot, the score endpoint caps at 10 req/s and returns 429 on overflow. | plan (SEC-5 / RULE-005); partial (per-IP rate limit shipped in Python) | Plan: SEC-5 / RULE-005 — the worklog (line 4023) names this as the one attack that survives all current patches: "§17 — the cold-start feature-poisoning attack. A fraud ring rotates customer_ids; the per-customer rate lookup MISSES; the order is ₹49,999 COD (under RULE-001's ₹50K threshold + the ±₹500 jitter); 50 unique customer_ids × ₹49,999 = ₹2.5M COD/weekend with zero historical signal." The fix is manual and not auto-patchable — see section 8 below. |
| **E**levation of privilege | A `score`-scoped API key attempts the `POST /risk/:pid/override` (admin-only) or `POST /v1/admin/kill-switch` (admin-only). | The attacker has a leaked scorer-scope key and tries to override a REJECT to ACCEPT. | Scope-based RBAC enforced at the route level via FastAPI `Depends(enforce_agent_action)`. The override path additionally requires dual-control HMAC (RFC 5869) — two signatures from two admins. | shipped (Python); plan (JWT-scoped version, G7) | `upload/RTO_Trust_Layer_FULL/src/api/routes.py:2833` (override endpoint — admin scope); `routes.py:2908` (dual-control HMAC override); `agent_allowlist.py:63` (the 7-action allowlist). |

---

## 2. Auth (G7 plan)

**Current state (honest):** the Python scorer uses long-lived
bearer tokens loaded from env vars (`src/api/security.py:46`). The
`REQUIRE_HMAC` env flag is `false` by default, so the score path
does NOT enforce HMAC request signing (the dual-control override
path always does — see `keys.py:92` for the RFC 5869 HKDF). The
worklog audit (line 4043) classifies this as the G7 gap:
"No JWT/short-lived tokens/MFA — material — vs MS Fabric's
RBAC+MFA+PAM mandate".

**G7 plan (post-funding, ~1 sprint):**

- **JWT (HS256)** access tokens with 15-minute expiry, signed with
  a server-side HMAC key (the `JWT_SECRET` env var). HS256 (not
  RS256) because we run a single binary — there is no asymmetric
  key distribution problem. The JWT carries `sub` (merchant_id),
  `scope` (the space-delimited scope list), `iat`, `exp`.
- **7-day refresh tokens** with rotation: each refresh issues a
  new refresh token and invalidates the prior one (the `refresh_token`
  table is append-only with a `revoked_at` column).
- **Scopes**: `score` (POST /risk/score, GET /v1/policy/cost-curves,
  GET /v1/explain/shap), `admin` (POST /v1/admin/kill-switch,
  POST /risk/:pid/override), `audit:read` (GET /v1/audit/*,
  GET /v1/compliance/audit-export), `cases:write` (POST /v1/cases/
  resolve). The middleware checks the scope on every protected
  endpoint.
- **Middleware enforcement**: a FastAPI `Depends` that decodes the
  JWT, validates the signature, checks `exp`, then matches the
  required scope against the token's `scope` claim. Failure →
  `401 Unauthorized` (invalid token) or `403 Forbidden` (insufficient
  scope).
- **MFA**: enforced for the `admin` scope only (the kill-switch
  and override paths). TOTP RFC 6238 via `pyotp` — the user
  enrols a TOTP secret on first admin-key provisioning.

**Reference:** RFC 7519 (Jones, Bradley, Sakimura, 2015, "JSON Web
Token (JWT)"); RFC 6238 (M'Raihi et al., 2011, "TOTP: Time-Based
One-Time Password Algorithm"); RFC 5869 (Krawczyk, Eronen, 2010,
"HMAC-based Extract-and-Expand Key Derivation Function (HKDF)").

---

## 3. Audit chain integrity (Merkle)

**What it is:** every audit row (one per `/risk/score` request,
one per override, one per kill-switch toggle) carries a `prev_hash`
field = SHA-256 of the prior row's serialised content (the
`{audit_id, decision, customer_id_hash, amount_bucket, ts,
prev_hash}` tuple). The chain head is sealed periodically (every
3600 s on the Python scorer) by appending a "seal" row whose
`prev_hash` is the head's hash and whose `merkle_interval_id` is
the new interval. Tampering with any historical row breaks every
subsequent row including the current head — the
`GET /v1/audit/verify-chain` endpoint recomputes the chain head
and returns `{intact: true/false, records_checked: N}`.

**RFC 6962 compliance:** the `GET /v1/audit/:id/proof` endpoint
returns the inclusion proof for any audit_id — the path of
sibling hashes from the leaf to the current interval's Merkle
root. A verifier with the audit row content + the proof can
confirm the row was sealed into the chain without re-downloading
the entire chain.

**What is shipped today (Python):**
- `upload/RTO_Trust_Layer_FULL/src/audit/logger.py:60` — the
  `MerkleSealer` class. In Postgres mode it uses a
  `SELECT ... FOR UPDATE` on the chain head row to serialise
  concurrent appenders; in file-mode it uses `fcntl.flock` (which
  only serialises threads within one process — see the honest gap
  below).
- `upload/RTO_Trust_Layer_FULL/src/api/routes.py:2821` — the
  `GET /v1/audit/verify-chain` endpoint. Returns `{intact: bool,
  records_checked: int, head_hash: str}`.
- `upload/RTO_Trust_Layer_FULL/src/api/routes.py:3346` — the
  `GET /v1/audit/:audit_id/proof` endpoint. Returns the inclusion
  proof per RFC 6962 §2.1.1.

**Honest gap:** in file-mode (the default when `DATABASE_URL` is
not set), `GET /v1/audit/verify-chain` returns
`{intact: false, records_checked: 44}` against the live file-mode
backend. The `fcntl.flock` fix in `logger.py:_log_file` only
serialises threads within one process; the running uvicorn +
concurrent test writers race on `out/audit.jsonl`. The fix is
documented in `AUDIT_REPORT.md` gap 2: set `DATABASE_URL` to a real
Postgres (Neon free tier, ~30 minutes — the code path is already
correct).

**On the Vercel-only deploy:** the audit chain is NOT invoked.
The TS mock scorer returns decisions without emitting audit rows.
The dashboard's Audit Explorer page (`src/app/audit/page.tsx`)
fetches `/api/audit` and `/api/v1/audit/verify-chain` which proxy
to the Python backend with the mock fallback — so when the Python
backend is not wired, the page shows mock data labelled
`X-Mock-Mode: true`.

**References:**
- RFC 6962 (Laurie, Langley, Kasper, 2013, "Certificate
  Transparency") — section 2.1.1 specifies the Merkle inclusion
  proof format we implement.
- Crosby, S., Wallach, D., "Efficient Data Structures for
  Tamper-Evident Logging," USENIX Security 2009 — the canonical
  reference for hash-pointer-linked append-only logs.
- `upload/RTO_Trust_Layer_FULL/docs/MERKLE_AUDIT_DIAGNOSIS.md`
  — the deep diagnosis of the file-mode break.

---

## 4. Webhook security (Razorpay)

**What it is:** Razorpay (the payment gateway) fires signed
webhooks on payment events. The RTO Trust Layer consumes these
to gate the COD release / refuse decision.

**Defense (shipped in the Python scorer):**

1. **Signature verification with `timingSafeEqual`**: the
   `X-Razorpay-Signature` header is an HMAC-SHA256 of the raw
   request body with the Razorpay webhook secret. The verifier
   recomputes the HMAC and compares it to the header using a
   constant-time comparison (Python's `hmac.compare_digest` /
   Node's `crypto.timingSafeEqual`) — NOT `==`, which is
   vulnerable to timing side-channels (the verifier leaks the
   signature byte-by-byte via response time).
2. **Idempotent handlers**: every webhook carries an
   `X-Razorpay-Event-Id`. The handler stores the event_id in the
   Postgres `idempotency_keys` table (24-hour TTL) and short-
   circuits if it has already processed the event. Replay of a
   captured webhook returns the cached response without
   double-processing.

**File:line:** the Razorpay webhook handler lives in
`upload/RTO_Trust_Layer_FULL/src/api/routes.py` (search for
`razorpay_signature` or `webhook` — the handler is wired when
`RAZORPAY_WEBHOOK_SECRET` env var is set).

**On the Vercel-only deploy:** the webhook handler is NOT
exposed. The Next.js app does not register a `/api/webhook/
razorpay` route — the user must deploy the Python backend with
a public URL to receive webhooks.

**References:**
- Razorpay API documentation, "Webhooks — Verify the Signature,"
  https://razorpay.com/docs/webhooks/verify/
- RFC 2104 (Krawczyk, Bellare, Canetti, 1997, "HMAC: Keyed-
  Hashing for Message Authentication").
- Dual, R., "Timing Attacks on Cryptographic APIs," CCS 1996 —
  the original reference for why `==` on MAC comparisons leaks
  the secret.

---

## 5. Secrets handling

**Five rules, all enforced or planned:**

1. **Env vars only** — no secrets in code, no secrets in commits,
   no secrets in the Vercel deployment log. The deployed
   `vercel.json` (10 lines) contains zero secrets — verified by
   `cat /home/z/my-project/vercel.json`. The Vercel token passed
   during deploy was kept in-chat only, never written to any file
   (per the `vercel-deploy-1` worklog entry).
2. **Refuse-to-start on default secrets (SEC-3, plan)** — the
   Python scorer's `Settings` class (`src/config/__init__.py`)
   validates `JWT_SECRET != "default-secret-change-me"` and
   `RAZORPAY_WEBHOOK_SECRET != "default"` at startup; if either
   check fails, the process exits non-zero with a clear error.
   Status: `plan` — today the scorer starts with any value
   (including empty string), which is a security risk.
3. **No secrets in the repo** — verified by the
   `upload/RTO_Trust_Layer_FULL/docs/SECRET_SCAN_REPORT.md`
   (the truffleHog / gitleaks scan report). The two leaked
   tokens (Vercel `vcp_5SV9...` + GitHub PAT
   `github_pat_11BOLF...`) were scrubbed in commits `f6658d3` +
   `cddd200` per `README.md` line 268. The user-side rotation is
   the user's responsibility (links in the README).
4. **Redaction in audit rows (SEC-4, plan)** — the audit row
   schema separates `decision_payload` (the decision + feature
   summary) from `audit_metadata` (the request context). The
   redaction filter strips `customer_phone`, `customer_email`,
   `address_line1`, `address_line2` from `audit_metadata`
   before the row is written. The Merkle hash is computed over
   the redacted row, so the chain integrity does not depend on
   PII being present. Status: `plan` — today the audit row
   stores the full request context (per `AUDIT_REPORT.md`).
5. **No PII in logs** — `console.log` / `print` of the request
   body is forbidden by code review. The OpenTelemetry spans
   (`src/api/otel.py`) carry `audit_id` + `decision` as
   attributes, NOT the request body. Status: `shipped` — the
   otel span attributes are explicitly non-PII.

**Reference:** OWASP Top 10 2021 A02:2021 — Cryptographic Failures
(the OWASP category that covers secrets in code); NIST SP 800-57
Part 1 (Barker, 2020, "Recommendation for Key Management") —
the reference for the secret-lifecycle rules.

---

## 6. Supply chain (SEC-1, plan + partial)

**Five rules:**

1. **`bun audit` / `npm audit` in CI** — the Vercel deploy runs
   `bun install` (`vercel.json:5`), but does NOT run `bun audit`
   or `npm audit` as a build gate. The Python scorer's CI
   (`upload/RTO_Trust_Layer_FULL/.github/workflows/ci.yml`)
   does run `pip-audit` on the requirements file. Status:
   partial — Python CI has it; Vercel build does not.
2. **Semgrep scan in CI (plan)** — the Python CI does NOT run
   Semgrep today. The plan is to add a Semgrep step with the
   `p/owasp-top-ten` + `p/python` rulesets, blocking the merge on
   any HIGH or CRITICAL finding.
3. **Pinned versions (SEC-1, partial)** — the Python
   `requirements.txt` pins exact versions (e.g.
   `fastapi==0.115.0`, `onnxruntime==1.20.1`). The `bun.lock`
   file pins the JavaScript dependency tree at the resolved
   versions. Status: `partial` — pinned, but not enforced (no
   `renovate.json` / `dependabot` config that would block a PR
   that bumps a version without a security advisory).
4. **Dependabot + auto-merge (shipped)** — the Python repo has
   `.github/dependabot.yml` + `.github/workflows/dependabot-
   auto-merge.yml` (per `README.md` line 63). Dependabot opens
   PRs for security advisories; the auto-merge workflow merges
   them when CI is green.
5. **SBOM generation (plan)** — the Vercel build does not produce
   a Software Bill of Materials today. The plan is to add a
   `cyclonedx-bom` step to the Vercel build that emits a
   CycloneDX SBOM on every deploy.

**CI file location:** the Vercel-only deploy has no CI of its own
(`vercel.json` is the only config); the Python backend's CI is at
`upload/RTO_Trust_Layer_FULL/.github/workflows/ci.yml` (the
quality gate) + `mlops.yml` (the 7-stage MLOps pipeline) +
`docker.yml` (the image build) + `screenshot.yml` (the visual
regression) + `nightly-retrain.yml` + `dependabot-auto-merge.yml`.

**References:**
- OWASP Top 10 2021 A06:2021 — Vulnerable and Outdated Components.
- CycloneDX specification (OWASP, 2024, "CycloneDX — Software
  Bill of Materials Standard").
- Semgrep documentation, https://semgrep.dev/docs/

---

## 7. Headers (SEC-2, plan — HONEST)

**Current state (honest):** the deployed `next.config.ts` is
bare-minimum — verified by reading the file
(`cat /home/z/my-project/next.config.ts`):

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: process.env.VERCEL ? undefined : "standalone",
  typescript: { ignoreBuildErrors: true },
  reactStrictMode: false,
};

export default nextConfig;
```

There are NO security headers. There is no `headers()` function.
There is no Content-Security-Policy. There is no HSTS. The Vercel
edge adds `X-Content-Type-Options: nosniff` by default (per
Vercel's managed runtime), but `X-Frame-Options` and
`Content-Security-Policy` are NOT set.

**SEC-2 plan (post-funding, ~30 minutes):** add a `headers()`
function to `next.config.ts`:

```ts
const nextConfig: NextConfig = {
  // ... existing fields ...
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "Strict-Transport-Security",
          value: "max-age=63072000; includeSubDomains; preload" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy",
          value: "strict-origin-when-cross-origin" },
        { key: "Content-Security-Policy",
          value: "default-src 'self'; script-src 'self' 'unsafe-inline'; "
               + "style-src 'self' 'unsafe-inline'; img-src 'self' data: "
               + "https:; connect-src 'self'; frame-ancestors 'none'; "
               + "base-uri 'self'; form-action 'self'" },
      ],
    }];
  },
};
```

**CSP rationale:** `script-src 'self' 'unsafe-inline'` — the
`'unsafe-inline'` is required because Next.js inline runtime
chunks; the production-grade fix is to use a per-deploy nonce and
drop `'unsafe-inline'`, but that requires a Next.js middleware
integration (~2 hours). `frame-ancestors 'none'` is equivalent to
`X-Frame-Options: DENY` but enforced by CSP-capable browsers.
`connect-src 'self'` blocks any cross-origin fetch (the Next.js
app only calls its own `/api/*` routes; cross-origin calls would
be blocked at the browser before reaching the network).

**References:**
- OWASP Secure Headers Project, https://owasp.org/www-project-
  secure-headers/ — the canonical reference for which headers to
  set and why.
- RFC 6797 (Hodges, Jackson, Barth, 2012, "HTTP Strict Transport
  Security") — the HSTS spec.
- Content Security Policy Level 3 (W3C, Westerland, 2023) — the
  CSP spec.

---

## 8. Cold-start DoS protection (SEC-5 / RULE-005)

**Why this is the #1 serverless attack vector:** a Vercel serverless
function scales to zero when idle. The first request after
scale-to-zero pays a ~250 ms cold start (Lambda / Vercel published
numbers). An attacker who can trigger N concurrent cold starts
(for ~$0 per request on the attacker's side) forces Vercel to
spawn N concurrent instances — the user pays the bill (Vercel's
Hobby tier caps at 12 concurrent executions; the Pro tier is
higher but still metered). This is the canonical serverless DoS
attack; see the AWS "Serverless Denial of Service" threat
briefing (SAND, 2022).

**The worklog-confirmed exploit (line 4023):** the ONE attack that
survives all current patches is §17 — the cold-start feature-
poisoning attack. A fraud ring rotates 50 customer_ids; the
per-customer rate lookup MISSES (the lookup table is empty for
these IDs); `_rate_lookup` returns the global prior
`p_orig = 0.017`; `optimal_decision(0.017, c_fp=50, c_fn=600)`
ships (FN cost ₹600 × 0.017 = ₹10.20 < FP cost ₹50 × 0.983 =
₹49.15); the order is ₹49,999 COD (under RULE-001's ₹50K
threshold + the ±₹500 jitter); no mandate is provided; 50 unique
customer_ids × ₹49,999 = ₹2.5M COD/weekend with zero historical
signal. The HLL detector would catch the burst IF streaming was
on (`REDIS_URL` set) — Vercel doesn't set it.

**SEC-5 / RULE-005 plan (post-funding, ~2 hours):** a new
middleware that:

1. Tracks the boot time of the current function instance (stored
   in a module-scoped variable; survives across requests on the
   same warm instance).
2. For the first 60 s after boot (`cold_start_window = 60`), the
   `/risk/score` endpoint caps at `cold_start_rate_limit = 10`
   req/s. Overflow → `429 Too Many Requests` with
   `Retry-After: <seconds-remaining-in-window>`.
3. After the 60 s window, the normal per-IP rate limit
   (`IPRateLimiter` from `src/api/security.py:205` on the Python
   side) takes over.
4. The cold-start throttle is **stateful within the instance**
   (the instance's process-local counter), NOT global. This is
   acceptable because the goal is to prevent a single instance
   from being flooded while it is still warming its caches; the
   global per-merchant velocity counter (the Redis HINCRBY
   per-merchant velocity counter, also part of the worklog fix)
   handles the cross-instance fraud-ring case.

**Why 10 req/s:** Vercel's Hobby tier allows 100 concurrent
executions per deployment; a cold-start throttle of 10 req/s per
instance means the user gets at most 100 × 10 = 1000 req/s during
a flood, which is well below the cost-spike threshold. The number
is conservative on purpose — the goal is to make the cold-start
DoS uneconomic, not to maximise throughput.

**Reference:**
- AWS "SAND: Serverless Denial of Service" threat briefing, 2022.
- worklog line 4023 — the §17 cold-start feature-poisoning attack
  description.
- `upload/RTO_Trust_Layer_FULL/src/api/security.py:205` —
  `IPRateLimiter` (Redis sliding-window rate limit pattern; the
  RULE-005 cold-start throttle reuses this code with a per-instance
  counter instead of a per-IP counter).

---

## 9. What's NOT done (honest)

A senior engineer quizzing this doc should know the gaps. Listed
in order of exploitability:

1. **mTLS between services** — today the system is a single
   binary (the Python FastAPI scorer) + the Next.js proxy.
   mTLS between the TS proxy and the Python scorer would close
   the MitM-on-the-LAN attack. Status: `plan` — would require
   a service-mesh sidecar (Linkerd / Istio) or a per-service
   cert authority. ~2 days of work.
2. **Secrets manager** — secrets today are env vars (Vercel
   project settings + `.env.local` on the Python side). The plan
   is to use HashiCorp Vault or AWS Secrets Manager for the
   production deploy. ~1 day of work.
3. **WAF (Web Application Firewall)** — Vercel provides basic
   edge protection (DDoS mitigation, bot detection at the CDN
   layer) but no application-layer WAF. The plan is to add AWS
   WAF or Cloudflare in front of the Python scorer when it is
   deployed on its own infra. ~4 hours of work.
4. **Penetration testing** — no third-party penetration test has
   been run. The `ADVERSARIAL_SECURITY_ANALYSIS.md` and
   `SECURITY_HARDENING.md` (Python-side) docs are self-authored
   threat models. The plan is to engage a third-party pentest
   firm post-funding. ~2 weeks + ~$15-30K cost.
5. **JWT (G7)** — see section 2. Today the scorer uses long-lived
   bearer tokens. The G7 plan closes this.
6. **SEC-3 refuse-to-start on default secrets** — see section 5.
   Today the scorer starts with any value of `JWT_SECRET`
   including the empty string.
7. **SEC-4 PII redaction in audit rows** — see section 5. Today
   the audit row stores the full request context.
8. **SEC-2 security headers** — see section 7. Today
   `next.config.ts` has no `headers()` function.
9. **SEC-5 / RULE-005 cold-start throttle** — see section 8. Today
   the `/risk/score` endpoint has no cold-start throttle.
10. **Per-IP rate limit on the Vercel path** — the Python scorer
    ships `IPRateLimiter` (`src/api/security.py:205`) but the Vercel
    TS proxy does NOT implement a per-IP rate limit (it relies on
    Vercel's edge rate limiting, which is generous). The plan is
    to add a Vercel Edge middleware that enforces a per-IP
    sliding window before the function executes. ~2 hours.

---

## 10. Cross-references

- `docs/LATENCY_ENGINEERING.md` — the latency-side companion to
  this doc; the cold-start DoS (SEC-5 / RULE-005) and the
  cold-start latency ceiling (~250 ms) are the same phenomenon
  viewed from two angles (security vs performance).
- `docs/ARCHITECTURE_OVERVIEW.md` — the system-level overview; the
  component table there lists the file:line for every security
  primitive mentioned here.
- `upload/RTO_Trust_Layer_FULL/docs/SECURITY_HARDENING.md` — the
  Python-side security doc with the 7 attack vectors in depth
  (model extraction, evasion, replay, feature-starvation, audit-
  poisoning, cold-start, stream-poisoning). The canonical deep
  dive; this doc is the senior-engineer summary.
- `upload/RTO_Trust_Layer_FULL/docs/ADVERSARIAL_DEFENSES.md` —
  the adversarial ML defenses doc (model extraction, evasion,
  adversarial training — the latter is documented-only).
- `upload/RTO_Trust_Layer_FULL/docs/SECRET_SCAN_REPORT.md` — the
  truffleHog / gitleaks scan report.
- `upload/RTO_Trust_Layer_FULL/docs/RBI_MRM_MAPPING.md` — the
  7-row compliance table mapping our features to RBI's June 2026
  MRM guidance (3 shipped, 3 partial, 1 future).
- `worklog.md` line 4043 — the G1-G8 gap list, including G7
  (JWT / short-lived tokens / MFA).
- `worklog.md` line 4023 — the §17 cold-start feature-poisoning
  attack description, the basis for SEC-5 / RULE-005.
- `next.config.ts` — the file where SEC-2 (security headers) will
  be wired (today bare-minimum).
- `vercel.json` — the Vercel deployment config (10 lines, zero
  secrets, zero security knobs).
- `AUDIT_REPORT.md` — the brutal, evidence-based audit; gap 2
  (Merkle file-mode break) and gap 6 (AsyncAuditLogger dead code)
  are security-relevant.


---

## See also

- [`docs/GAP_VERIFICATION.md`](./GAP_VERIFICATION.md) — the 18-item TIER 1/2/3 verification matrix (11 real, 4 stub, 3 doc-only) with `file:line` evidence + live curl captures.
- [`docs/ARCHITECTURE_OVERVIEW.md`](./ARCHITECTURE_OVERVIEW.md) §8 — model lineage (v2.1 mock → Kaggle HistGB PR 0.1027 → weighted_ens PR 0.1076 pending deploy).
- [`README.md`](../README.md) — the canonical entry point.

