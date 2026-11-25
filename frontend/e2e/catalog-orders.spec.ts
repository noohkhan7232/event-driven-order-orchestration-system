import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { randomBytes, randomInt } from "node:crypto";

async function register(page: Page, id: string) {
  await page.setExtraHTTPHeaders({
    "X-Dashboard-Test-IP": `198.18.${randomInt(256)}.${randomInt(1, 255)}`,
  });
  const password = randomBytes(18).toString("base64url");
  const email = `catalog-${id}@example.com`;
  await page.goto("/tenants/register");
  await page.getByLabel("Store name", { exact: true }).fill("Catalog " + id);
  await page.getByLabel("Your name").fill("Fixture owner");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Create store" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}
async function accessible(page: Page) {
  const result = await new AxeBuilder({ page }).analyze();
  expect(
    result.violations.filter((v) =>
      ["serious", "critical"].includes(v.impact || ""),
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
async function createOrder(
  page: Page,
  sku: string,
  reference: string,
  method: "cod" | "prepaid",
) {
  await page.goto("/orders/new");
  await page.getByLabel("SKU", { exact: true }).fill(sku);
  await page.getByLabel("Payment", { exact: true }).selectOption(method);
  await page.getByLabel("External reference", { exact: true }).fill(reference);
  await page
    .getByRole("button", { name: "Create test order", exact: true })
    .click();
  await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/);
  return page.url();
}
test("F2 F3 F4 F10: catalog, stock, live orders, COD and tenant isolation", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  const id = randomBytes(5).toString("hex");
  const sku = "TEA-" + id;
  await register(page, id);
  await page.goto("/inventory");
  await page
    .getByRole("button", { name: "Create warehouse", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Warehouse code").fill("MAIN");
  await dialog.getByLabel("Warehouse name").fill("Main warehouse");
  await dialog.getByLabel("Region", { exact: true }).fill("India");
  await dialog
    .getByRole("button", { name: "Create warehouse", exact: true })
    .click();
  await expect(dialog).toBeHidden();
  await page.goto("/catalog");
  await page
    .getByRole("button", { name: "Create product", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("SKU", { exact: true }).fill(sku);
  await dialog.getByLabel("Product name").fill("Fixture tea " + id);
  await dialog.getByLabel("Price", { exact: true }).fill("249.50");
  await dialog.getByRole("button", { name: "Save product" }).click();
  await expect(page).toHaveURL(/\/catalog\/[0-9a-f-]{36}$/);
  const productUrl = page.url();
  await accessible(page);
  await page.goto("/catalog");
  await accessible(page);
  await page.goto("/inventory");
  await page
    .getByRole("button", { name: "Add inventory", exact: true })
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("SKU", { exact: true }).fill(sku);
  await dialog
    .getByLabel("Warehouse", { exact: true })
    .selectOption({ label: "MAIN · Main warehouse" });
  await dialog.getByLabel("Available", { exact: true }).fill("20");
  await dialog
    .getByRole("button", { name: "Add inventory", exact: true })
    .click();
  await expect(dialog).toBeHidden();
  await page.getByRole("button", { name: "Adjust stock" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Quantity change").fill("-21");
  await dialog.getByLabel("Adjustment reason").fill("Synthetic count");
  await dialog.getByRole("button", { name: "Save adjustment" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await dialog.getByLabel("Quantity change").fill("5");
  await dialog.getByRole("button", { name: "Save adjustment" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("25", { exact: true })).toBeVisible();
  await accessible(page);
  console.log(
    "PASS F2 warehouse/product/inventory UI; adjustment +5; negative result rejected; axe/responsive catalog/detail/inventory",
  );
  const list = await context.newPage();
  await list.goto("/orders");
  await expect(list.getByText("Live", { exact: true })).toBeVisible();
  const prepaid = "PRE-" + id;
  const orderUrl = await createOrder(page, sku, prepaid, "prepaid");
  const card = list.getByRole("link").filter({ hasText: prepaid });
  await expect(card).toBeVisible({ timeout: 60000 });
  await expect(
    card.getByText("Preparing shipment", { exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await accessible(list);
  await page.goto(orderUrl);
  await expect(
    page.getByRole("heading", { name: "Order journey" }),
  ).toBeVisible();
  await expect(page.getByText("Stock reserved", { exact: true })).toBeVisible();
  await accessible(page);
  console.log(
    "PASS F3 order appears and reaches fulfillment on already-open Live list without manual refresh; recorded saga timeline",
  );
  await list.close();
  await createOrder(page, sku, "COD-" + id, "cod");
  await expect(page.getByRole("button", { name: "Edit tracking" })).toBeVisible(
    { timeout: 60000 },
  );
  await page.getByRole("button", { name: "Edit tracking" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Carrier", { exact: true }).fill("Fixture carrier");
  await dialog
    .getByLabel("Tracking number", { exact: true })
    .fill("TRACK-" + id);
  await dialog
    .getByLabel("Estimated delivery", { exact: true })
    .fill("2026-12-25");
  const trackingSaved = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      response.url().includes("/bff/api/shipments/"),
  );
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  expect((await trackingSaved).status()).toBe(200);
  await expect(dialog).toBeHidden();
  await expect(page.locator('time[datetime="2026-12-25"]')).toHaveAttribute(
    "title",
    /IST$/,
  );
  await expect(page.locator('time[datetime="2026-12-25"]')).not.toHaveText(
    "2026-12-25",
  );
  for (const status of ["in_transit", "delivered"]) {
    await page.getByRole("button", { name: "Add shipment event" }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("Status", { exact: true }).selectOption(status);
    await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(dialog).toBeHidden();
    if (status === "in_transit")
      await expect(
        page
          .locator("header")
          .filter({ hasText: "COD-" + id })
          .getByText("Shipped", { exact: true }),
      ).toBeVisible({ timeout: 60000 });
  }
  await expect(page.getByText("Collected", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  console.log(
    "PASS F4 COD tracking -> shipped -> delivered; payment Collected",
  );
  const other = await browser.newContext();
  try {
    const otherPage = await other.newPage();
    await register(otherPage, "other-" + id);
    await otherPage.goto(orderUrl);
    await expect(
      otherPage.getByText("This record was not found.", { exact: true }),
    ).toBeVisible();
    await otherPage.goto(productUrl);
    await expect(
      otherPage.getByText("This record was not found.", { exact: true }),
    ).toBeVisible();
    await otherPage.goto("/orders");
    await expect(
      otherPage.getByText("No orders match these filters."),
    ).toBeVisible();
    await otherPage.goto("/catalog");
    await expect(
      otherPage.getByText("No products match these filters."),
    ).toBeVisible();
    console.log(
      "PASS F10 foreign order/product clean not-found; tenant lists isolated",
    );
  } finally {
    await other.close();
  }
});
