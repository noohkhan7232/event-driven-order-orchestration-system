"use client";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useForm, useFieldArray } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { api, ApiError } from "@/lib/api/client";
import { createOrderSchema, type Order } from "@/lib/orders";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Problem } from "./workspace";
export function CreateOrder() {
  const router = useRouter();
  const [error, setError] = useState<Error>();
  const attempt = useRef<{ body: string; key: string } | null>(null);
  const form = useForm<z.infer<typeof createOrderSchema>>({
    resolver: zodResolver(createOrderSchema),
    defaultValues: {
      items: [{ sku: "", quantity: 1 }],
      payment_method: "prepaid",
      buyer_name: "",
      buyer_email: "",
      buyer_phone: "",
      external_reference: "",
    },
  });
  const items = useFieldArray({ control: form.control, name: "items" });
  return (
    <div className="max-w-2xl space-y-5">
      <h1 className="text-3xl font-bold">{s.orders.create}</h1>
      <p className="text-slate-600">{s.orders.testHint}</p>
      <form
        method="post"
        action="/bff/api/orders"
        className="panel space-y-4"
        onSubmit={(event) => {
          void form.handleSubmit(async (values) => {
            setError(undefined);
            const body = JSON.stringify({
              ...values,
              buyer_name: values.buyer_name || null,
              buyer_email: values.buyer_email || null,
              buyer_phone: values.buyer_phone || null,
              external_reference: values.external_reference || null,
            });
            if (!attempt.current || attempt.current.body !== body)
              attempt.current = { body, key: crypto.randomUUID() };
            try {
              const order = await api<Order>("/bff/api/orders", {
                method: "POST",
                headers: { "Idempotency-Key": attempt.current.key },
                body,
              });
              router.push("/orders/" + order.id);
            } catch (cause) {
              setError(cause as ApiError);
            }
          })(event);
        }}
      >
        {items.fields.map((item, index) => (
          <fieldset
            key={item.id}
            className="grid gap-3 rounded-xl border border-slate-200 p-3 sm:grid-cols-2"
          >
            <Field
              label={s.orders.sku}
              error={form.formState.errors.items?.[index]?.sku?.message}
            >
              <input {...form.register(`items.${index}.sku`)} maxLength={64} />
            </Field>
            <Field
              label={s.orders.quantity}
              error={form.formState.errors.items?.[index]?.quantity?.message}
            >
              <input
                type="number"
                min={1}
                max={500}
                {...form.register(`items.${index}.quantity`, {
                  valueAsNumber: true,
                })}
              />
            </Field>
            <Button
              type="button"
              variant="ghost"
              disabled={items.fields.length === 1}
              onClick={() => items.remove(index)}
            >
              {s.orders.remove}
            </Button>
          </fieldset>
        ))}
        <Button
          variant="outline"
          type="button"
          disabled={items.fields.length >= 100}
          onClick={() => items.append({ sku: "", quantity: 1 })}
        >
          {s.orders.addItem}
        </Button>
        <Field label={s.orders.payment}>
          <select {...form.register("payment_method")}>
            <option value="prepaid">{s.orders.prepaid}</option>
            <option value="cod">{s.orders.cod}</option>
          </select>
        </Field>
        {(
          [
            "external_reference",
            "buyer_name",
            "buyer_email",
            "buyer_phone",
          ] as const
        ).map((key, index) => (
          <Field
            key={key}
            label={
              [
                s.orders.reference,
                s.orders.name,
                s.orders.email,
                s.orders.phone,
              ][index]
            }
            error={form.formState.errors[key]?.message}
          >
            <input
              {...form.register(key)}
              maxLength={[100, 120, 255, 20][index]}
              type={key === "buyer_email" ? "email" : "text"}
            />
          </Field>
        ))}
        {error && <Problem error={error} />}
        <Button disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? s.pending : s.orders.create}
        </Button>
      </form>
    </div>
  );
}
