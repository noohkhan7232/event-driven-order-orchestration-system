import { expect, it } from "vitest";
import {
  productSchema,
  adjustmentSchema,
  inventorySchema,
  warehouseSchema,
  decimalPrice,
} from "@/lib/catalog";
it("accepts nonnegative prices with at most two decimals and normalizes without float arithmetic", () => {
  expect(decimalPrice("12.1")).toBe("12.10");
  expect(decimalPrice("000.00")).toBe("0.00");
  const product = {
    sku: "TEA",
    name: "Tea",
    description: "",
    price: "12.34",
    currency: "INR",
    is_active: true,
  };
  expect(productSchema.safeParse(product).success).toBe(true);
  for (const price of ["-1", "1.234", "NaN", "1e3", ""])
    expect(productSchema.safeParse({ ...product, price }).success).toBe(false);
});
it("requires a nonblank adjustment reason and prevents negative or overflowing stock", () => {
  expect(
    adjustmentSchema(3).safeParse({ delta: -3, reason: "Count correction" })
      .success,
  ).toBe(true);
  for (const value of [
    { delta: -4, reason: "Count" },
    { delta: 1, reason: "   " },
    { delta: 0.5, reason: "Count" },
    { delta: 2147483647, reason: "Count" },
  ])
    expect(adjustmentSchema(3).safeParse(value).success).toBe(false);
});
it("validates warehouse and stock creation contracts", () => {
  expect(
    warehouseSchema.safeParse({ code: "A", name: "AB", region: "IN" }).success,
  ).toBe(false);
  expect(
    inventorySchema.safeParse({
      sku: "SKU",
      warehouse_id: "invalid",
      available_quantity: -1,
      low_stock_threshold: 10,
    }).success,
  ).toBe(false);
});
