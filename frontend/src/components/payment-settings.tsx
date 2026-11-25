"use client";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api/client";
import { paymentSchema, readPaymentConfig } from "@/lib/operations";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Problem } from "./workspace";
import { ConfirmAction, copy } from "./operations-ui";
import { toast } from "./toasts";
function Credentials({
  tenant,
  mode,
}: {
  tenant: string;
  mode: "test" | "live";
}) {
  const client = useQueryClient();
  const [error, setError] = useState<Error>();
  const form = useForm<z.infer<typeof paymentSchema>>({
    resolver: zodResolver(paymentSchema),
    defaultValues: { key_id: "", key_secret: "", webhook_secret: "", mode },
  });
  const { resetField } = form;
  useEffect(() => {
    const clear = () => {
      resetField("key_id");
      resetField("key_secret");
      resetField("webhook_secret");
    };
    window.addEventListener("pagehide", clear);
    return () => {
      clear();
      window.removeEventListener("pagehide", clear);
    };
  }, [resetField]);
  return (
    <form
      method="post"
      action="/bff/api/settings/payments/razorpay"
      className="panel space-y-4"
      autoComplete="off"
      onSubmit={form.handleSubmit(async (values) => {
        setError(undefined);
        try {
          await api("/bff/api/settings/payments/razorpay", {
            method: "PUT",
            body: JSON.stringify(values),
          });
          resetField("key_id");
          resetField("key_secret");
          resetField("webhook_secret");
          await client.invalidateQueries({
            queryKey: [tenant, "payment-config"],
          });
          toast.success(s.ops.paymentSaved);
        } catch (cause) {
          setError(cause as Error);
        } finally {
          resetField("key_id");
          resetField("key_secret");
          resetField("webhook_secret");
        }
      })}
    >
      <h2 className="section-title">{s.ops.replace}</h2>
      <p className="text-sm text-slate-600">{s.ops.replaceHint}</p>
      {(["key_id", "key_secret", "webhook_secret"] as const).map(
        (name, index) => (
          <Field
            key={name}
            label={[s.ops.keyId, s.ops.keySecret, s.ops.webhookSecret][index]}
            error={form.formState.errors[name]?.message}
          >
            <input
              type="password"
              autoComplete="new-password"
              maxLength={name === "key_id" ? 120 : 2048}
              {...form.register(name)}
            />
          </Field>
        ),
      )}
      <Field label={s.ops.mode}>
        <select {...form.register("mode")}>
          <option value="test">{s.ops.test}</option>
          <option value="live">{s.ops.live}</option>
        </select>
      </Field>
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.ops.connect}
      </Button>
    </form>
  );
}
export function PaymentSettings({ tenant }: { tenant: string }) {
  const client = useQueryClient();
  const [disconnect, setDisconnect] = useState(false);
  const query = useQuery({
    queryKey: [tenant, "payment-config"],
    queryFn: async ({ signal }) => {
      try {
        return readPaymentConfig(
          await api<unknown>("/bff/api/settings/payments/razorpay", { signal }),
        );
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 404) return null;
        throw cause;
      }
    },
  });
  return (
    <div className="max-w-3xl space-y-6">
      <header>
        <h1 className="text-3xl font-bold">{s.ops.payments}</h1>
        <p className="mt-2 text-slate-600">{s.ops.paymentHint}</p>
      </header>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          {query.data ? (
            <section className="panel space-y-4">
              <p>
                {s.ops.status}:{" "}
                {query.data.active ? s.ops.active : s.ops.inactive}
              </p>
              <p>
                {s.ops.maskedKey}: {query.data.key_id}
              </p>
              <p>
                {s.ops.mode}:{" "}
                {query.data.mode === "live" ? s.ops.live : s.ops.test}
              </p>
              <label>
                {s.ops.inboundUrl}
                <input readOnly value={query.data.webhook_url} />
              </label>
              <Button
                variant="outline"
                onClick={() => void copy(query.data!.webhook_url)}
              >
                {s.ops.copy}
              </Button>
              <Button
                variant="outline"
                className="ml-3"
                disabled={!query.data.active}
                onClick={() => setDisconnect(true)}
              >
                {s.ops.disconnect}
              </Button>
            </section>
          ) : (
            <p className="panel">{s.ops.notConnected}</p>
          )}
          <Credentials
            tenant={tenant}
            mode={query.data?.mode === "live" ? "live" : "test"}
          />
          <section className="panel">
            <h2 className="section-title">{s.ops.setup}</h2>
            <ol className="list-decimal space-y-3 pl-5 text-sm">
              {s.ops.setupSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <a
              className="mt-5 inline-block text-teal-900 underline"
              href="https://razorpay.com/docs/payments/dashboard/account-settings/webhooks/"
              target="_blank"
              rel="noopener noreferrer"
            >
              {s.ops.setupDocs}
            </a>
          </section>
        </>
      )}
      {disconnect && (
        <ConfirmAction
          title={s.ops.disconnect}
          hint={s.ops.disconnectHint}
          onClose={() => setDisconnect(false)}
          perform={async () => {
            await api("/bff/api/settings/payments/razorpay", {
              method: "DELETE",
            });
            await client.invalidateQueries({
              queryKey: [tenant, "payment-config"],
            });
          }}
        />
      )}
    </div>
  );
}
