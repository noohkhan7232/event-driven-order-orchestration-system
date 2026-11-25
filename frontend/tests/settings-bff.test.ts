// @vitest-environment node
import { expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { handleProxy } from "@/lib/server/bff";
const paths = [
  "settings/payments/razorpay",
  "api-keys",
  "webhook-endpoints",
  "settings/notifications",
  "failed-jobs",
  "admin/tenants",
];
it.each(
  paths.flatMap((path) =>
    ["admin", "vendor", "customer", "platform"].map((role) => ({ path, role })),
  ),
)("enforces $role on $path before forwarding", async ({ path, role }) => {
  const platform = role === "platform";
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({
        role: platform ? "admin" : role,
        tenant_id: platform ? null : "a",
        is_platform_admin: platform,
      }),
    )
    .mockResolvedValueOnce(Response.json({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  const request = new NextRequest("http://localhost:3000/bff/api/" + path, {
    headers: {
      cookie: "oos_session=fixture",
      "x-dashboard-client-ip": "198.51.100.1",
    },
  });
  const result = await handleProxy(request, path.split("/"));
  const allowed = path.startsWith("admin/") ? platform : role === "admin";
  expect(result.status).toBe(allowed ? 200 : 403);
  expect(fetcher).toHaveBeenCalledTimes(allowed ? 2 : 1);
});
