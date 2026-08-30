// POST /api/v1/webhooks/razorpay
//
// Receives a Razorpay webhook, verifies the HMAC-SHA256 signature
// using constant-time comparison, and dispatches the event to a
// handler. Always returns HTTP 200 to Razorpay so the webhook
// doesn't retry — unknown events are ack'd but flagged for the
// operator.
//
// Security:
//   1. The signature is HMAC-SHA256 over the RAW body bytes (NOT the
//      parsed JSON). The route reads `await req.text()` BEFORE
//      parsing to capture the exact bytes Razorpay signed.
//   2. `crypto.timingSafeEqual` is used for the comparison to
//      prevent timing attacks (a !== b check would leak the position
//      of the first mismatched byte).
//   3. The secret comes from `RAZORPAY_WEBHOOK_SECRET` env var. If
//      the env var is unset (hackathon), the verifier mock-accepts
//      the webhook AND sets `X-Mock-Mode: true` + logs a warning —
//      production MUST have the secret set.
//
// Handled events:
//   • payment.captured   → mark the order as prepaid, clear the COD RTO risk.
//   • payment.failed     → flag the order for follow-up; the merchant's
//                          checkout flow decides whether to retry or cancel.
//   • refund.processed   → close the order's RTO risk window; the refund
//                          implies the order is closed.
//   • (unknown events)   → ack 200, log for the operator.

import { NextRequest, NextResponse } from "next/server";
import {
  verifySignature,
  processEvent,
  type RazorpayWebhookBody,
} from "@/lib/integrations/razorpay-webhook";

export const runtime = "nodejs";

/**
 * POST — receive + verify + dispatch a Razorpay webhook.
 *
 * Razorpay retries on any non-2xx response. The route MUST ack 200
 * even for unknown events to avoid the retry storm. The handler
 * result is returned in the JSON body so the caller (and an operator
 * reading logs) can see what happened.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  // Step 1 — capture the RAW body bytes Razorpay signed. Do NOT
  // parse JSON before this; the HMAC is computed over the wire bytes.
  const rawBody = await req.text();

  // Step 2 — verify the signature.
  const signature = req.headers.get("x-razorpay-signature");
  const secret = process.env.RAZORPAY_WEBHOOK_SECRET ?? null;

  const verify = verifySignature(rawBody, signature, secret);

  // Step 3 — on invalid signature, reject with 400. Razorpay will
  // retry; if the retry still fails, it's a misconfigured secret and
  // the operator gets paged by the dead-letter queue.
  if (!verify.valid) {
    console.warn(
      `[razorpay-webhook] rejected: ${verify.reason}`,
    );
    return NextResponse.json(
      { detail: "invalid signature", reason: verify.reason },
      { status: 400 },
    );
  }

  // Step 4 — parse the body and dispatch. JSON parse errors are
  // non-fatal: ack 200 so Razorpay doesn't retry, but flag the event
  // for the operator.
  let body: RazorpayWebhookBody;
  try {
    body = JSON.parse(rawBody) as RazorpayWebhookBody;
  } catch {
    console.warn(
      `[razorpay-webhook] body not JSON (len=${rawBody.length})`,
    );
    return NextResponse.json(
      { detail: "body not JSON", handled: false },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    );
  }

  // Step 5 — dispatch the event.
  const result = processEvent(body);

  if (verify.mock) {
    console.warn(
      "[razorpay-webhook] RAZORPAY_WEBHOOK_SECRET not set — mock-accepting. " +
        "Production MUST set the env var.",
    );
  }
  console.info(
    `[razorpay-webhook] event=${result.event} handled=${result.handled} ` +
      `payment_id=${result.payment_id ?? "-"} note=${result.note}`,
  );

  const headers: Record<string, string> = { "Cache-Control": "no-store" };
  if (verify.mock) {
    headers["X-Mock-Mode"] = "true";
  }

  return NextResponse.json(
    {
      received: true,
      handled: result.handled,
      event: result.event,
      payment_id: result.payment_id,
      refund_id: result.refund_id,
      amount: result.amount,
      status: result.status,
      note: result.note,
      mock: verify.mock,
    },
    { status: 200, headers },
  );
}
