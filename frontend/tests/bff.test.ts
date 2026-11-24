// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { handleProxy, login, register, logout } from "@/lib/server/bff";
const origin = "http://localhost:3000";
const make = (
  path: string,
  method = "GET",
  extra: Record<string, string> = {},
  body?: string,
) =>
  new NextRequest(origin + path, {
    method,
    headers: {
      "x-dashboard-client-ip": "198.51.100.7",
      origin,
      "x-requested-with": "seller-dashboard",
      "sec-fetch-site": "same-origin",
      ...extra,
    },
    body,
  });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
describe("bounded authenticated BFF", () => {
  it("strips even whitelisted fields named by a Connection header", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ role: "admin", tenant_id: "a" }))
      .mockResolvedValueOnce(
        new Response("{}", {
          headers: {
            connection: "x-ratelimit-temporary",
            "x-ratelimit-temporary": "drop",
          },
        }),
      );
    vi.stubGlobal("fetch", fetcher);
    const result = await handleProxy(
      make("/bff/api/orders", "GET", {
        cookie: "oos_session=t",
        connection: "last-event-id",
        "last-event-id": "99",
      }),
      ["orders"],
    );
    expect(
      new Headers(fetcher.mock.calls[1][1].headers).has("last-event-id"),
    ).toBe(false);
    expect(result.headers.has("x-ratelimit-temporary")).toBe(false);
  });
  it("closes an oversized SSE response while retaining streaming semantics", async () => {
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(new Uint8Array(2097153));
        c.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );
    const result = await handleProxy(
      make("/bff/api/public/track/token/stream"),
      ["public", "track", "token", "stream"],
    );
    expect(result.status).toBe(200);
    await expect(result.text()).rejects.toThrow("response_limit");
  });
  it("caps chunked mutation bodies with 413 before contacting upstream", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const request = make(
      "/bff/api/public/track/token",
      "POST",
      {},
      "x".repeat(1048577),
    );
    // Public writes are rejected separately; authenticated writes must be bounded too.
    expect(
      (await handleProxy(request, ["public", "track", "token"])).status,
    ).toBe(405);
    fetcher.mockResolvedValueOnce(
      Response.json({ role: "admin", tenant_id: "a" }),
    );
    const oversized = make(
      "/bff/api/orders",
      "POST",
      { cookie: "oos_session=t" },
      "x".repeat(1048577),
    );
    expect((await handleProxy(oversized, ["orders"])).status).toBe(413);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("blocks unlisted paths, encoded traversal and alternate origins before fetch", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    for (const path of [
      "system/metrics",
      "webhooks/payments/a/b",
      "auth/login",
      "../admin",
      "orders/%2e%2e/admin",
      "https://evil.test",
      "orders//x",
    ])
      expect(
        (await handleProxy(make("/bff/api/x"), path.split("/"))).status,
      ).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("requires both CSRF signals for every mutation including login/register/logout", async () => {
    for (const handler of [login, register, logout])
      expect(
        (
          await handler(
            make(
              "/bff/auth/login",
              "POST",
              { origin: "http://evil.test" },
              "{}",
            ),
          )
        ).status,
      ).toBe(403);
    expect(
      (
        await handleProxy(
          make("/bff/api/orders", "POST", { "x-requested-with": "" }, "{}"),
          ["orders"],
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await handleProxy(
          make(
            "/bff/api/orders",
            "POST",
            { "sec-fetch-site": "cross-site" },
            "{}",
          ),
          ["orders"],
        )
      ).status,
    ).toBe(403);
  });
  it("strips credentials and hop headers, replaces XFF, preserves status and quotas", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          role: "admin",
          tenant_id: "a",
          is_platform_admin: false,
        }),
      )
      .mockResolvedValueOnce(
        new Response("slow", {
          status: 429,
          headers: {
            "retry-after": "4",
            "x-ratelimit-limit": "2",
            "set-cookie": "evil=1",
            connection: "close",
          },
        }),
      );
    vi.stubGlobal("fetch", fetcher);
    const result = await handleProxy(
      make("/bff/api/orders?status=paid", "GET", {
        cookie: "oos_session=jwt",
        "x-forwarded-for": "10.0.0.1",
        authorization: "Bearer attacker",
        connection: "x-evil",
        "x-evil": "secret",
      }),
      ["orders"],
    );
    expect(result.status).toBe(429);
    expect(result.headers.get("retry-after")).toBe("4");
    expect(result.headers.get("x-ratelimit-limit")).toBe("2");
    expect(result.headers.has("set-cookie")).toBe(false);
    const headers = new Headers(fetcher.mock.calls[1][1].headers);
    expect(headers.get("authorization")).toBe("Bearer jwt");
    expect(headers.get("x-forwarded-for")).toBe("198.51.100.7");
    for (const name of ["cookie", "connection", "x-evil"])
      expect(headers.has(name)).toBe(false);
  });
  it("streams SSE without waiting for stream closure and aborts on disconnect", async () => {
    let cancelled = false;
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode("id: 1\ndata: {}\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );
    const result = await handleProxy(
      make("/bff/api/public/track/token/stream"),
      ["public", "track", "token", "stream"],
    );
    expect(result.headers.get("content-type")).toContain("text/event-stream");
    const reader = result.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toContain(
      "id: 1",
    );
    await reader.cancel();
    expect(cancelled).toBe(true);
  });
  it("clears session on API 401 and preserves its status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 401 })),
    );
    const result = await handleProxy(
      make("/bff/api/orders", "GET", { cookie: "oos_session=expired" }),
      ["orders"],
    );
    expect(result.status).toBe(401);
    expect(result.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(result.headers.get("x-auth-redirect")).toBe("/login");
  });
  it("denies customer dashboard and vendor owner routes in the BFF", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ role: "vendor", tenant_id: "a" }))
      .mockResolvedValueOnce(
        Response.json({ role: "customer", tenant_id: "a" }),
      );
    vi.stubGlobal("fetch", fetcher);
    expect(
      (
        await handleProxy(
          make("/bff/api/api-keys", "GET", { cookie: "oos_session=t" }),
          ["api-keys"],
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await handleProxy(
          make("/bff/api/orders", "GET", { cookie: "oos_session=t" }),
          ["orders"],
        )
      ).status,
    ).toBe(403);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("rejects oversized requests and responses", async () => {
    expect(
      (
        await login(
          make(
            "/bff/auth/login",
            "POST",
            { "content-length": "1048577" },
            "{}",
          ),
        )
      ).status,
    ).toBe(413);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("x".repeat(2097153))),
    );
    expect(
      (
        await handleProxy(make("/bff/api/public/track/token"), [
          "public",
          "track",
          "token",
        ])
      ).status,
    ).toBe(502);
  });
  it("sets short httpOnly SameSite cookie and never returns token in login JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(
            Response.json({ access_token: "header.payload.signature" }),
          ),
        ),
    );
    const result = await login(
      make(
        "/bff/auth/login",
        "POST",
        {},
        JSON.stringify({ email: "owner@example.com", password: "password123" }),
      ),
    );
    expect(result.status).toBe(200);
    const cookie = result.headers.get("set-cookie")!;
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=lax");
    expect(cookie).toContain("Max-Age=900");
    expect(await result.text()).not.toContain("signature");
    vi.stubEnv("APP_ORIGIN", "https://seller.example.com");
    const secure = await login(
      new NextRequest("https://seller.example.com/bff/auth/login", {
        method: "POST",
        headers: {
          origin: "https://seller.example.com",
          "sec-fetch-site": "same-origin",
          "x-requested-with": "seller-dashboard",
          "x-dashboard-client-ip": "198.51.100.7",
        },
        body: JSON.stringify({
          email: "owner@example.com",
          password: "password123",
        }),
      }),
    );
    expect(secure.headers.get("set-cookie")).toContain("Secure");
  });
});
