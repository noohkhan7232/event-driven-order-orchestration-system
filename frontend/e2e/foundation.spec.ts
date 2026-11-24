import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { randomBytes, randomUUID } from "node:crypto";

test("native auth fallback never puts credentials in a URL", async ({
  browser,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  try {
    await page.goto(
      (process.env.E2E_FRONTEND_URL || "http://localhost:3000") + "/login",
    );
    const password = randomBytes(18).toString("base64url");
    await page.getByLabel("Email address").fill("native-fixture@example.com");
    await page.getByLabel("Password", { exact: true }).fill(password);
    const responsePromise = page.waitForResponse(
      (r) =>
        r.url().includes("/bff/auth/login") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(403);
    expect(new URL(page.url()).search).toBe("");
    expect(page.url()).not.toContain(password);
    expect(await response.text()).not.toContain(password);
    console.log(
      "PASS native no-JavaScript auth uses POST; CSRF rejection; no credentials in URL/response",
    );
  } finally {
    await context.close();
  }
});

test("F1 foundation: register, sign in, responsive shell, no JS token, sign out", async ({
  page,
  context,
}) => {
  const id = randomBytes(5).toString("hex");
  const email = `phase4-${id}@example.com`;
  const password = randomBytes(18).toString("base64url");
  await page.goto("/tenants/register");
  const serious = async () => {
    const result = await new AxeBuilder({ page }).analyze();
    expect(
      result.violations.filter((v) =>
        ["serious", "critical"].includes(v.impact || ""),
      ),
    ).toEqual([]);
  };
  await serious();
  await page.getByLabel("Store name", { exact: true }).fill("Foundation " + id);
  await page.getByLabel("Your name").fill("Fixture owner ".repeat(10).trim());
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create store" }).click();
  await expect(page).toHaveURL(/\/login\?registered=1/);
  await serious();
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(
    page.getByText("Foundation " + id, { exact: true }),
  ).toBeVisible();
  await serious();
  const session = (await context.cookies()).find(
    (c) => c.name === "oos_session",
  );
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(session?.expires).toBeLessThan(Date.now() / 1000 + 901);
  expect(
    await page.evaluate(() => ({
      cookies: document.cookie,
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    })),
  ).toEqual({ cookies: "", local: [], session: [] });
  for (const width of [360, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (width < 1024) {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toBeHidden();
    }
  }
  await page.locator("summary").click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect((await context.cookies()).some((c) => c.name === "oos_session")).toBe(
    false,
  );
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login/);
  console.log(
    "PASS F1 foundation; a11y login/register/shell zero serious/critical; widths360/768/1280; httpOnly session; logout guarded",
  );
});

test("F11: independent visitors through real BFF on login and public tracking", async ({
  request,
  page,
}) => {
  const origin = process.env.E2E_FRONTEND_URL || "http://localhost:3000";
  const suffix = Math.floor(Math.random() * 180) + 20;
  const ipA = `198.51.100.${suffix}`;
  const ipB = `203.0.113.${suffix}`;
  const headers = (ip: string) => ({
    origin,
    "sec-fetch-site": "same-origin",
    "x-requested-with": "seller-dashboard",
    "x-dashboard-test-ip": ip,
    "x-forwarded-for": "10.0.0.1",
    "x-dashboard-client-ip": "10.0.0.2",
  });
  const data = {
    email: `unknown-${randomUUID()}@example.com`,
    password: randomBytes(18).toString("base64url"),
  };
  let status = 0;
  for (let n = 0; n < 12; n++) {
    const r = await request.post("/bff/auth/login", {
      headers: headers(ipA),
      data,
    });
    status = r.status();
    if (status === 429) {
      expect(Number(r.headers()["retry-after"])).toBeGreaterThan(0);
      break;
    }
    expect(status).toBe(403);
  }
  expect(status).toBe(429);
  expect(
    (
      await request.post("/bff/auth/login", { headers: headers(ipB), data })
    ).status(),
  ).toBe(403);
  await page.setExtraHTTPHeaders({ "x-dashboard-test-ip": ipA });
  await page.goto("/login");
  await page.getByLabel("Email address").fill(data.email);
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByText(/Try again in \d+ seconds/)).toBeVisible();
  const token = randomBytes(32).toString("base64url");
  let trackingStatus = 0;
  for (let n = 0; n < 65; n++) {
    const r = await request.get("/bff/api/public/track/" + token, {
      headers: headers(ipA),
    });
    trackingStatus = r.status();
    if (trackingStatus === 429) break;
    expect(trackingStatus).toBe(404);
  }
  expect(trackingStatus).toBe(429);
  expect(
    (
      await request.get("/bff/api/public/track/" + token, {
        headers: headers(ipB),
      })
    ).status(),
  ).toBe(404);
  console.log(
    "PASS F11: login and public tracking independently rate-limited per visitor THROUGH BFF; spoofed XFF stripped; visible countdown",
  );
});

test("security headers, CSP nonce and mutation CSRF on real server", async ({
  request,
  page,
}) => {
  const response = await page.goto("/login");
  const csp = response?.headers()["content-security-policy"] || "";
  expect(csp).toContain("connect-src 'self'");
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).not.toContain("'unsafe-inline'");
  const nonce = /nonce-([^']+)/.exec(csp)?.[1];
  expect(nonce).toBeTruthy();
  const inline = await page
    .locator("script:not([src])")
    .evaluateAll((nodes) => nodes.map((n) => n.nonce));
  expect(inline.length).toBeGreaterThan(0);
  expect(inline.every((n) => n === nonce)).toBe(true);
  expect(response?.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response?.headers()["referrer-policy"]).toBe("no-referrer");
  const blocked = await request.post("/bff/auth/logout", {
    headers: {
      origin: "https://evil.test",
      "sec-fetch-site": "cross-site",
      "x-requested-with": "seller-dashboard",
    },
  });
  expect(blocked.status()).toBe(403);
  expect((await request.get("/bff/api/system/metrics")).status()).toBe(404);
  await page.goto("/rate-limited?retry=2");
  await expect(page.getByText(/Try again in \d+ seconds/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Try again", exact: true }),
  ).toBeEnabled({ timeout: 5000 });
  console.log(
    "PASS BFF CSP nonce/header/CSRF/path guards and retry screen on real server",
  );
});
