import { strings } from "@/lib/strings";
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public retryAfter = 0,
  ) {
    super(strings.errors[code] || strings.errors.unknown);
    this.name = "ApiError";
  }
}
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!path.startsWith("/bff/")) throw new ApiError(400, "invalid_request");
  const target = new URL(path, "http://bff.local");
  if (
    target.origin !== "http://bff.local" ||
    !target.pathname.startsWith("/bff/")
  )
    throw new ApiError(400, "invalid_request");
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "content-type": "application/json",
        ...Object.fromEntries(new Headers(init.headers)),
        "x-requested-with": "seller-dashboard",
      },
    });
  } catch {
    throw new ApiError(0, "offline");
  }
  if (!response.ok) {
    let code = "unknown";
    try {
      const value: unknown = await response.json();
      if (
        value &&
        typeof value === "object" &&
        "code" in value &&
        typeof value.code === "string"
      )
        code = value.code;
    } catch {
      /* Error bodies can be empty; never display raw upstream text. */
    }
    if (response.status === 401) {
      if (typeof window !== "undefined")
        window.location.replace(
          new URL("/login?expired=1", window.location.origin).href,
        );
    }
    if (code === "tenant_suspended" && typeof window !== "undefined")
      window.location.replace(
        new URL("/suspended", window.location.origin).href,
      );
    const header = response.headers.get("retry-after") || "";
    const seconds = Number(header);
    const retry =
      Number.isFinite(seconds) && seconds > 0
        ? seconds
        : Math.max(0, Math.ceil((Date.parse(header) - Date.now()) / 1000)) || 0;
    throw new ApiError(response.status, code, retry);
  }
  if (
    typeof window !== "undefined" &&
    ["/bff/auth/login", "/bff/auth/logout"].includes(target.pathname)
  ) {
    window.dispatchEvent(new Event("dashboard:session-changed"));
  }
  return response.status === 204
    ? (undefined as T)
    : ((await response.json()) as T);
}
