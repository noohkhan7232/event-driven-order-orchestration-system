import { expect, it, vi } from "vitest";
import { isValidElement } from "react";
import Payments from "@/app/(app)/payments/page";
import Keys from "@/app/(app)/api-keys/page";
import Webhooks from "@/app/(app)/webhooks/page";
import Notifications from "@/app/(app)/notifications/page";
import Jobs from "@/app/(app)/failed-jobs/page";
import Detail from "@/app/(app)/failed-jobs/[id]/page";
import Platform from "@/app/(app)/platform/page";
import { session } from "@/lib/server/session";
vi.mock("@/lib/server/session", () => ({ session: vi.fn() }));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("not-found");
  },
  usePathname: () => "/payments",
}));
const pages = [
  Payments,
  Keys,
  Webhooks,
  Notifications,
  Jobs,
  () => Detail({ params: Promise.resolve({ id: "job" }) }),
];
function context(role: "admin" | "vendor" | "customer", platform = false) {
  return {
    user: {
      id: "user",
      tenant_id: platform ? null : "tenant",
      role,
      is_platform_admin: platform,
      email: "fixture@example.com",
      full_name: "Fixture",
      is_active: true,
    },
    tenant: platform
      ? null
      : {
          id: "tenant",
          name: "Fixture",
          slug: "fixture",
          status: "active",
          default_currency: "INR",
        },
  };
}
it.each(pages)(
  "permits the owner on every owner settings route",
  async (page) => {
    vi.mocked(session).mockResolvedValue(context("admin"));
    expect(isValidElement(await page())).toBe(true);
  },
);
it.each(
  pages.flatMap((page) =>
    ["vendor", "customer", "platform"].map((role) => ({ page, role })),
  ),
)("rejects $role before rendering an owner route", async ({ page, role }) => {
  vi.mocked(session).mockResolvedValue(
    context(
      role === "platform" ? "admin" : (role as "vendor" | "customer"),
      role === "platform",
    ),
  );
  await expect(page()).rejects.toThrow("not-found");
});
it("reserves the platform page for platform administrators", async () => {
  for (const role of ["admin", "vendor", "customer"] as const) {
    vi.mocked(session).mockResolvedValue(context(role));
    await expect(Platform()).rejects.toThrow("not-found");
  }
  vi.mocked(session).mockResolvedValue(context("admin", true));
  expect(isValidElement(await Platform())).toBe(true);
});
