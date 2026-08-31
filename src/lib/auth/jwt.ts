// G7 — JWT + short-lived tokens.
//
// HS256-signed access tokens (15-min TTL) + stateful refresh tokens
// (7-day TTL, rotating, with refresh-rotation-attack detection). The
// refresh tokens are persisted in the RefreshToken table so a rotated
// predecessor can be detected and the whole family nuked (the OAuth2
// threat-model mitigation for stolen-refresh-token replay).
//
// Scopes (not flat roles): ["score","admin","audit:read","cases:write"].
// The middleware in src/middleware.ts enforces scope per route.

import { SignJWT, jwtVerify, type JWTPayload } from "jose";
import { createHash, timingSafeEqual } from "node:crypto";
import { db } from "@/lib/db";

// ---------------------------------------------------------------------------
// SEC-3 — Refuse-to-start guard for default/missing secrets.
//
// The runtime must NOT boot if the signing secret is the documented
// default or empty. This is defense-in-depth: even if an operator
// forgets to rotate, the service fails closed rather than signing
// tokens with a publicly-known key.
// ---------------------------------------------------------------------------

const DEFAULT_SENTINELS = new Set([
  "",
  "changeme",
  "change-me",
  "default-jwt-secret",
  "rto-scorer-key-default",
  "DO_NOT_USE_IN_PROD",
]);

function readSecret(): string {
  const raw = process.env.JWT_SECRET ?? "";
  if (DEFAULT_SENTINELS.has(raw)) {
    // SEC-3: refuse to start. Throwing at module load is the loudest
    // possible signal and is caught by Next.js's boot path.
    throw new Error(
      "SEC-3 refuse-to-start: JWT_SECRET is missing or set to a known default. " +
        "Set a 256-bit random secret via `openssl rand -base64 32` before booting.",
    );
  }
  if (raw.length < 32) {
    throw new Error(
      "SEC-3 refuse-to-start: JWT_SECRET must be >= 32 chars (HS256 minimum).",
    );
  }
  return raw;
}

const secretCache: { value?: Uint8Array } = {};
function secretKey(): Uint8Array {
  if (!secretCache.value) {
    secretCache.value = new TextEncoder().encode(readSecret());
  }
  return secretCache.value;
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type Scope = "score" | "admin" | "audit:read" | "cases:write";

export interface AccessClaims extends JWTPayload {
  sub: string; // user id / handle
  scope: Scope[];
  typ: "access";
}

export interface RefreshClaims extends JWTPayload {
  sub: string;
  fid: string; // refresh-token family id
  jti: string; // this token's id (matches RefreshToken.id)
  scope: Scope[];
  typ: "refresh";
}

export const ACCESS_TTL_SEC = 15 * 60; // 15 minutes
export const REFRESH_TTL_SEC = 7 * 24 * 60 * 60; // 7 days

/** Issue an access token for a user + scopes. */
export async function issueAccessToken(
  subject: string,
  scopes: Scope[],
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  return new SignJWT({ scope: scopes, typ: "access" })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(subject)
    .setIssuedAt(now)
    .setExpirationTime(now + ACCESS_TTL_SEC)
    .setIssuer("rto-trust-layer")
    .setJti(crypto.randomUUID())
    .sign(secretKey());
}

/** Hash a raw refresh token before storing it (we never store raw). */
function hashToken(raw: string): string {
  return createHash("sha256").update(raw).digest("hex");
}

/** Generate a cryptographically-random refresh token (URL-safe). */
function rawRefreshToken(): string {
  return crypto.randomUUID() + "." + crypto.randomUUID();
}

/**
 * Issue a refresh token: generate the raw value, persist its hash + the
 * family id + scope + expiry. Returns the raw token (the only time the
 * raw value exists in memory).
 */
export async function issueRefreshToken(
  subject: string,
  scopes: Scope[],
): Promise<string> {
  const familyId = crypto.randomUUID();
  const raw = rawRefreshToken();
  const id = crypto.randomUUID();
  const expiresAt = new Date(Date.now() + REFRESH_TTL_SEC * 1000);
  await db.refreshToken.create({
    data: {
      id,
      familyId,
      userId: subject,
      hashedToken: hashToken(raw),
      scope: scopes.join(" "),
      expiresAt,
    },
  });
  return raw;
}

/** Verify an access token; returns the claims or null. */
export async function verifyAccessToken(
  token: string,
): Promise<AccessClaims | null> {
  try {
    const { payload } = await jwtVerify(token, secretKey(), {
      issuer: "rto-trust-layer",
      maxTokenAge: `${ACCESS_TTL_SEC}s`,
    });
    if (payload.typ !== "access") return null;
    return payload as AccessClaims;
  } catch {
    return null;
  }
}

/**
 * Verify a refresh token AND rotate it:
 *   1. Look up by hash. If not found → null (invalid).
 *   2. If the row is revoked → if compromised flag is set, this is a
 *      replay of a stolen-rotated token → return "compromised". Caller
 *      nukes the family. Otherwise just invalid.
 *   3. If expired → null.
 *   4. Revoke this token, issue a new one in the same family, return
 *      the new raw token.
 *
 * The rotation-detection logic mirrors the OAuth2 RFC 6749 §10.4
 * threat-model: if a stolen token is replayed after the legitimate
 * client has rotated, the whole family is compromised.
 */
export async function rotateRefreshToken(
  raw: string,
): Promise<
  | { ok: true; subject: string; scopes: Scope[]; newRaw: string }
  | { ok: false; reason: "invalid" | "expired" | "compromised"; familyId?: string }
> {
  const hashed = hashToken(raw);
  const row = await db.refreshToken.findUnique({
    where: { hashedToken: hashed },
  });
  if (!row) return { ok: false, reason: "invalid" };
  if (row.compromised || row.revokedAt) {
    // Replay of a rotated token → mark the whole family compromised.
    if (!row.compromised) {
      await db.refreshToken.updateMany({
        where: { familyId: row.familyId },
        data: { compromised: true, revokedAt: new Date() },
      });
    }
    return { ok: false, reason: "compromised", familyId: row.familyId };
  }
  if (row.expiresAt.getTime() < Date.now()) {
    return { ok: false, reason: "expired" };
  }
  // Rotate: revoke this token, issue a successor in the same family.
  const newRaw = rawRefreshToken();
  const newId = crypto.randomUUID();
  await db.$transaction([
    db.refreshToken.update({
      where: { id: row.id },
      data: { revokedAt: new Date() },
    }),
    db.refreshToken.create({
      data: {
        id: newId,
        familyId: row.familyId,
        userId: row.userId,
        hashedToken: hashToken(newRaw),
        scope: row.scope,
        expiresAt: new Date(Date.now() + REFRESH_TTL_SEC * 1000),
      },
    }),
  ]);
  const scopes = row.scope.split(" ") as Scope[];
  return { ok: true, subject: row.userId, scopes, newRaw };
}

/** Revoke an entire refresh-token family (logout-everywhere). */
export async function revokeFamily(familyId: string): Promise<void> {
  await db.refreshToken.updateMany({
    where: { familyId },
    data: { revokedAt: new Date() },
  });
}

/** Constant-time string compare for auth-header parsing. */
export function safeEqual(a: string, b: string): boolean {
  const ab = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

/** Parse a Bearer token from an Authorization header. */
export function bearerFrom(header: string | null): string | null {
  if (!header) return null;
  const m = /^Bearer\s+(.+)$/i.exec(header.trim());
  return m ? m[1].trim() : null;
}
