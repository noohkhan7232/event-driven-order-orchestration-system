import { defineConfig, devices } from "@playwright/test";
import { randomInt } from "node:crypto";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  use: {
    // Persistent Redis quotas must not couple separate local fixture runs.
    // The server ignores this header unless its local/test IP flag is enabled.
    extraHTTPHeaders: {
      "X-Dashboard-Test-IP": `198.19.${randomInt(256)}.${randomInt(1, 255)}`,
    },
    baseURL: process.env.E2E_FRONTEND_URL || "http://localhost:3000",
    // Auth traces contain request bodies; never persist credentials in traces.
    trace: "off",
    screenshot: "only-on-failure",
  },
  reporter: "list",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
