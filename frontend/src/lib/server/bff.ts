import "server-only";
import { isIP } from "node:net";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import type { components } from "@/lib/api/generated";

export const SESSION_COOKIE = "oos_session";
export const MAX_REQUEST = 1048576,
  MAX_RESPONSE = 2097152;
const safeMethods = new Set(["GET", "HEAD"]);
const connectionFields = (headers: Headers) =>
  new Set(
    (headers.get("connection") || "")
      .split(",")
      .map((name) => name.trim().toLowerCase()),
  );
const prefixes = new Set([
  "orders",
  "products",
  "inventory",
  "shipments",
  "api-keys",
  "webhook-endpoints",
  "failed-jobs",
  "settings",
  "admin",
]);
const loginSchema = z.object({
  email: z.email(),
  password: z.string().min(1).max(128),
});
const registrationSchema = z.object({
  business_name: z.string().min(2).max(160),
  owner_name: z.string().min(2).max(160),
  owner_email: z.email(),
  owner_password: z.string().min(8).max(128),
  slug: z.string().optional(),
  invite_code: z.string().optional(),
});
export function origin() {
  return new URL(process.env.APP_ORIGIN || "http://localhost:3000").origin;
}
export function csrf(request: NextRequest) {
  return (
    request.headers.get("x-requested-with") === "seller-dashboard" &&
    request.headers.get("origin") === origin() &&
    request.headers.get("sec-fetch-site") === "same-origin"
  );
}
export function problem(status: number, code: string) {
  return NextResponse.json(
    { code },
    { status, headers: { "cache-control": "no-store" } },
  );
}
function cookieOptions() {
  const host = new URL(origin()).hostname;
  return {
    httpOnly: true,
    secure: !["localhost", "127.0.0.1", "[::1]"].includes(host),
    sameSite: "lax" as const,
    path: "/",
    maxAge: 900,
  };
}
export function clearSession(response: NextResponse) {
  response.cookies.set(SESSION_COOKIE, "", { ...cookieOptions(), maxAge: 0 });
  return response;
}
async function bounded(body: ReadableStream<Uint8Array> | null, limit: number) {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) {
        await reader.cancel();
        throw new Error("body_limit");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}
