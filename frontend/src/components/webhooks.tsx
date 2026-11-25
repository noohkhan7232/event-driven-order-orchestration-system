"use client";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import {
  endpointSchema,
  endpointPath,
  events,
  readEndpoints,
  readDeliveries,
  readSecret,
  type Endpoint,
  type Delivery,
} from "@/lib/operations";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Modal, Pager, Problem } from "./workspace";
import {
  ConfirmAction,
  SecretOnce,
  State,
  Stamp,
  copy,
  useSecretDisclosure,
} from "./operations-ui";
function EndpointForm({
  endpoint,
  begin,
  onDone,
  onBusy,
}: {
  endpoint?: Endpoint;
  begin: () => (value: string) => void;
  onDone: () => Promise<void>;
  onBusy: (value: boolean) => void;
}) {
  const [error, setError] = useState<Error>();
  const form = useForm<z.infer<typeof endpointSchema>>({
    resolver: zodResolver(endpointSchema),
    defaultValues: {
      url: endpoint?.url || "",
      events:
        endpoint?.events.filter((value): value is (typeof events)[number] =>
          events.includes(value as (typeof events)[number]),
        ) || [],
      description: endpoint?.description || "",
      is_active: endpoint?.is_active ?? true,
    },
  });
  return (
    <form
      method="post"
      action="/bff/api/webhook-endpoints"
      className="space-y-4"
      onSubmit={form.handleSubmit(async (values) => {
        setError(undefined);
        onBusy(true);
        const reveal = begin();
        try {
          const payload = {
            url: values.url,
            events: values.events,
            description: values.description || null,
          };
          const result = await api<unknown>(
            endpoint ? endpointPath(endpoint.id) : "/bff/api/webhook-endpoints",
            {
              method: endpoint ? "PATCH" : "POST",
              body: JSON.stringify(
                endpoint
                  ? { ...payload, is_active: values.is_active }
                  : payload,
              ),
            },
          );
          const secret = endpoint ? null : readSecret(result, "signing_secret");
          await onDone();
          if (secret) reveal(secret);
        } catch (cause) {
          setError(cause as Error);
        } finally {
          onBusy(false);
        }
      })}
    >
      <Field label={s.ops.url} error={form.formState.errors.url?.message}>
        <input type="url" maxLength={2048} {...form.register("url")} />
      </Field>
      <p className="text-sm text-slate-600">{s.ops.targetHint}</p>
      <Field
        label={s.ops.description}
        error={form.formState.errors.description?.message}
      >
        <input maxLength={200} {...form.register("description")} />
      </Field>
      <fieldset className="space-y-3">
        <legend className="mb-3 font-semibold">{s.ops.eventsLabel}</legend>
        {events.map((value) => (
          <label className="flex items-start gap-3" key={value}>
            <input type="checkbox" value={value} {...form.register("events")} />
            <span>{s.ops.eventLabels[value]}</span>
          </label>
        ))}
        {form.formState.errors.events && (
          <p role="alert" className="field-error">
            {s.validation.required}
          </p>
        )}
      </fieldset>
      {endpoint && (
        <Field label={s.ops.endpointActive}>
          <input type="checkbox" {...form.register("is_active")} />
        </Field>
      )}
      {error && <Problem error={error} />}
      <Button disabled={form.formState.isSubmitting}>
        {form.formState.isSubmitting ? s.pending : s.ops.save}
      </Button>
    </form>
  );
}
function Deliveries({
  tenant,
  endpoint,
}: {
  tenant: string;
  endpoint: Endpoint;
}) {
  const client = useQueryClient();
  const [filters, setFilters] = useState({ offset: 0, status: "", event: "" });
  const [replay, setReplay] = useState<Delivery>();
  const params = new URLSearchParams({
    limit: "20",
    offset: String(filters.offset),
  });
  if (filters.status) params.set("status", filters.status);
  if (filters.event) params.set("event_type", filters.event);
  const query = useQuery({
    queryKey: [tenant, "webhook-deliveries", endpoint.id, filters],
    queryFn: async ({ signal }) =>
      readDeliveries(
        await api<unknown>(
          endpointPath(endpoint.id) + "/deliveries?" + params,
          { signal },
        ),
      ),
    refetchInterval: 15000,
  });
  return (
    <section className="space-y-4">
      <h2 className="text-2xl font-bold">{s.ops.deliveries}</h2>
      <p className="break-all text-sm">{endpoint.url}</p>
      <form
        className="panel grid gap-4 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setFilters({
            offset: 0,
            status: String(data.get("status")),
            event: String(data.get("event")),
          });
        }}
      >
        <Field label={s.ops.status}>
          <select name="status">
            <option value="">{s.ops.all}</option>
            {["pending", "delivered", "failed", "dead"].map((value) => (
              <option key={value} value={value}>
                {s.ops.statusLabels[value]}
              </option>
            ))}
          </select>
        </Field>
        <Field label={s.ops.eventType}>
          <select name="event">
            <option value="">{s.ops.all}</option>
            {events.map((value) => (
              <option key={value} value={value}>
                {s.ops.eventLabels[value]}
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
          <table className="stock-table w-full table-fixed text-left text-sm">
            <caption className="sr-only">{s.ops.deliveries}</caption>
            <thead>
              <tr>
                {[
                  s.ops.eventType,
                  s.ops.status,
                  s.ops.attempts,
                  s.ops.httpStatus,
                  s.ops.nextAttempt,
                  s.ops.deliveryError,
                  s.ops.actions,
                ].map((label) => (
                  <th key={label} scope="col">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((row) => (
                <tr key={row.id}>
                  <th scope="row" className="break-words">
                    {s.ops.eventLabels[row.event_type] || s.ops.unknownEvent}
                  </th>
                  <td data-label={s.ops.status}>
                    <State value={row.status} />
                  </td>
                  <td data-label={s.ops.attempts}>{row.attempts}</td>
                  <td data-label={s.ops.httpStatus}>
                    {row.last_status_code ?? s.ops.none}
                  </td>
                  <td data-label={s.ops.nextAttempt} className="break-words">
                    <Stamp value={row.next_attempt_at} />
                  </td>
                  <td data-label={s.ops.deliveryError} className="break-words">
                    {row.last_error
                      ? s.ops.deliveryErrors[row.last_error] ||
                        s.ops.diagnostics
                      : s.ops.none}
                  </td>
                  <td>
                    <Button
                      variant="outline"
                      disabled={
                        !endpoint.is_active ||
                        !["failed", "dead"].includes(row.status)
                      }
                      onClick={() => setReplay(row)}
                    >
                      {s.ops.replayDelivery}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!query.data.items.length && <p className="panel">{s.ops.empty}</p>}
          <Pager
            offset={filters.offset}
            total={query.data.total}
            onChange={(offset) => setFilters({ ...filters, offset })}
          />
        </>
      )}
      {replay && (
        <ConfirmAction
          title={s.ops.replayDelivery}
          hint={s.ops.replayDeliveryHint}
          onClose={() => setReplay(undefined)}
          perform={async () => {
            await api(
              endpointPath(endpoint.id) +
                "/deliveries/" +
                encodeURIComponent(replay.id) +
                "/replay",
              { method: "POST" },
            );
            await client.invalidateQueries({
              queryKey: [tenant, "webhook-deliveries", endpoint.id],
            });
          }}
        />
      )}
    </section>
  );
}
type Action = {
  endpoint: Endpoint;
  kind: "delete" | "rotate" | "toggle" | "ping";
};
export function Webhooks({ tenant }: { tenant: string }) {
  const [formBusy, setFormBusy] = useState(false);
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [form, setForm] = useState<Endpoint | "create">();
  const [action, setAction] = useState<Action>();
  const [selected, setSelected] = useState<string>();
  const secret = useSecretDisclosure();
  const query = useQuery({
    queryKey: [tenant, "webhooks", offset],
    refetchInterval: 15000,
    queryFn: async ({ signal }) =>
      readEndpoints(
        await api<unknown>(
          `/bff/api/webhook-endpoints?limit=20&offset=${offset}`,
          { signal },
        ),
      ),
  });
  const endpoint = query.data?.items.find((row) => row.id === selected);
  const title = action
    ? {
        delete: s.ops.deleteEndpoint,
        rotate: s.ops.rotate,
        toggle: action.endpoint.is_active ? s.ops.disable : s.ops.enable,
        ping: s.ops.testPing,
      }[action.kind]
    : "";
  const hints = {
    delete: s.ops.deleteHint,
    rotate: s.ops.rotateHint,
    toggle: s.ops.toggleHint,
    ping: s.ops.pingHint,
  };
  return (
    <div className="space-y-8">
      <header className="flex flex-wrap justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">{s.ops.webhooks}</h1>
          <p className="mt-2 text-slate-600">{s.ops.webhookHint}</p>
        </div>
        <Button disabled={formBusy} onClick={() => setForm("create")}>
          {s.ops.createEndpoint}
        </Button>
      </header>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <div className="space-y-4">
            {query.data.items.map((row) => (
              <article className="panel space-y-4" key={row.id}>
                <h2 className="break-all font-semibold">{row.url}</h2>
                {row.description && (
                  <p className="break-words text-sm">{row.description}</p>
                )}
                <p>{row.is_active ? s.ops.active : s.ops.disabled}</p>
                <p className="text-sm">
                  {s.ops.failureCount}: {row.consecutive_failures}
                </p>
                {row.disabled_reason && (
                  <p className="notice">
                    {s.ops.disabledReason}:{" "}
                    {row.disabled_reason === "consecutive_delivery_failures"
                      ? s.ops.failureThreshold
                      : row.disabled_reason === "owner_disabled"
                        ? s.ops.ownerDisabled
                        : s.ops.none}
                  </p>
                )}
                <ul className="text-sm text-slate-600">
                  {row.events.map((event) => (
                    <li key={event}>
                      {s.ops.eventLabels[event] || s.ops.unknownEvent}
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap gap-3">
                  <Button variant="outline" onClick={() => setForm(row)}>
                    {s.ops.editEndpoint}
                  </Button>
                  <Button variant="outline" onClick={() => setSelected(row.id)}>
                    {s.ops.deliveries}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setAction({ kind: "toggle", endpoint: row })}
                  >
                    {row.is_active ? s.ops.disable : s.ops.enable}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!row.is_active}
                    onClick={() => setAction({ kind: "ping", endpoint: row })}
                  >
                    {s.ops.testPing}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setAction({ kind: "rotate", endpoint: row })}
                  >
                    {s.ops.rotate}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setAction({ kind: "delete", endpoint: row })}
                  >
                    {s.ops.deleteEndpoint}
                  </Button>
                </div>
              </article>
            ))}
            {!query.data.items.length && <p className="panel">{s.ops.empty}</p>}
          </div>
          <Pager
            offset={offset}
            total={query.data.total}
            onChange={(value) => {
              setOffset(value);
              setSelected(undefined);
            }}
          />
        </>
      )}
      {endpoint && (
        <Deliveries key={endpoint.id} tenant={tenant} endpoint={endpoint} />
      )}
      <section className="panel space-y-4">
        <h2 className="section-title">{s.ops.verifyTitle}</h2>
        <p className="text-sm text-slate-600">{s.ops.verifyHint}</p>
        {[
          [s.ops.python, s.ops.verificationPython],
          [s.ops.node, s.ops.verificationNode],
        ].map(([language, code]) => (
          <details key={language}>
            <summary className="cursor-pointer rounded-lg p-3 font-semibold">
              {language}
            </summary>
            <Button variant="outline" onClick={() => void copy(code)}>
              {s.ops.copy}
            </Button>
            <pre className="mt-4 max-h-96 overflow-auto rounded-xl bg-slate-100 p-4 text-xs">
              <code>{code}</code>
            </pre>
          </details>
        ))}
      </section>
      {form && (
        <Modal
          title={form === "create" ? s.ops.createEndpoint : s.ops.editEndpoint}
          onClose={() => {
            if (!formBusy) setForm(undefined);
          }}
        >
          <EndpointForm
            onBusy={setFormBusy}
            endpoint={form === "create" ? undefined : form}
            begin={secret.begin}
            onDone={async () => {
              await client.invalidateQueries({
                queryKey: [tenant, "webhooks"],
              });
              setForm(undefined);
            }}
          />
        </Modal>
      )}
      {action && (
        <ConfirmAction
          title={title}
          hint={hints[action.kind]}
          onClose={() => setAction(undefined)}
          perform={async () => {
            const reveal = secret.begin();
            const base = endpointPath(action.endpoint.id);
            const result = await api<unknown>(
              base +
                (action.kind === "rotate"
                  ? "/rotate-secret"
                  : action.kind === "ping"
                    ? "/test"
                    : ""),
              {
                method:
                  action.kind === "delete"
                    ? "DELETE"
                    : action.kind === "toggle"
                      ? "PATCH"
                      : "POST",
                ...(action.kind === "toggle"
                  ? {
                      body: JSON.stringify({
                        is_active: !action.endpoint.is_active,
                      }),
                    }
                  : {}),
              },
            );
            const value =
              action.kind === "rotate"
                ? readSecret(result, "signing_secret")
                : null;
            await client.invalidateQueries({ queryKey: [tenant, "webhooks"] });
            await client.invalidateQueries({
              queryKey: [tenant, "webhook-deliveries"],
            });
            if (value) {
              setAction(undefined);
              reveal(value);
            }
            if (action.kind === "delete") setSelected(undefined);
          }}
        />
      )}
      {secret.value && (
        <SecretOnce value={secret.value} onForget={secret.forget} />
      )}
    </div>
  );
}
