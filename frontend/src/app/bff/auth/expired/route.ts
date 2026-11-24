import { NextResponse } from "next/server";
import { NextRequest } from "next/server";
import { cookies, headers } from "next/headers";
import {
  clearSession,
  origin,
  upstream,
  SESSION_COOKIE,
} from "@/lib/server/bff";
function jump(path: string) {
  const response = NextResponse.redirect(new URL(path, origin()));
  response.headers.set("cache-control", "no-store");
  return response;
}
export async function GET() {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (token) {
    try {
      const request = new NextRequest(new URL("/bff/auth/expired", origin()), {
        headers: await headers(),
      });
      const response = await upstream(request, "auth/me", token);
      if (response.ok) return jump("/dashboard");
      if (response.status === 403) {
        const value = (await response.json()) as { code?: string };
        if (value.code === "tenant_suspended") return jump("/suspended");
      }
      if (response.status === 429) {
        const seconds = Number(response.headers.get("retry-after"));
        return jump(
          "/rate-limited?retry=" +
            Math.max(
              1,
              Math.min(
                3600,
                Number.isFinite(seconds) && seconds > 0
                  ? Math.ceil(seconds)
                  : 60,
              ),
            ),
        );
      }
      if (response.status !== 401) return jump("/login");
    } catch {
      return jump("/login");
    }
  }
  return clearSession(jump("/login?expired=1"));
}
