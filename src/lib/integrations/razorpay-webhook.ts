// src/lib/integrations/razorpay-webhook.ts — Razorpay webhook signature verifier.
//
// G8 — Courier / NPCI / ERP integration stubs.
//
// Production endpoint:
//   Razorpay fires a POST to YOUR public webhook URL (configured in
//   the Razorpay dashboard → Settings → Webhooks). Each webhook carries
//   a `X-Razorpay-Signature` header that is the HMAC-SHA256 of the raw
//   request body keyed with your `RAZORPAY_WEBHOOK_SECRET`.
//
// Production env vars:
//   RAZORPAY_WEBHOOK_SECRET  — the secret configured in the Razorpay
//                              dashboard. NEVER commit this. In
//                              production it comes from AWS Secrets
//                              Manager / Vercel project env vars.
//
// Verifier rules (Razorpay docs §"Verifying the Signature"):
//   1. Compute HMAC-SHA256 over the RAW body bytes (NOT parsed JSON).
//   2. Compare against `X-Razorpay-Signature` using
//      `crypto.timingSafeEqual` to prevent timing attacks.
//   3. Reject with 400 if the signature is missing or doesn't match.
//   4. NEVER use `===` or `Buffer.compare` directly on the hex strings
//      — they short-circuit on the first mismatched byte.
//
// Files to read alongside this: docs/INTEGRATIONS.md (§5 Razorpay
// webhook sequence diagram).

import { createHmac, timingSafeEqual } from "node:crypto";

/** Subset of Razorpay webhook events the Trust Layer handles. */
export type RazorpayEvent =
  | "payment.captured"
  | "payment.failed"
  | "refund.processed";

/** The parsed webhook body — Razorpay wraps the real payload in `payload.payment.entity`. */
export interface RazorpayWebhookBody {
  /** The event type, e.g. "payment.captured". */
  event: RazorpayEvent | string;
  /** Contains the payment entity with `id`, `amount`, `status`, etc. */
  payload?: {
    payment?: {
      entity?: {
        id?: string;
        amount?: number; // in paise
        currency?: string;
        status?: string;
        order_id?: string;
        method?: string;
      };
    };
    refund?: {
      entity?: {
        id?: string;
        amount?: number;
        status?: string;
        payment_id?: string;
      };
    };
  };
}

/** Outcome of a signature verification — caller surfaces the reason. */
export interface VerifyResult {
  /** True iff the HMAC matched. */
  valid: boolean;
  /** Reason for failure (or "ok" on success). */
  reason: string;
  /** Whether the verifier ran in mock-accept mode (no secret set). */
  mock: boolean;
}

/**
 * Verify a Razorpay webhook signature using constant-time comparison.
 *
 * @example
 *   const result = await verifySignature(rawBody, sig, secret);
 *   if (!result.valid) return new Response("bad sig", { status: 400 });
 *
 * @param rawBody   The raw request body bytes (UTF-8 string). Razorpay
 *                  signs the RAW body, not the parsed JSON, so the
 *                  route handler MUST pass `await req.text()` here
 *                  BEFORE parsing JSON.
 * @param signature The value of the `X-Razorpay-Signature` header
 *                  (hex-encoded HMAC-SHA256).
 * @param secret    The `RAZORPAY_WEBHOOK_SECRET` from the env var.
 *                  The caller is responsible for fetching this — the
 *                  verifier doesn't read `process.env` itself so it
 *                  can be unit-tested with explicit inputs.
 */
export function verifySignature(
  rawBody: string,
  signature: string | null | undefined,
  secret: string | null | undefined,
): VerifyResult {
  if (!signature) {
    return {
      valid: false,
      reason: "missing X-Razorpay-Signature header",
      mock: false,
    };
  }
  if (!secret) {
    // Mock-accept path — when the secret is unset (hackathon) we log
    // a warning and accept the webhook so the demo can exercise the
    // event handlers. The route handler MUST set X-Mock-Mode: true
    // so the frontend can badge the experience. In production this
    // branch is unreachable because the secret MUST be set.
    return {
      valid: true,
      reason:
        "RAZORPAY_WEBHOOK_SECRET not set — mock-accept (would reject in production)",
      mock: true,
    };
  }

  const expected = createHmac("sha256", secret).update(rawBody).digest("hex");

  // timingSafeEqual requires equal-length buffers. Two hex strings of
  // different length cannot match — but we still want to run a
  // constant-time compare on the EQUAL-length prefix to avoid leaking
  // the length via the early return. Razorpay's signature is always
  // 64 hex chars (sha256 → 32 bytes → 64 hex).
  if (expected.length !== signature.length) {
    // Run a dummy compare to keep timing constant.
    timingSafeEqual(Buffer.from(expected), Buffer.from(expected));
    return {
      valid: false,
      reason: "signature length mismatch",
      mock: false,
    };
  }

  const valid = timingSafeEqual(
    Buffer.from(signature, "utf8"),
    Buffer.from(expected, "utf8"),
  );

  return {
    valid,
    reason: valid ? "ok" : "signature does not match",
    mock: false,
  };
}

/**
 * Decode the webhook body and dispatch the event to a handler.
 *
 * The route handler (`src/app/api/v1/webhooks/razorpay/route.ts`)
 * calls this AFTER `verifySignature` has passed. The function returns
 * a structured result so the route can log + return 200 ack to
 * Razorpay (Razorpay retries on non-2xx, so we MUST ack even for
 * unknown events).
 *
 * @example
 *   const r = processEvent(body);
 *   // → { handled: true, event: "payment.captured", payment_id: "pay_..." }
 */
export function processEvent(
  body: RazorpayWebhookBody,
): {
  handled: boolean;
  event: RazorpayEvent | string;
  payment_id: string | null;
  refund_id: string | null;
  amount: number | null;
  status: string | null;
  note: string;
} {
  const event = body.event ?? "unknown";
  const paymentEntity = body.payload?.payment?.entity;
  const refundEntity = body.payload?.refund?.entity;
  const paymentId = paymentEntity?.id ?? null;
  const refundId = refundEntity?.id ?? null;
  const amount =
    paymentEntity?.amount ?? refundEntity?.amount ?? null;
  const status = paymentEntity?.status ?? refundEntity?.status ?? null;

  switch (event) {
    case "payment.captured":
    case "payment.failed":
    case "refund.processed":
      return {
        handled: true,
        event,
        payment_id: paymentId,
        refund_id: refundId,
        amount,
        status,
        note: `event ${event} processed`,
      };
    default:
      // Unknown event — ack 200 so Razorpay doesn't retry, but flag
      // it for the operator.
      return {
        handled: false,
        event,
        payment_id: paymentId,
        refund_id: refundId,
        amount,
        status,
        note: `unknown event ${event} — ack'd but not handled`,
      };
  }
}
