import { z } from "zod";
import { ApiError } from "@/lib/api/client";
import { strings as s } from "@/lib/strings";
import type { components } from "@/lib/api/generated";
export type Key = components["schemas"]["ApiKeyRead"];
export type Endpoint = components["schemas"]["EndpointRead"];
export type Job = components["schemas"]["FailedJobRead"];
export const scopes = [
  "orders:read",
  "orders:write",
  "catalog:read",
  "catalog:write",
  "inventory:read",
  "inventory:write",
] as const;
export const events = [
  "order.created",
  "order.paid",
  "order.shipped",
  "order.delivered",
  "order.failed",
  "order.cancelled",
  "order.status_changed",
  "payment.refunded",
  "ping",
] as const;
const required = (max: number) =>
  z
    .string()
    .trim()
    .min(1, s.validation.required)
    .max(max, s.errors.validation_error);
export const keySchema = z.object({
  name: required(120),
  scopes: z.array(z.enum(scopes)).min(1, s.validation.required),
  expires_at: z
    .string()
    .refine(
      (value) =>
        !value ||
        (Number.isFinite(Date.parse(value)) && Date.parse(value) > Date.now()),
      s.ops.future,
    ),
});
export const paymentSchema = z.object({
  key_id: required(120)
    .min(8, s.errors.validation_error)
    .regex(/^[A-Za-z0-9_]+$/, s.errors.validation_error),
  key_secret: z
    .string()
    .min(1, s.validation.required)
    .max(2048, s.errors.validation_error),
  webhook_secret: z
    .string()
    .min(1, s.validation.required)
    .max(2048, s.errors.validation_error),
  mode: z.enum(["test", "live"]),
});
export const endpointSchema = z.object({
  url: required(2048).refine((value) => {
    try {
      const url = new URL(value);
      return (
        ["https:", "http:"].includes(url.protocol) &&
        !url.username &&
        !url.password &&
        !url.hash
      );
    } catch {
      return false;
    }
  }, s.errors.unsafe_webhook_url),
  events: z
    .array(z.enum(events))
    .min(1, s.validation.required)
    .max(9, s.errors.validation_error),
  description: z.string().max(200, s.errors.validation_error),
  is_active: z.boolean(),
});
function checked<T>(schema: z.ZodType<T>, value: unknown): T {
  const result = schema.safeParse(value);
  if (!result.success) throw new ApiError(502, "upstream_unavailable");
  return result.data;
}
const nullable = z.string().nullable();
const keyRead = z.object({
  id: z.string(),
  name: z.string(),
  key_prefix: z.string(),
  scopes: z.array(z.string()),
  last_used_at: nullable,
  expires_at: nullable,
  revoked_at: nullable,
});
const endpointRead = z.object({
  id: z.string(),
  url: z.string(),
  events: z.array(z.string()),
  description: nullable,
  is_active: z.boolean(),
  consecutive_failures: z.number(),
  disabled_at: nullable,
  disabled_reason: nullable,
  created_at: z.string(),
  signing_secret_hint: z.string().default("whsec_********"),
});
const deliveryRead = z.object({
  id: z.string(),
  event_id: z.string(),
  event_type: z.string(),
  status: z.enum(["pending", "delivered", "failed", "dead"]),
  attempts: z.number(),
  last_error: nullable,
  last_status_code: z.number().nullable(),
  next_attempt_at: z.string(),
  delivered_at: nullable,
  replay_count: z.number(),
  created_at: z.string(),
});
export type Delivery = z.infer<typeof deliveryRead>;
export const readKeys = (value: unknown): Key[] =>
  checked(z.array(keyRead), value);
export const readPaymentConfig = (value: unknown) =>
  checked(
    z.object({
      key_id: z.string(),
      webhook_url: z.string(),
      webhook_public_id: z.string(),
      mode: z.string(),
      active: z.boolean(),
    }),
    value,
  );
export const readEndpoints = (value: unknown) =>
  checked(z.object({ total: z.number(), items: z.array(endpointRead) }), value);
export const readDeliveries = (value: unknown) =>
  checked(z.object({ total: z.number(), items: z.array(deliveryRead) }), value);
export const readSecret = (value: unknown, field: "key" | "signing_secret") =>
  checked(z.object({ [field]: z.string().min(1) }), value)[field];
export const endpointPath = (id: string) =>
  "/bff/api/webhook-endpoints/" + encodeURIComponent(id);