export async function upstream(
  request: NextRequest,
  path: string,
  token?: string,
  body?: Uint8Array,
) {
  const ip = request.headers.get("x-dashboard-client-ip") || "";
  if (!isIP(ip)) throw new Error("missing_ingress");
  const target = new URL(process.env.API_INTERNAL_URL || "http://api:8000");
  if (
    !["http:", "https:"].includes(target.protocol) ||
    target.username ||
    target.password ||
    target.pathname !== "/"
  )
    throw new Error("invalid_upstream");
  target.pathname = "/api/v1/" + path;
  target.search = new URL(request.url).search;
  const headers = new Headers({ "x-forwarded-for": ip });
  const hop = connectionFields(request.headers);
  for (const name of [
    "accept",
    "content-type",
    "last-event-id",
    "idempotency-key",
  ]) {
    const value = request.headers.get(name);
    if (value && !hop.has(name)) headers.set(name, value);
  }
  if (token) headers.set("authorization", "Bearer " + token);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    return await fetch(target, {
      method: request.method,
      headers,
      body: body?.length ? Buffer.from(body) : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.any([request.signal, controller.signal]),
    });
  } finally {
    clearTimeout(timeout);
  }
}
async function relay(response: Response) {
  const hop = connectionFields(response.headers);
  const headers = new Headers({
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  });
  response.headers.forEach((v, k) => {
    if (hop.has(k)) return;
    if (
      k === "content-type" ||
      k === "retry-after" ||
      /^x-ratelimit-[a-z-]+$/.test(k) ||
      /^ratelimit-[a-z-]+$/.test(k)
    )
      headers.set(k, v);
  });
  let result: NextResponse;
  if (response.status === 204 || response.status === 304)
    result = new NextResponse(null, { status: response.status, headers });
  else if (
    response.headers.get("content-type")?.startsWith("text/event-stream")
  ) {
    headers.set("x-accel-buffering", "no");
    let bytes = 0,
      stopped = false;
    const reader = response.body?.getReader();
    const stream = reader
      ? new ReadableStream<Uint8Array>({
          async pull(controller) {
            let item: ReadableStreamReadResult<Uint8Array>;
            try {
              item = await reader.read();
            } catch {
              controller.error(new Error("stream_interrupted"));
              return;
            }
            if (stopped) return;
            if (item.done) {
              stopped = true;
              reader.releaseLock();
              controller.close();
              return;
            }
            bytes += item.value.byteLength;
            if (bytes > MAX_RESPONSE) {
              stopped = true;
              await reader.cancel();
              reader.releaseLock();
              controller.error(new Error("response_limit"));
              return;
            }
            controller.enqueue(item.value);
          },
          async cancel(reason) {
            stopped = true;
            await reader.cancel(reason);
            reader.releaseLock();
          },
        })
      : null;
    result = new NextResponse(stream, {
      status: response.status,
      headers,
    });
  } else {
    const bytes = await bounded(response.body, MAX_RESPONSE);
    result = new NextResponse(Buffer.from(bytes), {
      status: response.status,
      headers,
    });
  }
  if (response.status === 401) {
    clearSession(result);
    result.headers.set("x-auth-redirect", "/login");
  }
  return result;
}
function allowed(path: string[]) {
  if (
    !path.length ||
    path.some(
      (p) => !p || p === "." || p === ".." || !/^[-a-zA-Z0-9_]+$/.test(p),
    )
  )
    return false;
  if (path.join("/") === "auth/me" || path.join("/") === "tenants/me")
    return true;
  if (path[0] === "public")
    return (
      path[1] === "track" &&
      (path.length === 3 || (path.length === 4 && path[3] === "stream"))
    );
  return prefixes.has(path[0]);
}
export function mayProxy(
  user: components["schemas"]["UserRead"],
  path: string[],
) {
  if (user.is_platform_admin)
    return path[0] === "admin" || path.join("/") === "auth/me";
  if (user.role === "customer" || !user.tenant_id) return false;
  if (path[0] === "admin") return false;
  if (
    user.role === "vendor" &&
    (["api-keys", "webhook-endpoints", "failed-jobs"].includes(path[0]) ||
      path[0] === "settings")
  )
    return false;
  return true;
}
export async function handleProxy(request: NextRequest, path: string[]) {
  if (!allowed(path)) return problem(404, "not_found");
  if (!safeMethods.has(request.method) && !csrf(request))
    return problem(403, "csrf_rejected");
  if (path[0] === "public" && !safeMethods.has(request.method))
    return problem(405, "method_not_allowed");
  try {
    const token = request.cookies.get(SESSION_COOKIE)?.value;
    if (path[0] !== "public") {
      if (!token) return clearSession(problem(401, "unauthenticated"));
      if (path.join("/") !== "auth/me") {
        const identity = await upstream(
          new NextRequest(request.url, {
            headers: request.headers,
            signal: request.signal,
          }),
          "auth/me",
          token,
        );
        if (!identity.ok) return await relay(identity);
        const user = JSON.parse(
          new TextDecoder().decode(await bounded(identity.body, 65536)),
        ) as components["schemas"]["UserRead"];
        if (!mayProxy(user, path)) return problem(403, "dashboard_forbidden");
      }
    }
    if (Number(request.headers.get("content-length")) > MAX_REQUEST)
      return problem(413, "payload_too_large");
    let body: Uint8Array | undefined;
    if (!safeMethods.has(request.method)) {
      try {
        body = await bounded(request.body, MAX_REQUEST);
      } catch {
        return problem(413, "payload_too_large");
      }
    }
    return await relay(await upstream(request, path.join("/"), token, body));
  } catch (error) {
    return problem(
      error instanceof Error && error.message === "body_limit" ? 502 : 503,
      "upstream_unavailable",
    );
  }
}
async function auth(request: NextRequest, kind: "login" | "register") {
  if (!csrf(request)) return problem(403, "csrf_rejected");
  if (Number(request.headers.get("content-length")) > 16384)
    return problem(413, "payload_too_large");
  let payload: unknown;
  try {
    payload = JSON.parse(
      new TextDecoder().decode(await bounded(request.body, 16384)),
    );
  } catch (error) {
    return problem(
      error instanceof Error && error.message === "body_limit" ? 413 : 400,
      "invalid_request",
    );
  }
  const parsed = (
    kind === "login" ? loginSchema : registrationSchema
  ).safeParse(payload);
  if (!parsed.success) return problem(422, "validation_error");
  try {
    const response = await upstream(
      request,
      kind === "login" ? "auth/login" : "tenants/register",
      undefined,
      new TextEncoder().encode(JSON.stringify(parsed.data)),
    );
    if (!response.ok || kind === "register") return await relay(response);
    const data = JSON.parse(
      new TextDecoder().decode(await bounded(response.body, 65536)),
    ) as { access_token?: unknown };
    if (
      typeof data.access_token !== "string" ||
      data.access_token.length > 4096
    )
      return problem(502, "invalid_upstream");
    const result = NextResponse.json(
      { ok: true },
      { headers: { "cache-control": "no-store" } },
    );
    result.cookies.set(SESSION_COOKIE, data.access_token, cookieOptions());
    return result;
  } catch {
    return problem(503, "upstream_unavailable");
  }
}
export const login = (request: NextRequest) => auth(request, "login");
export const register = (request: NextRequest) => auth(request, "register");
export async function logout(request: NextRequest) {
  return csrf(request)
    ? clearSession(NextResponse.json({ ok: true }))
    : problem(403, "csrf_rejected");
}
