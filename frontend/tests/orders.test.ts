import { expect, it } from "vitest";
import { orderQuery, failureMessage, createOrderSchema } from "@/lib/orders";
import { EventDecoder } from "@/lib/order-stream";

it("builds server pagination and exact filters with inclusive local date bounds", () => {
  const query = new URLSearchParams(
    orderQuery({
      offset: 20,
      status: "paid",
      search: "ref & one",
      from: "2026-11-01",
      to: "2026-11-02",
    }),
  );
  expect(query.get("offset")).toBe("20");
  expect(query.get("limit")).toBe("20");
  expect(query.get("external_reference")).toBe("ref & one");
  expect(query.get("status")).toBe("paid");
  expect(new Date(query.get("created_to")!).getMilliseconds()).toBe(999);
});
it("validates order quantities and buyer fields before sending", () => {
  const good = {
    items: [{ sku: "TEA", quantity: 1 }],
    payment_method: "cod",
    buyer_name: "",
    buyer_email: "",
    buyer_phone: "",
    external_reference: "",
  };
  expect(createOrderSchema.safeParse(good).success).toBe(true);
  expect(
    createOrderSchema.safeParse({
      ...good,
      items: [{ sku: "TEA", quantity: 0 }],
    }).success,
  ).toBe(false);
  expect(
    createOrderSchema.safeParse({ ...good, buyer_email: "bad" }).success,
  ).toBe(false);
});
it("maps every known failure without displaying raw diagnostics", () => {
  for (const code of [
    "INSUFFICIENT_INVENTORY",
    "PAYMENT_DECLINED",
    "SHIPMENT_FAILED",
    "RETRIES_EXHAUSTED",
    "PAYMENT_EXPIRED",
    "LATE_PAYMENT",
    "AMOUNT_MISMATCH",
  ])
    expect(failureMessage(code)).not.toBe(
      failureMessage("PRIVATE_STACK_TRACE"),
    );
  expect(failureMessage("PRIVATE_STACK_TRACE")).not.toContain("PRIVATE");
});
it("decodes fragmented CRLF frames, preserves 64-bit cursors and deduplicates replay without dropping late lower IDs", () => {
  const decoder = new EventDecoder();
  expect(
    decoder.push(
      'id: 9007199254740993\r\nevent: order.status_changed\r\ndata: {"order_id":"a","to":"paid"}\r\n',
    ),
  ).toEqual([]);
  expect(decoder.push("\r\n")).toHaveLength(1);
  expect(decoder.cursor).toBe("9007199254740993");
  expect(
    decoder.push('id: 2\ndata: {"order_id":"b","to":"created"}\n\n'),
  ).toHaveLength(1);
  expect(
    decoder.push('id: 2\ndata: {"order_id":"b","to":"created"}\n\n'),
  ).toEqual([]);
  expect(decoder.cursor).toBe("9007199254740993");
});
