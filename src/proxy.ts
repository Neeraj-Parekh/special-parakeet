// Edge proxy — security headers (SEC-2 part 1) + cold-start throttle
// (SEC-5).
//
// Next.js 16 renamed `middleware.ts` → `proxy.ts` (the middleware
// convention is deprecated and silently kills the process on
// request). This file is the renamed replacement.
//
// This runs on the Edge runtime, so it cannot import node:crypto or
// Prisma. It only manipulates headers + does cheap counting. The
// actual JWT verification + scope enforcement happens per-route via
// src/lib/auth/guard.ts (nodejs runtime).
//
// Headers set here:
//   Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
//   X-Content-Type-Options: nosniff
//   X-Frame-Options: DENY
//   Referrer-Policy: strict-origin-when-cross-origin
//   Permissions-Policy: geolocation=(), microphone=(), camera=()
//   Cross-Origin-Opener-Policy: same-origin
//
// SEC-5 cold-start throttle: first 60s after process boot, the
// /api/risk/score path is capped at 10 req/s. Over-budget requests
// get 429 with Retry-After. This is the #1 serverless attack vector
// (cold-start flooding — see worklog §17 feature-poisoning). The
// counter is per-instance in-memory; in multi-replica deployments,
// the real cap is enforced by a Redis token bucket (documented in
// docs/SECURITY_HARDENING.md §8).

import { NextResponse, type NextRequest } from "next/server";

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|gif|svg|webp|ico|css|js|map)).*)",
  ],
};

const BOOT_EPOCH = Date.now();
const COLD_START_WINDOW_MS = 60_000;
const COLD_START_RPS_CAP = 10;

// In-process rolling-window counter (60 buckets of 1s each).
const buckets = new Array(60).fill(0);
let bucketHead = Math.floor(Date.now() / 1000) % 60;

function rollWindow(now: number): number {
  const sec = Math.floor(now / 1000);
  const head = sec % 60;
  // Zero-fill buckets we've passed since last call.
  let diff = (head - bucketHead + 60) % 60;
  while (diff > 0) {
    bucketHead = (bucketHead + 1) % 60;
    buckets[bucketHead] = 0;
    diff--;
  }
  return buckets.reduce((a, b) => a + b, 0);
}

function recordHit(now: number): void {
  rollWindow(now);
  const head = Math.floor(now / 1000) % 60;
  buckets[head] += 1;
}

export function proxy(req: NextRequest): NextResponse {
  const res = NextResponse.next();
  // SEC-2 — security headers on every response.
  res.headers.set(
    "Strict-Transport-Security",
    "max-age=63072000; includeSubDomains; preload",
  );
  res.headers.set("X-Content-Type-Options", "nosniff");
  res.headers.set("X-Frame-Options", "DENY");
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  res.headers.set(
    "Permissions-Policy",
    "geolocation=(), microphone=(), camera=()",
  );
  res.headers.set("Cross-Origin-Opener-Policy", "same-origin");

  // SEC-5 — cold-start throttle on the hot path.
  const path = req.nextUrl.pathname;
  const now = Date.now();
  if (path === "/api/risk/score" && now - BOOT_EPOCH < COLD_START_WINDOW_MS) {
    const inFlight = rollWindow(now);
    if (inFlight >= COLD_START_RPS_CAP) {
      return new NextResponse(
        JSON.stringify({
          detail:
            "cold-start throttle RULE-005: service warming up, retry shortly",
          rule_id: "RULE-005",
          retry_after: 5,
        }),
        {
          status: 429,
          headers: {
            "Content-Type": "application/json",
            "Retry-After": "5",
            "Cache-Control": "no-store",
            "Strict-Transport-Security":
              "max-age=63072000; includeSubDomains; preload",
            "X-Content-Type-Options": "nosniff",
          },
        },
      );
    }
    recordHit(now);
  }
  return res;
}
