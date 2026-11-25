import { expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProductForm, Catalog } from "@/components/catalog";
import {
  AdjustmentForm,
  WarehouseForm,
  StockCards,
  InventoryForm,
} from "@/components/inventory";
import type { Product, Stock } from "@/lib/catalog";
import { strings as s } from "@/lib/strings";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const product: Product = {
  id: "product",
  sku: "TEA",
  name: "Tea",
  description: null,
  price: "12.00",
  currency: "INR",
  is_active: true,
};
const stock: Stock = {
  id: "stock",
  sku: "TEA",
  product_name: "Tea",
  warehouse_id: "00000000-0000-4000-8000-000000000001",
  available_quantity: 3,
  reserved_quantity: 2,
  low_stock_threshold: 5,
};
function wrap(children: React.ReactNode) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      {children}
    </QueryClientProvider>,
  );
}
it("keeps product SKU and currency immutable and sends only supported update fields", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(product));
  vi.stubGlobal("fetch", fetcher);
  const done = vi.fn();
  wrap(<ProductForm tenant="a" product={product} onDone={done} />);
  expect(screen.getByLabelText(s.orders.sku)).toHaveAttribute("readonly");
  expect(screen.getByLabelText(s.catalog.currency)).toHaveAttribute("readonly");
  fireEvent.change(screen.getByLabelText(s.catalog.price), {
    target: { value: "12.5" },
  });
  fireEvent.click(screen.getByLabelText(s.catalog.active));
  fireEvent.click(screen.getByRole("button", { name: s.catalog.save }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  const body = JSON.parse(fetcher.mock.calls[0][1].body);
  expect(body).toEqual({
    name: "Tea",
    description: null,
    price: "12.50",
    is_active: false,
  });
});
it("shows duplicate SKU errors inline without closing the form", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(Response.json({ code: "conflict" }, { status: 409 })),
  );
  wrap(<ProductForm tenant="a" onDone={vi.fn()} />);
  fireEvent.change(screen.getByLabelText(s.orders.sku), {
    target: { value: "TEA" },
  });
  fireEvent.change(screen.getByLabelText(s.catalog.name), {
    target: { value: "Tea" },
  });
  fireEvent.change(screen.getByLabelText(s.catalog.price), {
    target: { value: "12.34" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.catalog.save }));
  await screen.findByText(s.errors.product_sku_conflict);
  expect(screen.getByLabelText(s.orders.sku)).toHaveValue("TEA");
});
it("filters catalog on the server and confirms archive", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return Response.json(
        init?.method === "PATCH"
          ? product
          : { items: [product], total: 1, limit: 20, offset: 0 },
      );
    }),
  );
  wrap(<Catalog tenant="a" />);
  await screen.findByText("Tea");
  fireEvent.change(screen.getByLabelText(s.catalog.search), {
    target: { value: "TE" },
  });
  fireEvent.change(screen.getByLabelText(s.catalog.activeFilter), {
    target: { value: "true" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.orders.apply }));
  await waitFor(() =>
    expect(
      calls.some(
        (call) =>
          call.url.includes("q=TE") && call.url.includes("is_active=true"),
      ),
    ).toBe(true),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: s.catalog.archive }),
  );
  expect(calls.every((call) => call.init?.method !== "PATCH")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: s.orders.confirm }));
  await waitFor(() =>
    expect(
      calls.some(
        (call) =>
          call.init?.method === "PATCH" &&
          JSON.parse(String(call.init.body)).is_active === false,
      ),
    ).toBe(true),
  );
});
it("blocks negative adjustment locally, then handles concurrent backend negative_stock", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      Response.json({ code: "negative_stock" }, { status: 422 }),
    );
  vi.stubGlobal("fetch", fetcher);
  const done = vi.fn();
  wrap(<AdjustmentForm tenant="a" stock={stock} onDone={done} />);
  fireEvent.change(screen.getByLabelText(s.inventory.delta), {
    target: { value: "-4" },
  });
  fireEvent.change(screen.getByLabelText(s.inventory.reason), {
    target: { value: "Count correction" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.inventory.save }));
  await screen.findByText(s.inventory.invalidDelta);
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText(s.inventory.delta), {
    target: { value: "-2" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.inventory.save }));
  await screen.findByText(s.errors.negative_stock);
  expect(done).not.toHaveBeenCalled();
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    delta: -2,
    reason: "Count correction",
  });
});
it("requires a reason for adjustments and renders available/reserved with a textual low-stock warning", async () => {
  wrap(
    <>
      <StockCards items={[stock]} warehouses={[]} onAdjust={vi.fn()} />
      <AdjustmentForm tenant="a" stock={stock} onDone={vi.fn()} />
    </>,
  );
  expect(screen.getByText(s.inventory.low)).toBeInTheDocument();
  expect(screen.getByText(s.inventory.reserved)).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(s.inventory.delta), {
    target: { value: "1" },
  });
  fireEvent.click(screen.getByRole("button", { name: s.inventory.save }));
  await screen.findByRole("alert");
  expect(fetch).not.toHaveBeenCalled();
});
it("maps warehouse and inventory duplicate errors in their forms", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ code: "warehouse_code_conflict" }, { status: 409 }),
      ),
  );
  const view = wrap(<WarehouseForm tenant="a" onDone={vi.fn()} />);
  for (const [label, value] of [
    [s.inventory.code, "MAIN"],
    [s.inventory.name, "Main"],
    [s.inventory.region, "IN"],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(
    screen.getByRole("button", { name: s.inventory.createWarehouse }),
  );
  await screen.findByText(s.errors.warehouse_code_conflict);
  view.unmount();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ code: "inventory_conflict" }, { status: 409 }),
      ),
  );
  wrap(
    <InventoryForm
      tenant="a"
      warehouses={[
        { id: stock.warehouse_id, code: "MAIN", name: "Main", region: "IN" },
      ]}
      onDone={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText(s.orders.sku), {
    target: { value: "TEA" },
  });
  fireEvent.change(screen.getByLabelText(s.inventory.warehouse), {
    target: { value: stock.warehouse_id },
  });
  fireEvent.click(screen.getByRole("button", { name: s.inventory.add }));
  await screen.findByText(s.errors.inventory_conflict);
});
