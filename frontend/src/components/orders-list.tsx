"use client";
import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import { orderQuery, statuses, uuidPattern } from "@/lib/orders";
import { useOrderStream } from "@/lib/order-stream";
import { money } from "@/lib/format";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Badge, Field, Pager, Problem } from "./workspace";
export function OrdersList({ tenant }: { tenant: string }) {
  const [filters, setFilters] = useState({
    offset: 0,
    status: "",
    search: "",
    from: "",
    to: "",
  });
  const [dateError, setDateError] = useState(false);
  const live = useOrderStream(tenant);
  const direct = uuidPattern.test(filters.search.trim());
  const query = useQuery({
    queryKey: [tenant, "orders", "list", filters],
    queryFn: async ({ signal }) =>
      direct
        ? {
            items: [
              await api<components["schemas"]["OrderRead"]>(
                "/bff/api/orders/" + filters.search.trim(),
                { signal },
              ),
            ],
            total: 1,
            limit: 20,
            offset: 0,
          }
        : api<components["schemas"]["Page_OrderRead_"]>(
            "/bff/api/orders?" + orderQuery(filters),
            { signal },
          ),
    refetchInterval: 15000,
  });
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">{s.orders.title}</h1>
          <p className="mt-2 text-slate-600">{s.orders.hint}</p>
        </div>
        <Button asChild>
          <Link href="/orders/new">{s.orders.create}</Link>
        </Button>
      </div>
      <p role="status" className="text-sm text-teal-900">
        {live ? s.orders.live : s.orders.polling}
      </p>
      <form
        className="panel grid gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const next = {
            offset: 0,
            status: String(data.get("status")),
            search: String(data.get("search")),
            from: String(data.get("from")),
            to: String(data.get("to")),
          };
          if (next.from && next.to && next.from > next.to) {
            setDateError(true);
            return;
          }
          setDateError(false);
          setFilters(next);
        }}
      >
        <Field label={s.orders.search}>
          <input name="search" maxLength={100} />
        </Field>
        <Field label={s.orders.status}>
          <select name="status">
            <option value="">{s.orders.all}</option>
            {statuses.map((status) => (
              <option key={status} value={status}>
                {s.statuses[status]}
              </option>
            ))}
          </select>
        </Field>
        <Field label={s.orders.from}>
          <input name="from" type="date" />
        </Field>
        <Field label={s.orders.to}>
          <input name="to" type="date" />
        </Field>
        {dateError && <p role="alert">{s.orders.invalidDate}</p>}
        <Button>{s.orders.apply}</Button>
      </form>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <div className="grid gap-3">
            {query.data.items.map((order) => (
              <Link
                className="panel grid gap-3 break-words hover:border-teal-700 sm:grid-cols-[minmax(0,1fr)_auto_auto]"
                key={order.id}
                href={"/orders/" + order.id}
              >
                <div>
                  <p className="font-semibold">
                    {order.external_reference || order.id}
                  </p>
                  <p className="mt-1 text-xs text-slate-600">{order.id}</p>
                </div>
                <Badge status={order.status} />
                <p className="font-semibold">
                  {money(order.total_amount, order.currency)}
                </p>
              </Link>
            ))}
            {!query.data.items.length && (
              <p className="panel">{s.orders.empty}</p>
            )}
          </div>
          <Pager
            offset={direct ? 0 : filters.offset}
            total={query.data.total}
            onChange={(offset) => setFilters({ ...filters, offset })}
          />
        </>
      )}
    </div>
  );
}
