// @vitest-environment node
import { it, expect, vi, beforeEach } from "vitest";
const { cookie } = vi.hoisted(() => ({ cookie: vi.fn() }));
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: cookie }),
  headers: async () => new Headers({ "x-dashboard-client-ip": "198.51.100.7" }),
}));
import { GET } from "@/app/bff/auth/expired/route";
beforeEach(() => cookie.mockReturnValue({ value: "fixture" }));
it("does not turn an expiry GET link into logout of a valid or refreshed session", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ id: "active" }));
  vi.stubGlobal("fetch", fetcher);
  const result = await GET();
  expect(result.headers.has("set-cookie")).toBe(false);
  expect(result.headers.get("location")).toBe(
    "http://localhost:3000/dashboard",
  );
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("clears only confirmed expired or absent sessions", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response("{}", { status: 401 }));
  vi.stubGlobal("fetch", fetcher);
  const expired = await GET();
  expect(expired.headers.get("set-cookie")).toContain("Max-Age=0");
  expect(fetcher).toHaveBeenCalledTimes(1);
  cookie.mockReturnValue(undefined);
  fetcher.mockClear();
  expect((await GET()).headers.get("location")).toBe(
    "http://localhost:3000/login?expired=1",
  );
  expect(fetcher).not.toHaveBeenCalled();
});
