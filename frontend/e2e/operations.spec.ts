import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { randomBytes, randomInt, createHmac } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

test.use({ screenshot: "off", trace: "off" });
const root = fileURLToPath(new URL("../../", import.meta.url));
const origin = process.env.E2E_FRONTEND_URL || "http://localhost:3000";
const apiOrigin = process.env.E2E_API_URL || "http://127.0.0.1:18000";
const sinkOrigin = process.env.E2E_SINK_URL || "http://127.0.0.1:18090";
for (const value of [origin, apiOrigin, sinkOrigin])
  if (!["localhost", "127.0.0.1", "[::1]"].includes(new URL(value).hostname))
    throw new Error("Operations proof requires loopback test services");
function fixture<T>(value: unknown): T {
  try {
    return JSON.parse(
      execFileSync(
        "docker",
        [
          "compose",
          "exec",
          "-T",
          "api",
          "python",
          "-m",
          "frontend.e2e.fixtures.operations",
        ],
        {
          cwd: root,
          input: JSON.stringify(value),
          encoding: "utf8",
          timeout: 30000,
        },
      ),
    ) as T;
  } catch {
    throw new Error("Scoped local fixture command failed");
  }
}
async function bff(
  context: BrowserContext,
  path: string,
  method = "GET",
  data?: unknown,
) {
  return context.request.fetch(origin + "/bff/api/" + path, {
    method,
    headers: {
      origin,
      "sec-fetch-site": "same-origin",
      "x-requested-with": "seller-dashboard",
      ...(path === "orders" && method === "POST"
        ? { "Idempotency-Key": randomBytes(16).toString("hex") }
        : {}),
    },
    ...(data === undefined ? {} : { data }),
  });
}
async function login(page: Page, email: string, password: string) {
  await page.goto(origin + "/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/(dashboard|platform)$/);
}
async function confirm(
  page: Page,
  path: string,
  status: number,
  method = "POST",
) {
  const response = page.waitForResponse(
    (value) =>
      value.url().includes(path) && value.request().method() === method,
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Confirm", exact: true })
    .click();
  expect((await response).status()).toBe(status);
  await expect(page.getByRole("dialog")).toBeHidden({ timeout: 20000 });
}
async function takeSecret(page: Page) {
  try {
    const field = page.getByLabel("Secret value");
    await expect(field).toBeVisible();
    const value = await field.inputValue();
    const gated = await page
      .getByRole("button", { name: "Done", exact: true })
      .isDisabled();
    await page.getByLabel("I have saved it").check();
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(field).toBeHidden();
    const absent = await page.evaluate(
      (secret) =>
        !document.body.textContent?.includes(secret) &&
        !JSON.stringify(localStorage).includes(secret) &&
        !JSON.stringify(sessionStorage).includes(secret),
      value,
    );
    if (!value || !gated || !absent) throw new Error("Unsafe secret lifecycle");
    return value;
  } catch {
    await page.close();
    throw new Error("Secret disclosure did not complete safely");
  }
}
async function accessible(page: Page) {
  const result = await new AxeBuilder({ page }).analyze();
  expect(
    result.violations.filter((value) =>
      ["serious", "critical"].includes(value.impact || ""),
    ),
  ).toEqual([]);
  for (const width of [360, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
}

test("F6 F7 F8 F9 F12: owner operations, transient secrets, recovery and platform controls", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(360000);
  const id = randomBytes(5).toString("hex"),
    password = randomBytes(24).toString("base64url"),
    email = `operations-owner-${id}@example.com`,
    slug = "operations-" + id,
    sku = "OPS-" + id;
  let tenantId = "",
    rootId = "";
  const extraContexts: BrowserContext[] = [];
  try {
    await page.setExtraHTTPHeaders({
      "X-Dashboard-Test-IP": `198.18.${randomInt(256)}.${randomInt(1, 255)}`,
    });
    await page.goto("/tenants/register");
    await page
      .getByLabel("Store name", { exact: true })
      .fill("Operations fixture " + id);
    await page.getByLabel("Your name").fill("Operations fixture");
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByLabel("Store URL name (optional)").fill(slug);
    const registered = page.waitForResponse(
      (value) =>
        value.url().endsWith("/bff/auth/register") &&
        value.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Create store", exact: true })
      .click();
    expect((await registered).status()).toBe(201);
    await expect(page).toHaveURL(/\/login/, { timeout: 20000 });
    await login(page, email, password);
    const profile = (await (await bff(context, "auth/me")).json()) as {
      id: string;
      tenant_id: string;
    };
    tenantId = profile.tenant_id;
    const ownerId = profile.id;
    const roles = fixture<{
      vendor: { id: string; email: string };
      root: { id: string; email: string };
    }>({ kind: "roles", tenant_id: tenantId, password });
    rootId = roles.root.id;
    const warehouse = await bff(context, "inventory/warehouses", "POST", {
      code: "OPS",
      name: "Operations warehouse",
      region: "India",
    });
    expect(warehouse.status()).toBe(201);
    const warehouseId = ((await warehouse.json()) as { id: string }).id;
    expect(
      (
        await bff(context, "products", "POST", {
          sku,
          name: "Operations tea",
          price: "12.34",
          currency: "INR",
        })
      ).status(),
    ).toBe(201);
    expect(
      (
        await bff(context, "inventory", "POST", {
          sku,
          warehouse_id: warehouseId,
          available_quantity: 30,
        })
      ).status(),
    ).toBe(201);
    await page.goto("/payments");
    const providerSecret = "not-a-real-provider-secret-" + id,
      webhookSecret = "not-a-real-webhook-secret-" + id;
    await page
      .getByLabel("Razorpay key ID", { exact: true })
      .fill("rzp_test_" + id);
    await page
      .getByLabel("Razorpay key secret", { exact: true })
      .fill(providerSecret);
    await page
      .getByLabel("Razorpay webhook secret", { exact: true })
      .fill(webhookSecret);
    await page
      .getByRole("button", { name: "Connect Razorpay", exact: true })
      .click();
    await expect(
      page.getByText("Saved key ID: ****" + id.slice(-4), { exact: true }),
    ).toBeVisible();
    expect(
      await page
        .getByLabel("Razorpay key secret", { exact: true })
        .inputValue(),
    ).toBe("");
    expect(
      await page
        .getByLabel("Razorpay webhook secret", { exact: true })
        .inputValue(),
    ).toBe("");
    expect(
      await page.evaluate(
        (value) => !document.body.textContent?.includes(value),
        providerSecret,
      ),
    ).toBe(true);
    expect(
      (await page.getByLabel("Inbound webhook URL").inputValue()).includes(
        "/api/v1/webhooks/payments/razorpay/",
      ),
    ).toBe(true);
    await accessible(page);
    await page
      .getByRole("button", { name: "Disconnect Razorpay", exact: true })
      .click();
    await confirm(page, "/settings/payments/razorpay", 204, "DELETE");
    console.log(
      "PASS F8 write-only dummy Razorpay settings; masked readback; secrets cleared; inbound URL; disconnect; no provider call",
    );
    await page.goto("/api-keys");
    await page
      .getByRole("button", { name: "Create API key", exact: true })
      .click();
    await page
      .getByLabel("Name", { exact: true })
      .fill("Operations integration");
    await page
      .getByLabel("Read orders and their status", { exact: true })
      .check();
    await page.getByLabel("Create and cancel orders", { exact: true }).check();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Create API key", exact: true })
      .click();
    let key = await takeSecret(page);
    const placed = await fetch(apiOrigin + "/api/v1/orders", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-API-Key": key,
        "Idempotency-Key": randomBytes(16).toString("hex"),
      },
      body: JSON.stringify({
        items: [{ sku, quantity: 1 }],
        payment_method: "cod",
      }),
    });
    expect(placed.status).toBe(202);
    await accessible(page);
    await page.getByRole("button", { name: "Revoke key", exact: true }).click();
    await confirm(page, "/api-keys/", 204, "DELETE");
    expect(
      (
        await fetch(apiOrigin + "/api/v1/orders", {
          headers: { "X-API-Key": key },
        })
      ).status,
    ).toBe(401);
    key = "";
    console.log(
      "PASS F6 show-once API key; saved gate; real scoped order call; revoke ->401; no browser storage",
    );
    await page.goto("/notifications");
    await page
      .getByLabel("Send order notifications", { exact: true })
      .uncheck();
    await page
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (
            (await (await bff(context, "settings/notifications")).json()) as {
              notifications_enabled: boolean;
            }
          ).notifications_enabled,
      )
      .toBe(false);
    await accessible(page);
    await page.goto("/webhooks");
    await page
      .getByRole("button", { name: "Add endpoint", exact: true })
      .click();
    await page
      .getByLabel("Receiver URL", { exact: true })
      .fill("http://webhook-sink:8080/receiver");
    await page.getByLabel("Order received", { exact: true }).check();
    await page.getByLabel("Test ping", { exact: true }).check();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    let signingSecret = await takeSecret(page);
    await page
      .getByRole("button", { name: "Rotate signing secret", exact: true })
      .click();
    const rotateResponse = page.waitForResponse(
      (value) =>
        value.url().includes("/rotate-secret") &&
        value.request().method() === "POST",
    );
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm", exact: true })
      .click();
    expect((await rotateResponse).status()).toBe(200);
    signingSecret = await takeSecret(page);
    const endpoints = (await (
      await bff(context, "webhook-endpoints")
    ).json()) as { items: { id: string }[] };
    const endpointId = endpoints.items[0].id;
    await page.getByRole("button", { name: "Deliveries", exact: true }).click();
    expect(
      (
        await fetch(sinkOrigin + "/config", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ failures: 1, clear: true }),
        })
      ).status,
    ).toBe(200);
    await page
      .getByRole("button", { name: "Queue test ping", exact: true })
      .click();
    await confirm(page, "/test", 202);
    await expect(
      page.getByRole("cell", { name: "503", exact: true }),
    ).toBeVisible({ timeout: 45000 });
    const failedRow = page
      .getByRole("row")
      .filter({ has: page.getByRole("cell", { name: "503", exact: true }) });
    await failedRow
      .getByRole("button", { name: "Replay delivery", exact: true })
      .click();
    await confirm(page, "/replay", 200);
    await expect(
      page.getByRole("cell", { name: "Delivered", exact: true }),
    ).toBeVisible({ timeout: 45000 });
    const received = (await (await fetch(sinkOrigin + "/received")).json()) as {
      received: {
        body: string;
        headers: Record<string, string>;
        status: number;
      }[];
    };
    const delivered = received.received.find(
      (row) =>
        row.status === 204 &&
        (JSON.parse(row.body) as { tenant_id: string }).tenant_id === tenantId,
    );
    if (!delivered)
      throw new Error("Local receiver did not record this tenant's delivery");
    const headers = Object.fromEntries(
      Object.entries(delivered.headers).map(([name, value]) => [
        name.toLowerCase(),
        value,
      ]),
    );
    const expected = createHmac("sha256", signingSecret)
      .update(headers["x-oos-timestamp"] + ".")
      .update(Buffer.from(delivered.body))
      .digest("hex");
    expect(
      headers["x-oos-signature"] ===
        "t=" + headers["x-oos-timestamp"] + ",v1=" + expected,
    ).toBe(true);
    signingSecret = "";
    expect(
      (
        await bff(context, "orders", "POST", {
          items: [{ sku, quantity: 1 }],
          payment_method: "cod",
          buyer_email: "synthetic-buyer@example.com",
          external_reference: "OPS-HOOK-" + id,
        })
      ).status(),
    ).toBe(202);
    await expect(
      page.getByRole("row").filter({ hasText: "Order received" }),
    ).toBeVisible({ timeout: 45000 });
    const deliveries = (await (
      await bff(context, `webhook-endpoints/${endpointId}/deliveries`)
    ).json()) as { items: { payload: unknown }[] };
    expect(
      JSON.stringify(deliveries).includes("synthetic-buyer@example.com"),
    ).toBe(false);
    await accessible(page);
    await page
      .getByRole("button", { name: "Remove endpoint", exact: true })
      .click();
    await confirm(page, "/webhook-endpoints/", 204, "DELETE");
    console.log(
      "PASS F7 endpoint create/rotate; show-once secrets; local signed delivery; failure503; replay ->delivered; safe order event; remove",
    );
    const jobs = fixture<{ id: string; order_id: string }[]>({
      kind: "jobs",
      tenant_id: tenantId,
      owner_id: ownerId,
      sku,
    });
    await page.goto("/failed-jobs");
    await page.locator(`a[href="/failed-jobs/${jobs[0].id}"]`).click();
    await page.getByRole("button", { name: "Replay job", exact: true }).click();
    await confirm(page, "/replay", 202);
    await expect(page.getByText("Replayed", { exact: true })).toBeVisible();
    const duplicate = await bff(
      context,
      `failed-jobs/${jobs[0].id}/replay`,
      "POST",
    );
    expect(duplicate.status()).toBe(409);
    await expect
      .poll(
        async () => {
          const recovered = await bff(context, "orders/" + jobs[0].order_id);
          expect(recovered.status()).toBe(200);
          return ((await recovered.json()) as { status: string }).status;
        },
        { timeout: 45000 },
      )
      .toBe("fulfillment_requested");
    await accessible(page);
    await page.goto("/failed-jobs/" + jobs[1].id);
    await page
      .getByRole("button", { name: "Discard job", exact: true })
      .click();
    await confirm(page, "/discard", 204);
    await expect(page.getByText("Discarded", { exact: true })).toBeVisible();
    console.log(
      "PASS F9 technical fixture replay ->fulfillment; duplicate409; discard; safe job detail",
    );
    const vendorContext = await browser.newContext();
    extraContexts.push(vendorContext);
    const vendor = await vendorContext.newPage();
    await login(vendor, roles.vendor.email, password);
    for (const path of [
      "api-keys",
      "payments",
      "webhooks",
      "notifications",
      "failed-jobs",
    ]) {
      await vendor.goto(origin + "/" + path);
      await expect(
        vendor.getByRole("heading", { name: "404", exact: true }),
      ).toBeVisible();
    }
    for (const path of [
      "api-keys",
      "settings/payments/razorpay",
      "webhook-endpoints",
      "settings/notifications",
      "failed-jobs",
    ]) {
      expect((await bff(vendorContext, path)).status()).toBe(403);
    }
    const rootContext = await browser.newContext();
    extraContexts.push(rootContext);
    const platform = await rootContext.newPage();
    await login(platform, roles.root.email, password);
    await expect(platform).toHaveURL(/\/platform$/);
    await platform
      .getByLabel("Store name or URL name", { exact: true })
      .fill(slug);
    await platform
      .getByRole("button", { name: "Apply filters", exact: true })
      .click();
    await expect(
      platform.getByText("Operations fixture " + id, { exact: true }),
    ).toBeVisible();
    await accessible(platform);
    await platform
      .getByRole("button", { name: "Suspend store", exact: true })
      .click();
    await confirm(platform, "/suspend", 200);
    await page.goto("/orders");
    await expect(page).toHaveURL(/\/suspended$/);
    await platform
      .getByRole("button", { name: "Activate store", exact: true })
      .click();
    await confirm(platform, "/activate", 200);
    await page.goto("/orders");
    await expect(
      page.getByRole("heading", { name: "Orders", exact: true }),
    ).toBeVisible();
    console.log(
      "PASS F12 vendor pages+BFF denied; platform search/suspend/activate; owner suspended screen; axe/responsive settings/operations",
    );
  } finally {
    await Promise.all(extraContexts.map((value) => value.close()));
    await Promise.all(context.pages().map((value) => value.close()));
    if (tenantId)
      fixture({
        kind: "cleanup",
        tenant_id: tenantId,
        root_id: rootId || undefined,
      });
  }
});
