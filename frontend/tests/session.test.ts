// @vitest-environment node
import { it, expect, vi, beforeEach } from "vitest";
const { upstream, cookie } = vi.hoisted(() => ({
  upstream: vi.fn(),
  cookie: vi.fn(),
}));
vi.mock("next/headers", () => ({
  headers: async () => new Headers(),
  cookies: async () => ({ get: cookie }),
}));
vi.mock("next/navigation", () => ({
  redirect: (path: string) => {
    throw new Error("REDIRECT:" + path);
  },
}));
vi.mock("@/lib/server/bff", () => ({
  upstream,
  SESSION_COOKIE: "oos_session",
  origin: () => "http://localhost:3000",
}));
import { session } from "@/lib/server/session";
beforeEach(() => {
  cookie.mockReturnValue({ value: "fixture" });
  upstream.mockReset();
});
it("guards missing, expired, suspended and customer sessions with their dedicated routes", async () => {
  cookie.mockReturnValue(undefined);
  await expect(session()).rejects.toThrow("REDIRECT:/login");
  expect(upstream).not.toHaveBeenCalled();
  cookie.mockReturnValue({ value: "fixture" });
  upstream.mockResolvedValueOnce(new Response("{}", { status: 401 }));
  await expect(session()).rejects.toThrow("REDIRECT:/bff/auth/expired");
  upstream.mockResolvedValueOnce(
    Response.json({ code: "tenant_suspended" }, { status: 403 }),
  );
  await expect(session()).rejects.toThrow("REDIRECT:/suspended");
  upstream.mockResolvedValueOnce(
    Response.json({ role: "customer", is_platform_admin: false }),
  );
  await expect(session()).rejects.toThrow("REDIRECT:/access-denied");
});
it("loads a staff store from authoritative API data and keeps platform accounts separate", async () => {
  upstream
    .mockResolvedValueOnce(
      Response.json({
        role: "vendor",
        tenant_id: "a",
        is_platform_admin: false,
      }),
    )
    .mockResolvedValueOnce(Response.json({ id: "a", name: "Store A" }));
  expect((await session()).tenant?.name).toBe("Store A");
  expect(upstream.mock.calls[1][1]).toBe("tenants/me");
  upstream.mockResolvedValueOnce(
    Response.json({ role: "admin", tenant_id: null, is_platform_admin: true }),
  );
  expect((await session()).tenant).toBeNull();
  expect(upstream).toHaveBeenCalledTimes(3);
});
it("handles throttling and expiry during either server-side session request", async () => {
  upstream.mockResolvedValueOnce(
    new Response("{}", { status: 429, headers: { "retry-after": "3" } }),
  );
  await expect(session()).rejects.toThrow("REDIRECT:/rate-limited?retry=3");
  upstream
    .mockResolvedValueOnce(Response.json({ role: "admin", tenant_id: "a" }))
    .mockResolvedValueOnce(new Response("{}", { status: 401 }));
  await expect(session()).rejects.toThrow("REDIRECT:/bff/auth/expired");
});
