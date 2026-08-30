# Integrations — Courier / NPCI / Razorpay / ERP

> **G8 — production-credible integration stubs.**
>
> Per the project owner: *"same with NPCI, courier ERP integration,
> we can show we have option to integrate Razorpay etc. stuff."*
>
> This doc explains every integration shipped in this repo, the
> production endpoint it wraps, the env vars needed, the mock
> behavior, and the production-swap path. Each integration has a
> deterministic mock so the demo is stable across reloads.

---

## 1. File map

| File | Integration | API surface |
|---|---|---|
| `src/lib/integrations/shiprocket.ts` | Shiprocket courier serviceability | `validatePincode(pincode)` |
| `src/lib/integrations/delhivery.ts` | Delhivery AWB tracking | `track(awb)` |
| `src/lib/integrations/npci.ts` | NPCI UPI Circle mandate (OC-201B) | `createMandate(input)` |
| `src/lib/integrations/razorpay-webhook.ts` | Razorpay webhook verifier + dispatcher | `verifySignature(body, sig, secret)`, `processEvent(body)` |
| `src/app/api/v1/integrations/shiprocket/validate-pincode/[pincode]/route.ts` | HTTP route | `GET` |
| `src/app/api/v1/integrations/delhivery/track/route.ts` | HTTP route | `POST {awb}` |
| `src/app/api/v1/integrations/npci/mandate/route.ts` | HTTP route | `POST {customer_id, amount_cap_inr, frequency}` |
| `src/app/api/v1/webhooks/razorpay/route.ts` | HTTP route | `POST` (Razorpay-fired) |

The libs are real TypeScript with deterministic mocks. The route
handlers wrap the libs with `NextRequest` / `NextResponse` /
`runtime = "nodejs"` and the project's `parseJsonBody` /
`X-Mock-Mode` header conventions (mirrors
`src/app/api/risk/score/route.ts`).

---

## 2. Shiprocket — courier serviceability

**Production endpoint:**
```
GET https://api.apigenius.in/external/serviceability/
    ?pincode={pincode}&cod={1|0}
Header: Authorization: Bearer {SHIPROCKET_TOKEN}
```

**Env vars:**
- `SHIPROCKET_TOKEN` — Bearer token from the Shiprocket dashboard (Settings → API → Generate Token).
- `SHIPROCKET_BASE_URL` (optional) — override the API base. Default `https://api.apigenius.in`.

**Mock behavior** — `validatePincode()` returns:
```json
{
  "pincode": "560001",
  "cod_available": true,
  "prepaid_available": true,
  "expected_delivery_days": 4,
  "recommended_courier": "Delhivery",
  "mock": true,
  "timestamp": "2026-08-29T12:34:56.789Z"
}
```
The values are derived from a stable FNV-1a 32-bit hash of the
pincode, so reloading the page returns the same serviceability every
time. The hash modulo decides:
- `cod_available` — hash & 0x1
- `expected_delivery_days` — 2 + (hash % 5) → 2..6 days
- `recommended_courier` — `FORWARDERS[hash % 4]`

**Production swap** — replace the body of `validatePincode` with:
```typescript
const res = await fetch(
  `${baseUrl}/external/serviceability/?pincode=${pincode}&cod=1`,
  { headers: { Authorization: `Bearer ${token}` } },
);
return mapShiprocketResponse(await res.json());
```
The function signature + return type stay the same; the route handler
doesn't change.

**Demo endpoint:**
```bash
curl https://rto-trust-layer.vercel.app/api/v1/integrations/shiprocket/validate-pincode/560001
```

---

## 3. Delhivery — AWB tracking

**Production endpoint:**
```
GET https://track.delhivery.com/api/v1/packages/json/?waybill={awb}
Header: Authorization: Token {DELHIVERY_TOKEN}
```

**Env vars:**
- `DELHIVERY_TOKEN` — API token from the Delhivery client panel.
- `DELHIVERY_BASE_URL` (optional) — default `https://track.delhivery.com`.

**Mock behavior** — `track()` returns a 4-stage milestone history
(`picked_up → in_transit → out_for_delivery → delivered`) where the
stage reached is `(hash % 5)` — 0 means just picked up, 4 means
delivered. Each milestone is 6 hours after the previous one so the
history looks realistic on a reload.

