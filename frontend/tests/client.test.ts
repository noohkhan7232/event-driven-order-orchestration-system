// @vitest-environment node
import { it, expect, vi, afterEach } from "vitest";
import { api, ApiError } from "@/lib/api/client";
import { navigation, type User } from "@/lib/roles";
import { money, localDate } from "@/lib/format";
afterEach(() => vi.unstubAllGlobals());
it("preserves query values containing URLs and dots without allowing another origin", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const path = "/bff/api/orders?external_reference=shop..sale://ref";
  await expect(api(path)).resolves.toEqual({ ok: true });
  expect(fetcher.mock.calls[0][0]).toBe(path);
});
it("preserves RequestInit Headers objects and always adds the CSRF marker", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  await api("/bff/api/orders", {
    headers: new Headers({ "idempotency-key": "fixture" }),
  });
  const headers = new Headers(fetcher.mock.calls[0][1].headers);
  expect(headers.get("idempotency-key")).toBe("fixture");
  expect(headers.get("x-requested-with")).toBe("seller-dashboard");
});
it("uses same-origin BFF requests with the custom CSRF header and no JS tokens", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  expect(await api("/bff/api/orders")).toEqual({ ok: true });
  expect(fetcher.mock.calls[0][1]).toMatchObject({
    credentials: "same-origin",
    cache: "no-store",
    headers: { "x-requested-with": "seller-dashboard" },
  });
  await expect(api("https://external.test")).rejects.toMatchObject({
    code: "invalid_request",
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("maps safe API codes, preserves 429 retry time and hides raw errors", async () => {
  expect(new ApiError(403, "forbidden").message).toContain(
    "email and password",
  );
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { code: "registration_closed", message: "PRIVATE" },
          { status: 403 },
        ),
      ),
  );
  await expect(api("/bff/auth/register")).rejects.toMatchObject({
    status: 403,
    code: "registration_closed",
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { code: "rate_limited" },
          { status: 429, headers: { "retry-after": "3" } },
        ),
      ),
  );
  await expect(api("/bff/auth/login")).rejects.toMatchObject({ retryAfter: 3 });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("PRIVATE")));
  await expect(api("/bff/api/orders")).rejects.toMatchObject({
    status: 0,
    code: "offline",
  });
  expect(new ApiError(500, "unknown").message).not.toContain("PRIVATE");
});
it("gates navigation for all roles and platform-only staff", () => {
  const user = {
    role: "admin",
    tenant_id: "tenant",
    is_platform_admin: false,
  } as User;
  expect(navigation(user).map((n) => n.href)).toContain("/webhooks");
  const vendor = navigation({ ...user, role: "vendor" }).map((n) => n.href);
  for (const path of ["/api-keys", "/payments", "/webhooks", "/failed-jobs"])
    expect(vendor).not.toContain(path);
  expect(navigation({ ...user, role: "customer" })).toEqual([]);
  expect(
    navigation({ ...user, is_platform_admin: true, tenant_id: null }).map(
      (n) => n.href,
    ),
  ).toEqual(["/platform"]);
});
it("formats INR and locale dates with an IST tooltip", () => {
  expect(money("123456.78")).toContain("1,23,456.78");
  expect(localDate("2026-11-24T00:00:00Z", "en-GB").tooltip).toMatch(
    /India|IST|GMT\+5:30/,
  );
});
