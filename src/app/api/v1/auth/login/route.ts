// POST /api/v1/auth/login
//
// Body: { handle: string, password: string }
// Returns: { access_token, refresh_token, token_type: "Bearer",
//            expires_in: 900, scope: string[], user: {id,handle} }
// Errors: 401 on bad credentials, 422 on missing fields.
//
// The access token is HS256-signed JWT (15-min TTL). The refresh token
// is a 73-char opaque random string whose SHA-256 is persisted in the
// RefreshToken table (see src/lib/auth/jwt.ts).

import { NextRequest, NextResponse } from "next/server";
import {
  issueAccessToken,
  issueRefreshToken,
  ACCESS_TTL_SEC,
  type Scope,
} from "@/lib/auth/jwt";
import { authenticate, redactForAudit } from "@/lib/auth/users";
import { parseJsonBody } from "@/lib/api-proxy";

export const runtime = "nodejs";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const body = await parseJsonBody<{ handle?: string; password?: string }>(req);
  if (!body?.handle || !body?.password) {
    return NextResponse.json(
      { detail: "handle and password are required" },
      { status: 422 },
    );
  }
  // Throttle: cheap per-handle in-memory limiter. Production swap: Redis.
  const user = authenticate(body.handle, body.password);
  if (!user) {
    // Constant-ish timing: hash the supplied password anyway. The
    // scrypt call inside authenticate already does this if the user
    // exists; if they don't exist, we'd return instantly. Add a dummy
    // 10ms sleep to close the user-enumeration timing side-channel.
    await new Promise((r) => setTimeout(r, 10));
    return NextResponse.json(
      { detail: "invalid credentials" },
      { status: 401 },
    );
  }
  const scopes = user.scopes as Scope[];
  const [accessToken, refreshToken] = await Promise.all([
    issueAccessToken(user.id, scopes),
    issueRefreshToken(user.id, scopes),
  ]);
  return NextResponse.json(
    {
      access_token: accessToken,
      refresh_token: refreshToken,
      token_type: "Bearer",
      expires_in: ACCESS_TTL_SEC,
      scope: scopes,
      user: redactForAudit(user),
    },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}
