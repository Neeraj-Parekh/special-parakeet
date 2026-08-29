// Shared API proxy helper for Next.js API routes.
//
// Usage:
//   import { proxyGet, proxyPost, jsonOk, mockOk } from "@/lib/api-proxy";
//
// Each Next.js API route:
//   1. Reads API_BASE_URL env var (default http://localhost:8000 — the
//      Python FastAPI service's docker-compose port).
//   2. Forwards the request with the same method, body, and select
//      forwarded headers (Authorization, Idempotency-Key, X-Mandate,
//      X-Device-Id, X-User-Id).
//   3. On fetch failure (Python API unreachable — the typical case in
//      the host sandbox where the Python project isn't running) the
//      caller's mock-data callback provides a fallback response and
//      the route sets `X-Mock-Mode: true` so the frontend can badge
//      the experience as "preview without backend".
//
// `z-ai-web-dev-sdk` is server-only — it is never imported here. The
// Copilot route uses it directly under src/app/api/copilot/route.ts.

export const API_BASE_URL =
  process.env.API_BASE_URL?.replace(/\/$/, "") || "http://localhost:8000";

export const FORWARD_HEADERS = [
  "authorization",
  "idempotency-key",
  "x-mandate",
  "x-device-id",
  "x-user-id",
] as const;

export type MockFactory<T> = () => T | Promise<T>;

/** Forward-only headers (lowercased keys, by header name). */
function pickForwardedHeaders(req: Request): Record<string, string> {
  const out: Record<string, string> = {};
  FORWARD_HEADERS.forEach((name) => {
    const v = req.headers.get(name);
    if (v) out[name] = v;
  });
  return out;
}

/** Construct a fetch to the Python backend with the same method + body. */
export async function callBackend(
  path: string,
  init: {
    method?: string;
    body?: BodyInit | null;
    req?: Request;
    extraHeaders?: Record<string, string>;
    query?: Record<string, string | number | undefined>;
    signal?: AbortSignal;
  } = {},
): Promise<Response> {
  const url = new URL(API_BASE_URL + path);
  if (init.query) {
    Object.entries(init.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    });
  }
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.req ? pickForwardedHeaders(init.req) : {}),
    ...(init.extraHeaders || {}),
  };
  if (init.body && init.method && init.method !== "GET" && init.method !== "HEAD") {
    if (init.body instanceof FormData) {
      // let fetch set the multipart boundary
    } else if (typeof init.body === "string") {
      headers["Content-Type"] = "application/json";
    }
  }
  return fetch(url, {
    method: init.method || "GET",
    headers,
    body: init.body,
    signal: init.signal,
    // Don't follow redirects — the Python API doesn't issue any and we
    // want to surface errors verbatim.
    redirect: "manual",
  });
}

/** Wrap a JSON value as a 200 Response with optional mock-mode flag. */
export function jsonOk<T>(
  value: T,
  opts: { mock?: boolean; headers?: Record<string, string> } = {},
): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...(opts.mock ? { "X-Mock-Mode": "true" } : {}),
      ...(opts.headers || {}),
    },
  });
}

/** Wrap a CSV string as a downloadable attachment. */
export function csvOk(
  csv: string,
  filename: string,
  opts: { mock?: boolean } = {},
): Response {
  return new Response(csv, {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-store",
      ...(opts.mock ? { "X-Mock-Mode": "true" } : {}),
    },
  });
}

/** Wrap a text/plain payload (used by /metrics proxy). */
export function textOk(
  text: string,
  opts: { mock?: boolean; contentType?: string } = {},
): Response {
  return new Response(text, {
    status: 200,
    headers: {
      "Content-Type": opts.contentType || "text/plain; version=0.0.4",
      "Cache-Control": "no-store",
      ...(opts.mock ? { "X-Mock-Mode": "true" } : {}),
    },
  });
}

/** Forward a backend Response verbatim (status + body + content-type).
 * The mock-mode flag is preserved only if the response was successful
 * AND we generated it (not the backend). */
export async function forwardResponse(
  backend: Response,
  opts: { mock?: boolean } = {},
): Promise<Response> {
  const body = await backend.text();
  const headers: Record<string, string> = {
    "Cache-Control": "no-store",
  };
  const ct = backend.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  const cd = backend.headers.get("content-disposition");
  if (cd) headers["Content-Disposition"] = cd;
  if (opts.mock) headers["X-Mock-Mode"] = "true";
  return new Response(body, { status: backend.status, headers });
}

/** Run callBackend, on failure fall back to mock. */
export async function proxyJson<T>(
  path: string,
  init: {
    method?: string;
    body?: BodyInit | null;
    req?: Request;
    extraHeaders?: Record<string, string>;
    query?: Record<string, string | number | undefined>;
  },
  mock: MockFactory<T>,
): Promise<Response> {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 4000);
    const backend = await callBackend(path, { ...init, signal: ctrl.signal });
    clearTimeout(timeout);
    if (!backend.ok) {
      return forwardResponse(backend);
    }
    return forwardResponse(backend);
  } catch (err) {
    const value = await mock();
    return jsonOk(value, { mock: true });
  }
}

/** Parse a JSON body from a Request, returning null on failure. */
export async function parseJsonBody<T>(req: Request): Promise<T | null> {
  try {
    const text = await req.text();
    if (!text) return null;
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}
