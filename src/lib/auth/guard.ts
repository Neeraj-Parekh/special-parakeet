// G7 — Per-route auth guard (the FastAPI Depends() equivalent).
//
// Next.js middleware runs on the edge runtime where node:crypto + the
// Prisma client are unavailable. So JWT verification stays per-route
// in the nodejs runtime, matching the existing check_key() pattern.
// The src/middleware.ts handles edge-safe concerns only (security
// headers for SEC-2).
//
// Usage in a protected route:
//   const claims = await requireScope(req, "score"); // throws AuthError on failure
//   // claims.sub, claims.scope available

import { NextRequest, NextResponse } from "next/server";
import { verifyAccessToken, bearerFrom, type AccessClaims, type Scope } from "./jwt";

export class AuthError extends Error {
  constructor(
    public readonly code: "missing" | "invalid" | "insufficient",
    message: string,
  ) {
    super(message);
  }
}

/** Verify the Bearer token and require the given scope. */
export async function requireScope(
  req: NextRequest,
  scope: Scope | Scope[],
): Promise<AccessClaims> {
  const token = bearerFrom(req.headers.get("authorization"));
  if (!token) {
    throw new AuthError("missing", "Authorization Bearer token required");
  }
  const claims = await verifyAccessToken(token);
  if (!claims) {
    throw new AuthError("invalid", "access token invalid or expired");
  }
  const required = Array.isArray(scope) ? scope : [scope];
  const has = required.some((s) => claims.scope.includes(s));
  if (!has) {
    throw new AuthError(
      "insufficient",
      `scope ${required.join(" or ")} required (have: ${claims.scope.join(", ")})`,
    );
  }
  return claims;
}

/** Turn an AuthError into the appropriate 401/403 Response. */
export function authErrorResponse(err: AuthError): NextResponse {
  const status = err.code === "insufficient" ? 403 : 401;
  return NextResponse.json(
    {
      detail: err.message,
      code: err.code,
      // RFC 6750 Bearer error.
      error:
        err.code === "insufficient" ? "insufficient_scope" : "invalid_token",
    },
    {
      status,
      headers: {
        "WWW-Authenticate": `Bearer error="${err.code === "insufficient" ? "insufficient_scope" : "invalid_token"}"`,
        "Cache-Control": "no-store",
      },
    },
  );
}

/** Convenience wrapper: try requireScope, on failure return the Response. */
export async function withScope<T>(
  req: NextRequest,
  scope: Scope | Scope[],
  handler: (claims: AccessClaims) => Promise<NextResponse | Response | T>,
): Promise<NextResponse | Response | T> {
  try {
    const claims = await requireScope(req, scope);
    return await handler(claims);
  } catch (err) {
    if (err instanceof AuthError) return authErrorResponse(err);
    throw err;
  }
}
