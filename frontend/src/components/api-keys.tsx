"use client";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import {
  keySchema,
  readKeys,
  readSecret,
  scopes,
  type Key,
} from "@/lib/operations";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Modal, Problem } from "./workspace";
import {
  ConfirmAction,
  SecretOnce,
  Stamp,
  useSecretDisclosure,
} from "./operations-ui";
function KeyForm({
  begin,
  onDone,
  onBusy,
}: {
  begin: () => (value: string) => void;
  onDone: () => Promise<void>;
  onBusy: (value: boolean) => void;
}) {
  const [error, setError] = useState<Error>();
  const form = useForm<z.infer<typeof keySchema>>({
    resolver: zodResolver(keySchema),
    defaultValues: { name: "", scopes: [], expires_at: "" },
  });
  return (
    <form
      method="post"
      action="/bff/api/api-keys"
      className="space-y-4"
      onSubmit={form.handleSubmit(async (values) => {
        setError(undefined);
        onBusy(true);
        const reveal = begin();
        try {
          const result = await api<unknown>("/bff/api/api-keys", {
            method: "POST",
            body: JSON.stringify({
              ...values,
              expires_at: values.expires_at
                ? new Date(values.expires_at).toISOString()
                : null,
            }),
          });
          const secret = readSecret(result, "key");
          await onDone();
          reveal(secret);
        } catch (cause) {
          setError(cause as Error);
        } finally {
          onBusy(false);
        }
      })}
    >
      <Field label={s.ops.name} error={form.formState.errors.name?.message}>
        <input maxLength={120} {...form.register("name")} />
      </Field>
      <fieldset className="space-y-3">
        <legend className="mb-3 font-semibold">{s.ops.scopesLabel}</legend>
        {scopes.map((scope) => (
          <label key={scope} className="flex items-start gap-3">
            <input type="checkbox" value={scope} {...form.register("scopes")} />
            <span>{s.ops.scopes[scope]}</span>
          </label>
        ))}
        {form.formState.errors.scopes && (
          <p className="field-error" role="alert">
            {s.validation.required}
          </p>
        )}
      </fieldset>
      <Field
        label={s.ops.expiry}
        error={form.formState.errors.expires_at?.message}
      >
        <input type="datetime-local" {...form.register("expires_at")} />
      </Field>
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.ops.createKey}
      </Button>
    </form>
  );
}
export function ApiKeys({ tenant }: { tenant: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(timer);
  }, []);
  const client = useQueryClient();
  const [formBusy, setFormBusy] = useState(false);
  const [create, setCreate] = useState(false);
  const [revoke, setRevoke] = useState<Key>();
  const secret = useSecretDisclosure();
  const query = useQuery({
    queryKey: [tenant, "api-keys"],
    queryFn: async ({ signal }) =>
      readKeys(await api<unknown>("/bff/api/api-keys", { signal })),
  });
  return (
    <div className="space-y-6">
      <header className="flex flex-wrap justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">{s.ops.keys}</h1>
          <p className="mt-2 text-slate-600">{s.ops.keyHint}</p>
        </div>
        <Button disabled={formBusy} onClick={() => setCreate(true)}>
          {s.ops.createKey}
        </Button>
      </header>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <div className="space-y-4">
          {query.data.map((key) => (
            <article className="panel space-y-3" key={key.id}>
              <h2 className="font-bold break-words">{key.name}</h2>
              <p className="font-mono text-sm">
                {s.ops.prefix}: {key.key_prefix}
              </p>
              <p className="text-sm">
                {key.revoked_at
                  ? s.ops.revoked
                  : key.expires_at && Date.parse(key.expires_at) < now
                    ? s.ops.expired
                    : s.ops.active}
              </p>
              <ul className="text-sm text-slate-600">
                {key.scopes.map((scope) => (
                  <li key={scope}>{s.ops.scopes[scope] || s.orders.unknown}</li>
                ))}
              </ul>
              <dl className="grid gap-2 text-sm sm:grid-cols-2">
                <dt>{s.ops.lastUsed}</dt>
                <dd>
                  <Stamp value={key.last_used_at} />
                </dd>
                <dt>{s.ops.expiry}</dt>
                <dd>
                  <Stamp value={key.expires_at} />
                </dd>
              </dl>
              <Button
                variant="outline"
                disabled={!!key.revoked_at}
                onClick={() => setRevoke(key)}
              >
                {s.ops.revoke}
              </Button>
            </article>
          ))}
          {!query.data.length && <p className="panel">{s.ops.empty}</p>}
        </div>
      )}
      {create && (
        <Modal
          title={s.ops.createKey}
          onClose={() => {
            if (!formBusy) setCreate(false);
          }}
        >
          <KeyForm
            onBusy={setFormBusy}
            begin={secret.begin}
            onDone={async () => {
              await client.invalidateQueries({
                queryKey: [tenant, "api-keys"],
              });
              setCreate(false);
            }}
          />
        </Modal>
      )}
      {revoke && (
        <ConfirmAction
          title={s.ops.revoke}
          hint={s.ops.revokeHint}
          onClose={() => setRevoke(undefined)}
          perform={async () => {
            await api("/bff/api/api-keys/" + encodeURIComponent(revoke.id), {
              method: "DELETE",
            });
            await client.invalidateQueries({ queryKey: [tenant, "api-keys"] });
          }}
        />
      )}
      {secret.value && (
        <SecretOnce value={secret.value} onForget={secret.forget} />
      )}
    </div>
  );
}