```json
{
  "awb": "AWB1234567890",
  "current_status": "in_transit",
  "eta": "2026-09-02",
  "history": [
    { "status": "in_transit", "timestamp": "...", "location": "Mumbai BOW", "remark": "In transit to destination hub" },
    { "status": "picked_up", "timestamp": "...", "location": "Bengaluru BOW", "remark": "Shipment picked up from origin warehouse" }
  ],
  "mock": true,
  "timestamp": "..."
}
```

**Production swap** — replace `track` body with a `fetch()` to the
real Delhivery endpoint, then map `ShipmentData.Scans` to the
`TrackingMilestone[]` shape. Same signature.

**Demo endpoint:**
```bash
curl -X POST https://rto-trust-layer.vercel.app/api/v1/integrations/delhivery/track \
  -H 'Content-Type: application/json' \
  -d '{"awb":"AWB1234567890"}'
```

---

## 4. NPCI — UPI Circle mandate (OC-201B enforcement)

**Production endpoint:**
```
POST https://api.npci.in/upi-circle/v1/mandates
Headers:
  client-id: {NPCI_CLIENT_ID}
  client-secret: {NPCI_CLIENT_SECRET}
  x-signature: {RSA-2048 signature over body}
```

**Env vars:**
- `NPCI_CLIENT_ID` — issuer's NPCI client id.
- `NPCI_CLIENT_SECRET` — issuer's NPCI client secret.
- `NPCI_BASE_URL` (optional) — sandbox or production. Default `https://api.npci.in`.
- `NPCI_SIGNING_KEY` — PEM RSA private key for request signing.

**OC-201B cap enforcement** — `createMandate()` throws BEFORE the
API call if the input violates OC-201B:
- `amount_cap_inr > 50,000` → 422 with `"OC-201B violation: amount_cap_inr ... exceeds max 50000"`
- `frequency !== 'monthly'` → 422 with `"OC-201B violation: frequency ... not in monthly"`

The caps are mirrored from `upload/RTO_Trust_Layer_FULL/src/api/mandates.py:699-705`
(`OC201B_CAPS` constant in `src/lib/integrations/npci.ts`). The
per_txn_cap (₹5,000), cooling period (24h), max_devices (5), and TTL
(180 days) are echoed in every successful response.

**Mock behavior:**
```json
{
  "mandate_id": "NPCI-MND-lq3jx9a1kbc",
  "customer_id": "CUST-REP-7782",
  "amount_cap_inr": 5000,
  "frequency": "monthly",
  "per_txn_cap_inr": 5000,
  "cooling_period_h": 24,
  "max_devices": 5,
  "mandate_ttl_days": 180,
  "status": "ACTIVE",
  "created_at": "2026-08-29T12:34:56.789Z",
  "mock": true
}
```
The `mandate_id` is `NPCI-MND-<timestamp_base36><random>` so it
looks like a real CUID but is generated locally.

**Production swap** — replace the body with a signed `fetch()` POST
to the real NPCI endpoint. The signing is RSA-2048 over the request
body using `NPCI_SIGNING_KEY`. The `OC201B_CAPS` constant +
the pre-call validation MUST stay — these are regulatory caps, not
transport concerns.

**Demo endpoints:**
```bash
# Valid mandate (under ₹50,000 cap)
curl -X POST https://rto-trust-layer.vercel.app/api/v1/integrations/npci/mandate \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"CUST-REP-7782","amount_cap_inr":5000,"frequency":"monthly"}'

# OC-201B violation (over ₹50,000 cap) → 422
curl -X POST https://rto-trust-layer.vercel.app/api/v1/integrations/npci/mandate \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"CUST-REP-7782","amount_cap_inr":60000,"frequency":"monthly"}'
```

---

## 5. Razorpay — webhook signature verifier + event dispatcher

**Production endpoint** — Razorpay fires a POST to YOUR public
webhook URL (configured in the Razorpay dashboard → Settings →
Webhooks). The Trust Layer's receiver is
`/api/v1/webhooks/razorpay`.

**Env vars:**
- `RAZORPAY_WEBHOOK_SECRET` — the secret configured in the Razorpay
  dashboard. NEVER commit. In production it comes from AWS Secrets
  Manager or Vercel project env vars. If unset, the verifier
  mock-accepts the webhook AND sets `X-Mock-Mode: true` + logs a
  warning — production MUST have this set.

**Verification flow:**
1. The route reads the RAW body bytes via `await req.text()` BEFORE
   parsing JSON. Razorpay signs the wire bytes, not the parsed
   object.
