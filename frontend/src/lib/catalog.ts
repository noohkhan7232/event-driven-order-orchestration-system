import { z } from "zod";
import { strings as s } from "@/lib/strings";
import type { components } from "@/lib/api/generated";
export type Product = components["schemas"]["ProductRead"];
export type Stock = components["schemas"]["InventoryRead"];
export type Warehouse = components["schemas"]["WarehouseRead"];
const required = (max: number, min = 1) =>
  z
    .string()
    .trim()
    .min(min, s.validation.required)
    .max(max, s.errors.validation_error);
export const productSchema = z.object({
  sku: required(64),
  name: required(180),
  description: z.string().max(10000, s.errors.validation_error),
  price: z.string().regex(/^\d{1,10}(\.\d{1,2})?$/, s.catalog.invalidPrice),
  currency: z.string().regex(/^[A-Z]{3}$/, s.errors.validation_error),
  is_active: z.boolean(),
});
export function decimalPrice(value: string) {
  const [whole, fraction = ""] = value.split(".");
  return `${BigInt(whole)}.${fraction.padEnd(2, "0")}`;
}
const quantity = z
  .number()
  .int(s.inventory.quantityInvalid)
  .min(0, s.inventory.quantityInvalid)
  .max(2147483647, s.inventory.quantityInvalid);
export const warehouseSchema = z.object({
  code: required(32, 2),
  name: required(120, 2),
  region: required(80, 2),
});
export const inventorySchema = z.object({
  sku: required(64),
  warehouse_id: z.uuid(s.validation.required),
  available_quantity: quantity,
  low_stock_threshold: quantity,
});
export const adjustmentSchema = (available: number) =>
  z.object({
    delta: z
      .number()
      .int(s.inventory.invalidDelta)
      .refine(
        (delta) =>
          delta !== 0 &&
          available + delta >= 0 &&
          available + delta <= 2147483647,
        s.inventory.invalidDelta,
      ),
    reason: required(200).min(1, s.inventory.reasonRequired),
  });
