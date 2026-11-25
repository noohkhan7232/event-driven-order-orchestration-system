import { z } from "zod";
import { strings as s } from "@/lib/strings";
import type { components } from "@/lib/api/generated";
export type Order = components["schemas"]["OrderRead"];
export const statuses = [
  "created",
  "validated",
  "inventory_reserved",
  "payment_pending",
  "paid",
  "fulfillment_requested",
  "shipped",
  "delivered",
  "cancelled",
  "failed",
] as const;
export function orderQuery(filters: {
  offset: number;
  status: string;
  search: string;
  from: string;
  to: string;
}) {
  const query = new URLSearchParams({
    limit: "20",
    offset: String(filters.offset),
  });
  if (filters.status) query.set("status", filters.status);
  if (filters.search.trim())
    query.set("external_reference", filters.search.trim());
  if (filters.from)
    query.set(
      "created_from",
      new Date(filters.from + "T00:00:00").toISOString(),
    );
  if (filters.to)
    query.set(
      "created_to",
      new Date(filters.to + "T23:59:59.999").toISOString(),
    );
  return query.toString();
}
export const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const createOrderSchema = z.object({
  items: z
    .array(
      z.object({
        sku: z.string().trim().min(1, s.validation.required).max(64),
        quantity: z.number().int().min(1).max(500),
      }),
    )
    .min(1)
    .max(100),
  payment_method: z.enum(["prepaid", "cod"]),
  external_reference: z.string().trim().max(100),
  buyer_name: z.string().trim().max(120),
  buyer_email: z.union([z.literal(""), z.email(s.validation.email)]),
  buyer_phone: z.union([
    z.literal(""),
    z.string().regex(/^\+?[0-9][0-9 ()-]{3,18}$/, s.validation.phone),
  ]),
});
export function failureMessage(code: string | null) {
  return s.failures[code || ""] || s.failures.unknown;
}
export function safeLink(value: string | null | undefined) {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
