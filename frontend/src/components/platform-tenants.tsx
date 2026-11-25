"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Pager, Problem } from "./workspace";
import { ConfirmAction, State } from "./operations-ui";
type Tenant = components["schemas"]["TenantRead"];
export function PlatformTenants({ user }: { user: string }) {
  const client = useQueryClient();
  const [filters, setFilters] = useState({ offset: 0, status: "", q: "" });
  const [target, setTarget] = useState<Tenant>();
  const params = new URLSearchParams({
    limit: "20",
    offset: String(filters.offset),
  });
  if (filters.status) params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  const query = useQuery({
    queryKey: [user, "platform-tenants", filters],
    queryFn: ({ signal }) =>
      api<components["schemas"]["Page_TenantRead_"]>(
        "/bff/api/admin/tenants?" + params,
        { signal },
      ),
  });
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold">{s.ops.tenants}</h1>
        <p className="mt-2 text-slate-600">{s.ops.tenantsHint}</p>
      </header>
      <form
        className="panel grid gap-4 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setFilters({
            offset: 0,
            q: String(data.get("q")).trim(),
            status: String(data.get("status")),
          });
        }}
      >
        <Field label={s.ops.search}>
          <input name="q" maxLength={160} />
        </Field>
        <Field label={s.ops.status}>
          <select name="status">
            <option value="">{s.ops.all}</option>
            {["active", "suspended"].map((value) => (
              <option key={value} value={value}>
                {s.ops.statusLabels[value]}
              </option>
            ))}
          </select>
        </Field>
        <Button className="self-end">{s.orders.apply}</Button>
      </form>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <div className="space-y-4">
            {query.data.items.map((tenant) => (
              <article className="panel space-y-3" key={tenant.id}>
                <h2 className="break-words font-bold">{tenant.name}</h2>
                <p className="break-words text-sm">
                  {s.ops.slug}: {tenant.slug}
                </p>
                <State value={tenant.status} />
                <p className="text-sm text-slate-600">
                  {s.ops.reason}: {s.ops.reasonUnavailable}
                </p>
                <Button variant="outline" onClick={() => setTarget(tenant)}>
                  {tenant.status === "suspended"
                    ? s.ops.activate
                    : s.ops.suspend}
                </Button>
              </article>
            ))}
            {!query.data.items.length && <p className="panel">{s.ops.empty}</p>}
          </div>
          <Pager
            offset={filters.offset}
            total={query.data.total}
            onChange={(offset) => setFilters({ ...filters, offset })}
          />
        </>
      )}
      {target && (
        <ConfirmAction
          title={target.status === "suspended" ? s.ops.activate : s.ops.suspend}
          hint={
            target.name +
            ": " +
            (target.status === "suspended"
              ? s.ops.activateHint
              : s.ops.suspendHint)
          }
          onClose={() => setTarget(undefined)}
          perform={async () => {
            await api(
              "/bff/api/admin/tenants/" +
                encodeURIComponent(target.id) +
                (target.status === "suspended" ? "/activate" : "/suspend"),
              { method: "POST" },
            );
            await client.invalidateQueries({
              queryKey: [user, "platform-tenants"],
            });
          }}
        />
      )}
    </div>
  );
}
