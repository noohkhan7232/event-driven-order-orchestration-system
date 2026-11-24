import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.E2E_FRONTEND_URL || "http://localhost:3000",
    // Auth traces contain request bodies; never persist credentials in traces.
    trace: "off",
    screenshot: "only-on-failure",
  },
  reporter: "list",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
