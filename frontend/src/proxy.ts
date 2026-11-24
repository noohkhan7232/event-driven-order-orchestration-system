import { NextRequest, NextResponse } from "next/server";
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const dev = process.env.NODE_ENV === "development";
  const csp = `default-src 'self'; script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}; style-src 'self' 'nonce-${nonce}'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`;
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const guarded = [
    "/dashboard",
    "/platform",
    "/orders",
    "/catalog",
    "/inventory",
    "/payments",
    "/api-keys",
    "/webhooks",
    "/notifications",
    "/failed-jobs",
  ].some(
    (p) =>
      request.nextUrl.pathname === p ||
      request.nextUrl.pathname.startsWith(p + "/"),
  );
  const response =
    guarded && !request.cookies.has("oos_session")
      ? NextResponse.redirect(new URL("/login", request.url))
      : NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("Cache-Control", "no-store");
  return response;
}
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
