// POST /api/v1/auth/refresh
//
// Body: { refresh_token: string }
// Returns: { access_token, refresh_token, token_type, expires_in, scope }
// Errors:
//   401 invalid / expired refresh token
//   401 + reason:"compromised" — a stolen-rotated token was replayed;
//     the entire family has been nuked. The client must re-authenticate.
//
// This implements refresh-token ROTATION (RFC 6749 §10.4): every
// successful refresh issues a NEW refresh token AND revokes the old
// one. If a revoked token is replayed, the whole family is marked
// compromised and every refresh in the chain dies — the textbook
// defense against refresh-token theft.

import { NextRequest, NextResponse } from "next/server";
import {
  issueAccessToken,
  rotateRefreshToken,
  ACCESS_TTL_SEC,
  type Scope,
} from "@/lib/auth/jwt";
import { parseJsonBody } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await parseJsonBody<{ refresh_token?: string }>(req);
  if (!body?.refresh_token) {
    return NextResponse.json(
      { detail: "refresh_token is required" },
      { status: 422 },
    );
  }
  const result = await rotateRefreshToken(body.refresh_token);
  if (!result.ok) {
    const status =
      result.reason === "compromised" ? 401 : result.reason === "expired" ? 401 : 401;
    return NextResponse.json(
      {
        detail: `refresh failed: ${result.reason}`,
        reason: result.reason,
        ...(result.familyId ? { family_id: result.familyId } : {}),
      },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  }
  const accessToken = await issueAccessToken(result.subject, result.scopes);
  return NextResponse.json(
    {
      access_token: accessToken,
      refresh_token: result.newRaw,
      token_type: "Bearer",
      expires_in: ACCESS_TTL_SEC,
      scope: result.scopes as Scope[],
    },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}
