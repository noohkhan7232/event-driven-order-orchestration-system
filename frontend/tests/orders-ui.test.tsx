import { expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OrderView, OrderDetail } from "@/components/order-detail";
import { OrdersList } from "@/components/orders-list";
import { CreateOrder } from "@/components/create-order";
import type { Order } from "@/lib/orders";
import { strings as s } from "@/lib/strings";
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
export const order: Order = {
  id: "1a86b90f-1527-4c21-bc10-01c425b2c091",
  status: "created",
  total_amount: "100.00",
  currency: "INR",
  failure_reason: null,
  items: [
    {
      id: "item",
      sku: "TEA",
      product_name: "Tea",
      quantity: 1,
      unit_price: "100.00",
      warehouse_id: null,
    },
  ],
  payments: [],
  shipments: [],
  tracking_token: "synthetic",
  tracking_url: "https://shop.example.test/track/synthetic",
  payment_method: "cod",
  payment_status: "pending",
  payment_expires_at: null,
  checkout: null,
  external_reference: "Test tea",
  buyer_name: "Test Buyer",
  buyer_email: "buyer@example.test",
  buyer_phone: null,
};
const activity = {
  timeline: [{ id: "1", at: "2026-11-01T00:00:00Z", status: "created" }],
  payments: [],
  shipments: [],
  refunds: [],
};
function wrap(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>,
  );
}
it.each([
  "created",
  "payment_pending",
  "paid",
  "shipped",
  "delivered",
  "cancelled",
] as const)("renders %s details and recorded activity", (status) => {
  render(<OrderView order={{ ...order, status }} activity={activity} />);
  expect(screen.getByRole("heading", { name: "Test tea" })).toBeInTheDocument();
  expect(screen.getByText("Test Buyer")).toBeInTheDocument();
  expect(screen.getAllByText(s.statuses[status]).length).toBeGreaterThan(0);
  expect(
    screen.getByRole("heading", { name: s.orders.timeline }),
  ).toBeInTheDocument();
});
it.each(Object.keys(s.failures).filter((code) => code !== "unknown"))(
  "explains failure %s without raw diagnostics",
  (code) => {
    render(
      <OrderView
        order={{ ...order, status: "failed", failure_reason: code }}
      />,
    );
    expect(screen.getByText(s.failures[code])).toBeInTheDocument();
  },
);
it("requests filters and next server page, then displays an empty state", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) =>
    String(input).includes("stream")
      ? Promise.reject(new Error("offline"))
      : Response.json({
          items: String(input).includes("offset=20") ? [] : [order],
          total: 21,
          limit: 20,
          offset: 0,
        }),
  );
  vi.stubGlobal("fetch", fetcher);
  wrap(<OrdersList tenant="a" />);
  await screen.findByText("Test tea");
  fireEvent.change(screen.getByLabelText(s.orders.search), {
    target: { value: "reference" },
  });
  fireEvent.change(screen.getByLabelText(s.orders.status), {
    target: { value: "paid" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.orders.apply }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        ([url]) =>
          String(url).includes("status=paid") &&
          String(url).includes("external_reference=reference"),
      ),
    ).toBe(true),
  );
  fireEvent.click(await screen.findByRole("button", { name: s.orders.next }));
  await screen.findByText(s.orders.empty);
  expect(
    fetcher.mock.calls.some(([url]) => String(url).includes("offset=20")),
  ).toBe(true);
});
it("rolls back optimistic cancellation after a conflict and keeps confirmation open", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("stream")) throw new Error("offline");
      if (url.includes("/cancel"))
        return Response.json(
          { code: "invalid_order_transition" },
          { status: 409 },
        );
      return Response.json(url.includes("activity") ? activity : order);
    }),
  );
  wrap(<OrderDetail tenant="a" id={order.id} />);
  await screen.findByText("Test tea");
  fireEvent.click(screen.getByRole("button", { name: s.orders.cancel }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: s.orders.confirm }));
  await screen.findByText(s.errors.invalid_order_transition);
  expect(screen.getAllByText(s.statuses.created).length).toBeGreaterThan(0);
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});
it("creates an order with a new idempotency key, reusing it after an ambiguous transport failure", async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(init!);
      if (requests.length === 1) throw new Error("connection lost");
      return Response.json(order);
    }),
  );
  wrap(<CreateOrder />);
  fireEvent.change(screen.getByLabelText(s.orders.sku), {
    target: { value: "TEA" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.orders.create }));
  await screen.findByText(s.errors.offline);
  fireEvent.click(screen.getByRole("button", { name: s.orders.create }));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/orders/" + order.id));
  expect(new Headers(requests[0].headers).get("idempotency-key")).toBe(
    new Headers(requests[1].headers).get("idempotency-key"),
  );
  expect(JSON.parse(String(requests[1].body)).items).toEqual([
    { sku: "TEA", quantity: 1 },
  ]);
});
