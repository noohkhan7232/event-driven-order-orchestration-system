import { expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import {
  keySchema,
  paymentSchema,
  endpointSchema,
  readEndpoints,
  readDeliveries,
} from "@/lib/operations";

it("maps a backend field-validation response without retaining raw diagnostic input", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json(
        {
          detail: [{ msg: "PRIVATE_DIAGNOSTIC", input: "SYNTHETIC_SECRET" }],
        },
        { status: 422 },
      ),
    ),
  );
  await expect(
    api("/bff/api/settings/payments/razorpay", { method: "PUT" }),
  ).rejects.toMatchObject({ status: 422, code: "validation_error" });
});
it("requires scoped named keys and a valid optional future expiry", () => {
  expect(
    keySchema.safeParse({
      name: "Store sync",
      scopes: ["orders:read"],
      expires_at: "",
    }).success,
  ).toBe(true);
  for (const value of [
    { name: "", scopes: ["orders:read"], expires_at: "" },
    { name: "Sync", scopes: [], expires_at: "" },
    { name: "Sync", scopes: ["admin:write"], expires_at: "" },
    { name: "Sync", scopes: ["orders:read"], expires_at: "2020-01-01T00:00" },
  ])
    expect(keySchema.safeParse(value).success).toBe(false);
});
it("validates payment settings and HTTP targets without pretending to perform DNS/SSRF checks in the browser", () => {
  expect(
    paymentSchema.safeParse({
      key_id: "rzp_test_fixture",
      key_secret: "fixture",
      webhook_secret: "fixture",
      mode: "test",
    }).success,
  ).toBe(true);
  expect(
    paymentSchema.safeParse({
      key_id: "bad id",
      key_secret: "",
      webhook_secret: "",
      mode: "test",
    }).success,
  ).toBe(false);
  for (const url of [
    "javascript:alert(1)",
    "https://user:password@example.com/hook",
    "https://example.com/hook#fragment",
  ])
    expect(
      endpointSchema.safeParse({
        url,
        events: ["order.created"],
        description: "",
        is_active: true,
      }).success,
    ).toBe(false);
  expect(
    endpointSchema.safeParse({
      url: "https://example.com/hook",
      events: [],
      description: "",
      is_active: true,
    }).success,
  ).toBe(false);
});
it("allows only public metadata into endpoint and delivery query results", () => {
  const endpoint = {
    id: "a",
    url: "https://example.com/hook",
    events: ["ping"],
    description: null,
    is_active: true,
    consecutive_failures: 0,
    disabled_at: null,
    disabled_reason: null,
    created_at: "2026-11-25T00:00:00Z",
    signing_secret: "SYNTHETIC_SECRET",
    secret_enc: "SYNTHETIC_CIPHERTEXT",
  };
  const read = readEndpoints({ total: 1, items: [endpoint] });
  expect(JSON.stringify(read)).not.toContain("SYNTHETIC");
  const deliveries = readDeliveries({
    total: 1,
    items: [
      {
        id: "d",
        event_id: "e",
        event_type: "ping",
        status: "failed",
        attempts: 1,
        last_error: "transport_error",
        last_status_code: null,
        next_attempt_at: "2026-11-25T00:00:00Z",
        delivered_at: null,
        replay_count: 0,
        created_at: "2026-11-25T00:00:00Z",
        payload: { secret: "SYNTHETIC_SECRET" },
      },
    ],
  });
  expect(JSON.stringify(deliveries)).not.toContain("SYNTHETIC");
  expect(deliveries.items[0].attempts).toBe(1);
});
