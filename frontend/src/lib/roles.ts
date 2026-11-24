import type { components } from "@/lib/api/generated";
import { strings } from "@/lib/strings";
export type User = components["schemas"]["UserRead"];
export function navigation(user: User) {
  if (user.is_platform_admin)
    return [{ href: "/platform", label: strings.nav.platform }];
  if (user.role === "customer" || !user.tenant_id) return [];
  return [
    { href: "/dashboard", label: strings.nav.home },
    ...(["orders", "catalog", "inventory"] as const).map((key) => ({
      href: "/" + key,
      label: strings.nav[key],
    })),
    ...(user.role === "admin"
      ? (
          [
            "payments",
            "api-keys",
            "webhooks",
            "notifications",
            "failed-jobs",
          ] as const
        ).map((key) => ({ href: "/" + key, label: strings.nav[key] }))
      : []),
  ];
}
