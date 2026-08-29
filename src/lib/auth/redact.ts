// SEC-4 — Secret redaction for audit rows + logs.
//
// Any object destined for the audit chain or a log line passes
// through `redactSecrets()` first. This strips Authorization headers,
// raw mandate tokens, bearer tokens, and any field whose name looks
// like a secret. The redaction is structural (walks the object),
// not regex-on-JSON, so it survives nested payloads.

const SECRET_KEY_PATTERNS = [
  /^authorization$/i,
  /^x-.*key$/i,
  /^x-.*token$/i,
  /^x-mandate$/i,
  /^bearer$/i,
  /^password$/i,
  /^secret$/i,
  /^api[_-]?key$/i,
  /^jwt[_-]?secret$/i,
  /^razorpay[_-]?webhook[_-]?secret$/i,
  /^refresh[_-]?token$/i,
  /^access[_-]?token$/i,
];

const SECRET_VALUE_PATTERNS = [
  // Bearer tokens.
  /^Bearer\s+\S+$/i,
  // JWTs (3 base64 segments).
  /^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$/,
  // Razorpay keys.
  /^rzp_(test|live)_[A-Za-z0-9]{16,}$/,
  // AWS keys.
  /^AKIA[0-9A-Z]{16}$/,
  // Stripe keys.
  /^(sk|pk|rk)_(test|live)_[A-Za-z0-9]{16,}$/,
];

const REDACTED = "[REDACTED]";

/** Is the key name a known secret field? */
function isSecretKey(key: string): boolean {
  return SECRET_KEY_PATTERNS.some((p) => p.test(key));
}

/** Does the value look like a secret token? */
function looksLikeSecret(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return SECRET_VALUE_PATTERNS.some((p) => p.test(value));
}

/** Recursively redact secrets from any JSON-serializable value. */
export function redactSecrets<T>(input: T, depth = 0): T {
  if (depth > 20) return input; // depth guard against cycles
  if (input === null || typeof input !== "object") {
    if (typeof input === "string" && looksLikeSecret(input)) {
      return REDACTED as unknown as T;
    }
    return input;
  }
  if (Array.isArray(input)) {
    return input.map((v) => redactSecrets(v, depth + 1)) as unknown as T;
  }
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(input as Record<string, unknown>)) {
    if (isSecretKey(k)) {
      out[k] = REDACTED;
    } else {
      out[k] = redactSecrets(v, depth + 1);
    }
  }
  return out as unknown as T;
}

/** Redact a single value (for ad-hoc scrubbing). */
export function redactValue(value: unknown): unknown {
  if (looksLikeSecret(value)) return REDACTED;
  return value;
}
