"use client";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import {
  adjustmentSchema,
  warehouseSchema,
  inventorySchema,
  type Stock,
  type Warehouse,
} from "@/lib/catalog";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Modal, Pager, Problem } from "./workspace";
import { toast } from "./toasts";
function useWriter(tenant: string, onDone: () => void, message: string) {
  const client = useQueryClient();
  const [error, setError] = useState<Error>();
  return {
    error,
    write: async (path: string, payload: unknown) => {
      setError(undefined);
      try {
        await api(path, { method: "POST", body: JSON.stringify(payload) });
        await Promise.all([
          client.invalidateQueries({ queryKey: [tenant, "inventory"] }),
          client.invalidateQueries({ queryKey: [tenant, "warehouses"] }),
        ]);
        toast.success(message);
        onDone();
      } catch (cause) {
        setError(cause as Error);
      }
    },
  };
}
export function WarehouseForm({
  tenant,
  onDone,
}: {
  tenant: string;
  onDone: () => void;
}) {
  const { error, write } = useWriter(
    tenant,
    onDone,
    s.inventory.warehouseCreated,
  );
  const form = useForm<z.infer<typeof warehouseSchema>>({
    resolver: zodResolver(warehouseSchema),
    defaultValues: { code: "", name: "", region: "" },
  });
  return (
    <form
      method="post"
      action="/bff/api/inventory/warehouses"
      className="space-y-4"
      onSubmit={form.handleSubmit((values) =>
        write("/bff/api/inventory/warehouses", values),
      )}
    >
      {(["code", "name", "region"] as const).map((name, index) => (
        <Field
          key={name}
          label={
            [s.inventory.code, s.inventory.name, s.inventory.region][index]
          }
          error={form.formState.errors[name]?.message}
        >
          <input {...form.register(name)} maxLength={[32, 120, 80][index]} />
        </Field>
      ))}
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.inventory.createWarehouse}
      </Button>
    </form>
  );
}
export function InventoryForm({
  tenant,
  warehouses,
  onDone,
}: {
  tenant: string;
  warehouses: Warehouse[];
  onDone: () => void;
}) {
  const { error, write } = useWriter(tenant, onDone, s.inventory.added);
  const form = useForm<z.infer<typeof inventorySchema>>({
    resolver: zodResolver(inventorySchema),
    defaultValues: {
      sku: "",
      warehouse_id: "",
      available_quantity: 0,
      low_stock_threshold: 10,
    },
  });
  return (
    <form
      method="post"
      action="/bff/api/inventory"
      className="space-y-4"
      onSubmit={form.handleSubmit((values) =>
        write("/bff/api/inventory", values),
      )}
    >
      <Field label={s.orders.sku} error={form.formState.errors.sku?.message}>
        <input {...form.register("sku")} maxLength={64} />
      </Field>
      <Field
        label={s.inventory.warehouse}
        error={form.formState.errors.warehouse_id?.message}
      >
        <select {...form.register("warehouse_id")}>
          <option value="">{s.inventory.choose}</option>
          {warehouses.map((row) => (
            <option key={row.id} value={row.id}>
              {row.code} · {row.name}
            </option>
          ))}
        </select>
      </Field>
      <p className="text-sm text-slate-600">{s.inventory.warehouseHelp}</p>
      {(["available_quantity", "low_stock_threshold"] as const).map(
        (name, index) => (
          <Field
            key={name}
            label={[s.inventory.available, s.inventory.threshold][index]}
            error={form.formState.errors[name]?.message}
          >
            <input
              type="number"
              min={0}
              max={2147483647}
              {...form.register(name, { valueAsNumber: true })}
            />
          </Field>
        ),
      )}
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.inventory.add}
      </Button>
    </form>
  );
}
export function AdjustmentForm({
  tenant,
  stock,
  onDone,
}: {
  tenant: string;
  stock: Stock;
  onDone: () => void;
}) {
  const { error, write } = useWriter(tenant, onDone, s.inventory.adjusted);
  const schema = adjustmentSchema(stock.available_quantity);
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { delta: 0, reason: "" },
  });
  const delta = useWatch({ control: form.control, name: "delta" });
  return (
    <form
      method="post"
      action={`/bff/api/inventory/${stock.id}/adjustments`}
      className="space-y-4"
      onSubmit={form.handleSubmit((values) =>
        write(`/bff/api/inventory/${stock.id}/adjustments`, values),
      )}
    >
      <p className="break-words font-semibold">{stock.sku}</p>
      <p className="text-sm text-slate-600">{s.inventory.adjustHint}</p>
      <Field
        label={s.inventory.delta}
        error={form.formState.errors.delta?.message}
      >
        <input
          type="number"
          {...form.register("delta", { valueAsNumber: true })}
        />
      </Field>
      <Field
        label={s.inventory.reason}
        error={form.formState.errors.reason?.message}
      >
        <input {...form.register("reason")} maxLength={200} />
      </Field>
      {Number.isFinite(delta) && (
        <p role="status">
          {s.inventory.result(stock.available_quantity + delta)}
        </p>
      )}
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.inventory.save}
      </Button>
    </form>
  );
}
export function StockCards({
  items,
  warehouses,
  onAdjust,
}: {
  items: Stock[];
  warehouses: Warehouse[];
  onAdjust: (stock: Stock) => void;
}) {
  return (
    <table className="stock-table w-full text-left text-sm">
      <caption className="sr-only">{s.inventory.title}</caption>
      <thead>
        <tr>
          {[
            s.catalog.name,
            s.inventory.warehouse,
            s.inventory.available,
            s.inventory.reserved,
            s.inventory.threshold,
            s.orders.status,
            s.inventory.adjust,
          ].map((label) => (
            <th scope="col" key={label}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {items.map((stock) => (
          <tr key={stock.id}>
            <th scope="row" className="break-words">
              <span className="block font-bold">{stock.product_name}</span>
              <span className="block font-normal">{stock.sku}</span>
            </th>
            <td data-label={s.inventory.warehouse} className="break-all">
              {warehouses.find((row) => row.id === stock.warehouse_id)?.code ||
                stock.warehouse_id}
            </td>
            <td data-label={s.inventory.available}>
              {stock.available_quantity}
            </td>
            <td data-label={s.inventory.reserved}>{stock.reserved_quantity}</td>
            <td data-label={s.inventory.threshold}>
              {stock.low_stock_threshold}
            </td>
            <td data-label={s.orders.status}>
              {stock.available_quantity <= stock.low_stock_threshold ? (
                <span className="rounded-lg bg-amber-100 px-2 py-1 font-semibold text-amber-950">
                  {s.inventory.low}
                </span>
              ) : (
                s.catalog.active
              )}
            </td>
            <td>
              <Button variant="outline" onClick={() => onAdjust(stock)}>
                {s.inventory.adjust}
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Inventory({ tenant }: { tenant: string }) {
  const [offset, setOffset] = useState(0);
  const [warehouseOffset, setWarehouseOffset] = useState(0);
  const [dialog, setDialog] = useState<"warehouse" | "stock">();
  const [adjust, setAdjust] = useState<Stock>();
  const stocks = useQuery({
    queryKey: [tenant, "inventory", offset],
    queryFn: ({ signal }) =>
      api<components["schemas"]["Page_InventoryRead_"]>(
        `/bff/api/inventory?limit=20&offset=${offset}`,
        { signal },
      ),
  });
  const warehouses = useQuery({
    queryKey: [tenant, "warehouses", warehouseOffset],
    queryFn: ({ signal }) =>
      api<components["schemas"]["Page_WarehouseRead_"]>(
        `/bff/api/inventory/warehouses?limit=20&offset=${warehouseOffset}`,
        { signal },
      ),
  });
  return (
    <div className="space-y-8">
      <header className="flex flex-wrap justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">{s.inventory.title}</h1>
          <p className="mt-2 text-slate-600">{s.inventory.hint}</p>
        </div>
        <Button
          disabled={!warehouses.data?.items.length}
          onClick={() => setDialog("stock")}
        >
          {s.inventory.add}
        </Button>
      </header>
      {stocks.isPending ? (
        <p role="status">{s.pending}</p>
      ) : stocks.error ? (
        <Problem error={stocks.error} retry={() => void stocks.refetch()} />
      ) : (
        <>
          <StockCards
            items={stocks.data.items}
            warehouses={warehouses.data?.items || []}
            onAdjust={setAdjust}
          />
          {!stocks.data.items.length && (
            <p className="panel">{s.inventory.empty}</p>
          )}
          <Pager
            offset={offset}
            total={stocks.data.total}
            onChange={setOffset}
          />
        </>
      )}
      <section className="space-y-4">
        <div className="flex flex-wrap justify-between gap-4">
          <h2 className="text-2xl font-bold">{s.inventory.warehouses}</h2>
          <Button onClick={() => setDialog("warehouse")}>
            {s.inventory.createWarehouse}
          </Button>
        </div>
        {warehouses.isPending ? (
          <p role="status">{s.pending}</p>
        ) : warehouses.error ? (
          <Problem
            error={warehouses.error}
            retry={() => void warehouses.refetch()}
          />
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              {warehouses.data.items.map((row) => (
                <article className="panel break-words" key={row.id}>
                  <h3 className="font-bold">{row.name}</h3>
                  <p>
                    {row.code} · {row.region}
                  </p>
                </article>
              ))}
            </div>
            {!warehouses.data.items.length && (
              <p className="panel">{s.inventory.warehousesEmpty}</p>
            )}
            <Pager
              offset={warehouseOffset}
              total={warehouses.data.total}
              onChange={setWarehouseOffset}
            />
          </>
        )}
      </section>
      {dialog && (
        <Modal
          title={
            dialog === "warehouse"
              ? s.inventory.createWarehouse
              : s.inventory.add
          }
          onClose={() => setDialog(undefined)}
        >
          {dialog === "warehouse" ? (
            <WarehouseForm
              tenant={tenant}
              onDone={() => setDialog(undefined)}
            />
          ) : (
            <InventoryForm
              tenant={tenant}
              warehouses={warehouses.data?.items || []}
              onDone={() => setDialog(undefined)}
            />
          )}
        </Modal>
      )}
      {adjust && (
        <Modal title={s.inventory.adjust} onClose={() => setAdjust(undefined)}>
          <AdjustmentForm
            tenant={tenant}
            stock={adjust}
            onDone={() => setAdjust(undefined)}
          />
        </Modal>
      )}
    </div>
  );
}
