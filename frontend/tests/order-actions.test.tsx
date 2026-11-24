import { it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OrderDetail } from "@/components/order-detail";
import { strings as s } from "@/lib/strings";
import type { Order } from "@/lib/orders";
it("confirms tracking, shipment delivery and link rotation through the BFF", async () => {
  const order: Order = {
    id: "00000000-0000-4000-8000-000000000001",
    status: "shipped",
    total_amount: "10.00",
    currency: "INR",
    payment_method: "cod",
    payment_status: "pending",
    payment_expires_at: null,
    checkout: null,
    external_reference: "Parcel",
    buyer_name: null,
    buyer_email: null,
    buyer_phone: null,
    failure_reason: null,
    tracking_token: "test",
    tracking_url: "https://example.test/track/test",
    items: [],
    payments: [],
    shipments: [
      {
        id: "shipment",
        status: "in_transit",
        carrier: "manual",
        tracking_number: null,
        tracking_url: null,
        estimated_delivery: null,
      },
    ],
  };
  const writes: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("stream")) throw new Error("offline");
      if (init?.method && init.method !== "GET") {
        writes.push({ url, init });
        return Response.json({});
      }
      return Response.json(
        url.includes("activity")
          ? { timeline: [], payments: [], shipments: [], refunds: [] }
          : order,
      );
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <OrderDetail tenant="a" id={order.id} />
    </QueryClientProvider>,
  );
  await screen.findByText("Parcel");
  fireEvent.click(screen.getByRole("button", { name: s.orders.tracking }));
  fireEvent.change(screen.getByLabelText(s.orders.number), {
    target: { value: "TRACK-123" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.orders.confirm }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(JSON.parse(String(writes[0].init.body)).tracking_number).toBe(
    "TRACK-123",
  );
  expect(writes[0].init.method).toBe("PATCH");
  fireEvent.click(screen.getByRole("button", { name: s.orders.event }));
  fireEvent.change(screen.getByLabelText(s.orders.status), {
    target: { value: "delivered" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.orders.confirm }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(JSON.parse(String(writes[1].init.body)).status).toBe("delivered");
  fireEvent.click(screen.getByRole("button", { name: s.orders.rotate }));
  expect(writes).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: s.orders.confirm }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2].url).toContain("/tracking/rotate");
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