2. `verifySignature(rawBody, signature, secret)` computes
   `HMAC-SHA256(rawBody, secret)` and compares the hex digest against
   the `X-Razorpay-Signature` header using `crypto.timingSafeEqual`.
3. Equal-length buffers are required for `timingSafeEqual`; if the
   signature length differs, the verifier runs a dummy compare to
   keep timing constant and returns `valid:false`.
4. On valid signature, the route parses the JSON and calls
   `processEvent(body)` to dispatch.

**Handled events:**
| Event | Action | RTO impact |
|---|---|---|
| `payment.captured` | Mark order prepaid | Clears the COD RTO risk — the order is now prepaid |
| `payment.failed` | Flag order for follow-up | Merchant's checkout decides whether to retry or cancel |
| `refund.processed` | Close the RTO risk window | Refund implies order closed; no RTO loss possible |
| (unknown) | Ack 200, log for operator | Razorpay retries on non-2xx — we MUST ack |

**Sequence diagram (ASCII):**
```
   Razorpay            Trust Layer API          NPCI / DB
       │                     │                      │
       │   POST /webhooks/   │                      │
       │   razorpay          │                      │
       │   (raw body +       │                      │
       │    X-Razorpay-      │                      │
       │    Signature)       │                      │
       │────────────────────▶│                      │
       │                     │                      │
       │                     │  verifySignature(    │
       │                     │    rawBody, sig,     │
       │                     │    secret)           │
       │                     │  ├─ HMAC-SHA256      │
       │                     │  └─ timingSafeEqual  │
       │                     │                      │
       │                     │  processEvent(body)  │
       │                     │  ├─ payment.captured  │
       │                     │  │  → mark prepaid    │
       │                     │  ├─ payment.failed    │
       │                     │  │  → flag follow-up   │
       │                     │  └─ refund.processed  │
       │                     │     → close RTO       │
       │                     │                      │
       │                     │  UPDATE orders SET   │
       │                     │  payment_status=...  │
       │                     │─────────────────────▶│
       │                     │                      │
       │   200 OK            │                      │
       │   {received:true,   │                      │
       │    handled:true,    │                      │
       │    event:...}       │                      │
       │◀────────────────────│                      │
       │                     │                      │
       │  (no retry — 200    │                      │
       │   acked)            │                      │
```

**Mock behavior** — when `RAZORPAY_WEBHOOK_SECRET` is unset, the
verifier returns `{ valid: true, mock: true, reason: "...mock-accept..." }`
and the route sets `X-Mock-Mode: true`. A judge can POST any body
and see the dispatcher work without needing a real Razorpay secret.

**Production swap** — set `RAZORPAY_WEBHOOK_SECRET` in the Vercel
project env vars (matching the secret configured in the Razorpay
dashboard). The code path is identical — the only change is
`verify.mock` flips to `false`.

**Security note** — the verifier deliberately uses `node:crypto`
(standard library), NOT a third-party HMAC lib. This matches the
project's quality bar: no unvetted crypto dependencies.

**Demo endpoint:**
```bash
# Valid body, no secret set → mock-accept (X-Mock-Mode: true)
curl -X POST https://rto-trust-layer.vercel.app/api/v1/webhooks/razorpay \
  -H 'Content-Type: application/json' \
  -H 'X-Razorpay-Signature: dummy' \
  -d '{"event":"payment.captured","payload":{"payment":{"entity":{"id":"pay_demo","amount":240000,"status":"captured"}}}}'

# Missing signature header → 400
curl -X POST https://rto-trust-layer.vercel.app/api/v1/webhooks/razorpay \
  -H 'Content-Type: application/json' \
  -d '{"event":"payment.captured"}'
```

---

## 6. Cross-references

- `docs/MULTI_AZ.md` — the K8s NetworkPolicy in `infra/k8s/multi-az/`
  restricts egress to DB + Kafka only. The integration endpoints
  listed here require their own egress allow-rules in production
  (e.g. add `to: api.npci.in` for the NPCI mandate call).
- `docs/SECURITY_HARDENING.md` — the Razorpay webhook verifier is the
  pattern every future inbound webhook should follow (constant-time
  compare, raw body capture, no third-party crypto).
- `docs/STREAMING_ARCHITECTURE.md` — the `payment.captured` event from
  Razorpay is also published to the streaming seam so the dashboard's
  "Recent Decisions" panel updates in real time.
- `docs/RULE_DSL.md` — the rule DSL can reference the integration
  results (e.g. `pincode NOT_SERVICEABLE` could be a rule predicate in
  a future iteration).
