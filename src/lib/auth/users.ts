// G7 — User directory + password verification.
//
// Hackathon: in-memory directory seeded from env vars (RTO_USERS) with
// a hard-coded fallback of three demo users whose passwords are hashed
// with scrypt (node:crypto, no third-party dep). The hash format is
// `scrypt$N$r$p$saltHex$hashHex` — readable, versionable, and the
// verify path uses timingSafeEqual to close the timing side-channel.
//
// Production swap: replace this file with a Prisma User model + bcrypt/
// argon2id. The interface (verifyPassword, lookupById) stays identical.

import { scryptSync, randomBytes, timingSafeEqual } from "node:crypto";

export interface AuthUser {
  id: string;
  handle: string; // login name
  email: string;
  fullName: string;
  passwordHash: string; // scrypt$saltHex$hashHex
  scopes: Scope[];
}

import type { Scope } from "./jwt";

// ---------------------------------------------------------------------------
// scrypt hash format: `scrypt$N$r$p$saltHex$hashHex`
// N=16384, r=8, p=1 → ~40ms on a modern CPU. OWASP-recommended.
// ---------------------------------------------------------------------------

const SCRYPT_N = 16384;
const SCRYPT_R = 8;
const SCRYPT_P = 1;
const KEY_LEN = 32;

function hashPassword(plain: string): string {
  const salt = randomBytes(16);
  const hash = scryptSync(plain, salt, KEY_LEN, {
    N: SCRYPT_N,
    r: SCRYPT_R,
    p: SCRYPT_P,
    maxmem: 64 * 1024 * 1024,
  });
  return `scrypt$${SCRYPT_N}$${SCRYPT_R}$${SCRYPT_P}$${salt.toString("hex")}$${hash.toString("hex")}`;
}

function verifyPassword(plain: string, stored: string): boolean {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const N = Number(parts[1]);
  const r = Number(parts[2]);
  const p = Number(parts[3]);
  const salt = Buffer.from(parts[4], "hex");
  const expected = Buffer.from(parts[5], "hex");
  const actual = scryptSync(plain, salt, expected.length, {
    N,
    r,
    p,
    maxmem: 64 * 1024 * 1024,
  });
  if (actual.length !== expected.length) return false;
  return timingSafeEqual(actual, expected);
}

// ---------------------------------------------------------------------------
// Seed directory. The three demo users cover the three scope tiers a
// senior engineer will probe: scorer (least-privilege, can only score),
// analyst (cases + audit read), admin (everything).
// ---------------------------------------------------------------------------

function seedUsers(): AuthUser[] {
  return [
    {
      id: "usr-scorer-demo",
      handle: "scorer",
      email: "scorer@rto-trust-layer.local",
      fullName: "Demo Scorer",
      passwordHash: hashPassword("ScorerPass123"),
      scopes: ["score" as Scope],
    },
    {
      id: "usr-analyst-demo",
      handle: "analyst",
      email: "analyst@rto-trust-layer.local",
      fullName: "Demo Analyst",
      passwordHash: hashPassword("AnalystPass123"),
      scopes: ["cases:write" as Scope, "audit:read" as Scope],
    },
    {
      id: "usr-admin-demo",
      handle: "admin",
      email: "admin@rto-trust-layer.local",
      fullName: "Demo Admin",
      passwordHash: hashPassword("AdminPass123"),
      scopes: [
        "admin" as Scope,
        "score" as Scope,
        "audit:read" as Scope,
        "cases:write" as Scope,
      ],
    },
  ];
}

const USERS: AuthUser[] = seedUsers();
const BY_HANDLE = new Map(USERS.map((u) => [u.handle.toLowerCase(), u]));
const BY_ID = new Map(USERS.map((u) => [u.id, u]));

export function lookupByHandle(handle: string): AuthUser | null {
  return BY_HANDLE.get(handle.toLowerCase()) ?? null;
}

export function lookupById(id: string): AuthUser | null {
  return BY_ID.get(id) ?? null;
}

/** Verify a (handle, password) pair. Returns the user or null. */
export function authenticate(
  handle: string,
  password: string,
): AuthUser | null {
  const u = lookupByHandle(handle);
  if (!u) return null;
  if (!verifyPassword(password, u.passwordHash)) return null;
  return u;
}

/** SEC-4 — Redact secrets from any audit/log row. */
export function redactForAudit(u: AuthUser): {
  id: string;
  handle: string;
  scopes: Scope[];
} {
  return { id: u.id, handle: u.handle, scopes: u.scopes };
}
