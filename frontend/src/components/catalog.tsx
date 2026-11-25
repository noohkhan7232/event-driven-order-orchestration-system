"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import { productSchema, decimalPrice, type Product } from "@/lib/catalog";
import { strings as s } from "@/lib/strings";
import { money } from "@/lib/format";
import { Button } from "./ui/button";
import { Field, Modal, Pager, Problem } from "./workspace";
import { toast } from "./toasts";
export function ProductForm({
  tenant,
  product,
  onDone,
}: {
  tenant: string;
  product?: Product;
  onDone: (product: Product) => void;
}) {
  const [error, setError] = useState<Error>();
  const client = useQueryClient();
  const form = useForm<z.infer<typeof productSchema>>({
    resolver: zodResolver(productSchema),
    defaultValues: product
      ? { ...product, description: product.description || "" }
      : {
          sku: "",
          name: "",
          description: "",
          price: "",
          currency: "INR",
          is_active: true,
        },
  });
  return (
    <form
      method="post"
      action="/bff/api/products"
      className="space-y-4"
      onSubmit={form.handleSubmit(async (values) => {
        setError(undefined);
        try {
          const payload = {
            name: values.name,
            description: values.description || null,
            price: decimalPrice(values.price),
          };
          const result = await api<Product>(
            "/bff/api/products" + (product ? "/" + product.id : ""),
            {
              method: product ? "PATCH" : "POST",
              body: JSON.stringify(
                product
                  ? { ...payload, is_active: values.is_active }
                  : { ...payload, sku: values.sku, currency: values.currency },
              ),
            },
          );
          await client.invalidateQueries({ queryKey: [tenant, "products"] });
          toast.success(s.catalog.saved);
          onDone(result);
        } catch (cause) {
          setError(
            cause instanceof ApiError && cause.status === 409
              ? new ApiError(409, "product_sku_conflict")
              : (cause as Error),
          );
        }
      })}
    >
      <p className="text-sm text-slate-600">{s.catalog.immutable}</p>
      {(["sku", "name", "price", "currency", "description"] as const).map(
        (name, index) => (
          <Field
            key={name}
            label={
              [
                s.orders.sku,
                s.catalog.name,
                s.catalog.price,
                s.catalog.currency,
                s.catalog.description,
              ][index]
            }
            error={form.formState.errors[name]?.message}
          >
            <input
              {...form.register(name)}
              readOnly={!!product && (name === "sku" || name === "currency")}
              inputMode={name === "price" ? "decimal" : undefined}
              maxLength={[64, 180, 13, 3, 10000][index]}
            />
          </Field>
        ),
      )}
      <p className="text-sm text-slate-600">{s.catalog.priceHint}</p>
      {product && (
        <Field label={s.catalog.active}>
          <input type="checkbox" {...form.register("is_active")} />
        </Field>
      )}
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.catalog.save}
      </Button>
    </form>
  );
}
export function Catalog({ tenant }: { tenant: string }) {
  const router = useRouter();
  const client = useQueryClient();
  const [filters, setFilters] = useState({ q: "", active: "", offset: 0 });
  const [create, setCreate] = useState(false);
  const [archive, setArchive] = useState<Product>();
  const [error, setError] = useState<Error>();
  const [saving, setSaving] = useState(false);
  const params = new URLSearchParams({
    limit: "20",
    offset: String(filters.offset),
    q: filters.q,
  });
  if (filters.active) params.set("is_active", filters.active);
  const query = useQuery({
    queryKey: [tenant, "products", "list", filters],
    queryFn: ({ signal }) =>
      api<components["schemas"]["Page_ProductRead_"]>(
        "/bff/api/products?" + params,
        { signal },
      ),
  });
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">{s.catalog.title}</h1>
          <p className="mt-2 text-slate-600">{s.catalog.hint}</p>
        </div>
        <Button onClick={() => setCreate(true)}>{s.catalog.create}</Button>
      </header>
      <form
        className="panel grid gap-4 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setFilters({
            offset: 0,
            q: String(data.get("q")).trim(),
            active: String(data.get("active")),
          });
        }}
      >
        <Field label={s.catalog.search}>
          <input name="q" maxLength={180} />
        </Field>
        <Field label={s.catalog.activeFilter}>
          <select name="active">
            <option value="">{s.catalog.all}</option>
            <option value="true">{s.catalog.active}</option>
            <option value="false">{s.catalog.archived}</option>
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
          <div className="grid gap-4 sm:grid-cols-2">
            {query.data.items.map((product) => (
              <article className="panel space-y-3" key={product.id}>
                <Link
                  href={"/catalog/" + product.id}
                  className="block font-bold text-teal-900 underline break-words"
                >
                  {product.name}
                </Link>
                <p className="break-words text-sm">{product.sku}</p>
                <p>{money(product.price, product.currency)}</p>
                <p className="text-sm">
                  {product.is_active ? s.catalog.active : s.catalog.archived}
                </p>
                <Button
                  variant="outline"
                  onClick={() => {
                    setError(undefined);
                    setArchive(product);
                  }}
                >
                  {product.is_active ? s.catalog.archive : s.catalog.unarchive}
                </Button>
              </article>
            ))}
          </div>
          {!query.data.items.length && (
            <p className="panel">{s.catalog.empty}</p>
          )}
          <Pager
            offset={filters.offset}
            total={query.data.total}
            onChange={(offset) => setFilters({ ...filters, offset })}
          />
        </>
      )}
      {create && (
        <Modal title={s.catalog.create} onClose={() => setCreate(false)}>
          <ProductForm
            tenant={tenant}
            onDone={(product) => {
              setCreate(false);
              router.push("/catalog/" + product.id);
            }}
          />
        </Modal>
      )}
      {archive && (
        <Modal
          title={archive.is_active ? s.catalog.archive : s.catalog.unarchive}
          onClose={() => {
            if (!saving) setArchive(undefined);
          }}
        >
          <p className="mb-4">
            {archive.is_active
              ? s.catalog.confirmArchive
              : s.catalog.confirmUnarchive}
          </p>
          {error && <Problem error={error} />}
          <Button
            disabled={saving}
            onClick={async () => {
              setSaving(true);
              try {
                await api("/bff/api/products/" + archive.id, {
                  method: "PATCH",
                  body: JSON.stringify({ is_active: !archive.is_active }),
                });
                await client.invalidateQueries({
                  queryKey: [tenant, "products"],
                });
                setArchive(undefined);
              } catch (cause) {
                setError(cause as Error);
              } finally {
                setSaving(false);
              }
            }}
          >
            {saving ? s.pending : s.orders.confirm}
          </Button>
        </Modal>
      )}
    </div>
  );
}
export function ProductDetail({ tenant, id }: { tenant: string; id: string }) {
  const query = useQuery({
    queryKey: [tenant, "products", "detail", id],
    queryFn: ({ signal }) =>
      api<Product>("/bff/api/products/" + id, { signal }),
  });
  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-3xl font-bold">{s.catalog.edit}</h1>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <div className="panel">
          <ProductForm
            key={query.data.id}
            tenant={tenant}
            product={query.data}
            onDone={() => void query.refetch()}
          />
        </div>
      )}
    </div>
  );
}
