import "server-only";
import { headers, cookies } from "next/headers";
import { NextRequest } from "next/server";
import { redirect } from "next/navigation";
import { SESSION_COOKIE, origin, upstream } from "./bff";
import type { components } from "@/lib/api/generated";
async function guard(response: Response) {
  if (response.status === 401) redirect("/bff/auth/expired");
  if (response.status === 429) {
    const header = response.headers.get("retry-after") || "";
    const numeric = Number(header);
    const retry =
      numeric > 0 && Number.isFinite(numeric)
        ? numeric
        : Math.ceil((Date.parse(header) - Date.now()) / 1000);
    const seconds = Number.isFinite(retry)
      ? Math.max(1, Math.min(3600, Math.ceil(retry)))
      : 60;
    redirect("/rate-limited?retry=" + seconds);
  }
  if (response.status === 403) {
    const value = (await response.clone().json()) as { code?: string };
    if (value.code === "tenant_suspended") redirect("/suspended");
  }
}
export async function session() {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");
  const request = new NextRequest(origin(), { headers: await headers() });
  let response: Response;
  try {
    response = await upstream(request, "auth/me", token);
  } catch {
    throw new Error("session_unavailable");
  }
  await guard(response);
  if (!response.ok) throw new Error("session_unavailable");
  const user = (await response.json()) as components["schemas"]["UserRead"];
  if (user.role === "customer" && !user.is_platform_admin)
    redirect("/access-denied");
  if (user.is_platform_admin) return { user, tenant: null };
  const tenantResponse = await upstream(request, "tenants/me", token);
  await guard(tenantResponse);
  if (!tenantResponse.ok) throw new Error("tenant_unavailable");
  return {
    user,
    tenant:
      (await tenantResponse.json()) as components["schemas"]["TenantRead"],
  };
}
