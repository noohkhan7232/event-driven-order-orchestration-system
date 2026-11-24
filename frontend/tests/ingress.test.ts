// @vitest-environment node
import { it, expect } from "vitest";
import { createRequire } from "node:module";
const { sanitize } = createRequire(import.meta.url)(
  "../scripts/ingress.cjs",
) as {
  sanitize: (
    req: { headers: Record<string, string>; socket: { remoteAddress: string } },
    env: Record<string, string>,
  ) => void;
};
it("overwrites spoofed forwarded IPs and test headers using the actual socket", () => {
  const req = {
    socket: { remoteAddress: "::ffff:198.51.100.5" },
    headers: {
      "x-forwarded-for": "10.0.0.1",
      "x-dashboard-client-ip": "10.0.0.2",
      "x-dashboard-test-ip": "10.0.0.3",
      forwarded: "for=10.0.0.4",
    },
  };
  sanitize(req, {});
  expect(req.headers["x-dashboard-client-ip"]).toBe("198.51.100.5");
  expect(req.headers["x-forwarded-for"]).toBe("198.51.100.5");
  expect(req.headers).not.toHaveProperty("x-dashboard-test-ip");
  expect(req.headers).not.toHaveProperty("forwarded");
});
it("trusts forwarding only from an explicitly configured peer and gates test overrides", () => {
  const req = {
    socket: { remoteAddress: "192.0.2.1" },
    headers: {
      "x-forwarded-for": "198.51.100.10",
      "x-dashboard-test-ip": "198.51.100.20",
    },
  };
  sanitize(req, { BFF_TRUSTED_PROXY_IPS: "192.0.2.1" });
  expect(req.headers["x-forwarded-for"]).toBe("198.51.100.10");
  for (const environment of ["local", "test", "production"]) {
    const r = {
      socket: { remoteAddress: "192.0.2.1" },
      headers: { "x-dashboard-test-ip": "198.51.100.20" },
    };
    sanitize(r, { DASHBOARD_ENV: environment, BFF_ALLOW_TEST_IP: "true" });
    expect(r.headers["x-dashboard-client-ip" as keyof typeof r.headers]).toBe(
      environment === "production" ? "192.0.2.1" : "198.51.100.20",
    );
  }
});
