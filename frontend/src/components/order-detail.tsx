"use client";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import { failureMessage, safeLink, type Order } from "@/lib/orders";
import { useOrderStream } from "@/lib/order-stream";
import { localDate, money } from "@/lib/format";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Badge, Field, Modal, Problem } from "./workspace";
import { toast } from "./toasts";
type Activity = components["schemas"]["OrderActivity"];
type Shipment = components["schemas"]["ShipmentRead"];
type Action =
  | { kind: "cancel" | "rotate" }
  | { kind: "tracking" | "event"; shipment: Shipment };
function Countdown({ expires }: { expires: string }) {
  const [seconds, setSeconds] = useState<number>();
  useEffect(() => {
    const update = () =>
      setSeconds(
        Math.max(0, Math.ceil((Date.parse(expires) - Date.now()) / 1000)),
      );
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [expires]);
  return (
    <p role="status">
      {seconds === undefined ? s.pending : s.orders.expires(seconds)}
    </p>
  );
}
function DateLabel({ at }: { at: string }) {
  const date = localDate(at);
  return (
    <time dateTime={at} title={date.tooltip} className="text-xs text-slate-600">
      {date.label}
    </time>
  );
}
export function OrderView({
  order,
  activity,
}: {
  order: Order;
  activity?: Activity;
}) {
  return (
    <div className="space-y-6">
      <header className="panel space-y-3 break-words">
        <Badge status={order.status} />
        <h1 className="text-2xl font-bold">
          {order.external_reference || order.id}
        </h1>
        <p className="text-xs text-slate-600">{order.id}</p>
        <p className="text-2xl font-bold">
          {money(order.total_amount, order.currency)}
        </p>
        <p>
          {order.payment_method === "cod" ? s.orders.cod : s.orders.prepaid}
        </p>
        {order.failure_reason && (
          <p className="notice">{failureMessage(order.failure_reason)}</p>
        )}
      </header>
      <section className="panel">
        <h2 className="section-title">{s.orders.buyer}</h2>
        <div className="break-words space-y-2">
          {[order.buyer_name, order.buyer_email, order.buyer_phone].map(
            (value, index) => (
              <p key={index}>{value || s.orders.unknown}</p>
            ),
          )}
        </div>
      </section>
      <section className="panel">
        <h2 className="section-title">{s.orders.items}</h2>
        <ul className="divide-y divide-slate-200">
          {order.items.map((item) => (
            <li className="py-3" key={item.id}>
              <p className="font-semibold break-words">{item.product_name}</p>
              <p className="text-sm">
                {item.sku} Â· {item.quantity} Ã—{" "}
                {money(item.unit_price, order.currency)}
              </p>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel space-y-3">
        <h2 className="section-title">{s.orders.payment}</h2>
        <Badge status={order.payment_status || "pending"} />
        {order.status === "payment_pending" && order.payment_expires_at && (
          <Countdown expires={order.payment_expires_at} />
        )}
        <ul>
          {order.payments?.map((payment) => (
            <li key={payment.id} className="py-2">
              <p>
                {payment.provider} Â· <Badge status={payment.status} /> Â·{" "}
                {money(payment.amount, order.currency)}
              </p>
              {payment.failure_code && (
                <p className="notice">{failureMessage(payment.failure_code)}</p>
              )}
            </li>
          ))}
        </ul>
        {activity?.payments.map((payment) => (
          <p className="text-sm" key={payment.id}>
            {s.orders.provider}:{" "}
            {s.statuses[payment.provider_status || payment.status] ||
              s.orders.unknown}{" "}
            Â· <DateLabel at={payment.at} />
          </p>
        ))}
        <h3 className="font-semibold">{s.orders.refunds}</h3>
        {activity?.refunds.length ? (
          activity.refunds.map((refund) => (
            <p key={refund.id}>
              <Badge status={refund.status} />{" "}
              {money(refund.amount, refund.currency)} Â·{" "}
              <DateLabel at={refund.at} />
            </p>
          ))
        ) : (
          <p className="text-sm text-slate-600">{s.orders.historyEmpty}</p>
        )}
      </section>
      <section className="panel">
        <h2 className="section-title">{s.orders.timeline}</h2>
        <ol className="space-y-4 border-l-2 border-teal-200 pl-5">
          {activity?.timeline.map((event) => (
            <li key={event.id}>
              <Badge status={event.status} />
              <p>
                <DateLabel at={event.at} />
              </p>
            </li>
          ))}
          {!activity?.timeline.length && <li>{s.orders.historyEmpty}</li>}
        </ol>
      </section>
    </div>
  );
}
function ActionDialog({
  action,
  order,
  tenant,
  onClose,
}: {
  action: Action;
  order: Order;
  tenant: string;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [error, setError] = useState<Error>();
  const [pending, setPending] = useState(false);
  const title = {
    cancel: s.orders.cancel,
    rotate: s.orders.rotate,
    tracking: s.orders.tracking,
    event: s.orders.event,
  }[action.kind];
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (pending) return;
          setPending(true);
          setError(undefined);
          const data = new FormData(event.currentTarget);
          const key = [tenant, "orders", "detail", order.id];
          await client.cancelQueries({ queryKey: key });
          const previous = client.getQueryData<Order>(key);
          try {
            if (action.kind === "cancel") {
              client.setQueryData<Order>(key, (current) =>
                current ? { ...current, status: "cancelled" } : current,
              );
              await api(
                `/bff/api/orders/${order.id}/cancel?reason=${encodeURIComponent(String(data.get("reason") || ""))}`,
                { method: "POST" },
              );
            }
            if (action.kind === "rotate")
              await api(`/bff/api/orders/${order.id}/tracking/rotate`, {
                method: "POST",
              });
            if (action.kind === "tracking") {
              const url = String(data.get("tracking_url") || "");
              if (url && !safeLink(url))
                throw new Error(s.errors.invalid_request);
              await api(`/bff/api/shipments/${action.shipment.id}`, {
                method: "PATCH",
                body: JSON.stringify({
                  carrier: data.get("carrier"),
                  tracking_number: data.get("tracking_number") || null,
                  tracking_url: url || null,
                  estimated_delivery: data.get("estimated_delivery") || null,
                }),
              });
            }
            if (action.kind === "event")
              await api(`/bff/api/shipments/${action.shipment.id}/events`, {
                method: "POST",
                body: JSON.stringify({
                  status: data.get("status"),
                  location: data.get("location") || null,
                  notes: data.get("notes") || null,
                }),
              });
            await client.invalidateQueries({ queryKey: [tenant, "orders"] });
            onClose();
          } catch (cause) {
            if (previous) client.setQueryData(key, previous);
            setError(cause as Error);
          } finally {
            setPending(false);
          }
        }}
      >
        {action.kind === "cancel" && (
          <>
            <p>{s.orders.cancelHint}</p>
            <Field label={s.orders.reason}>
              <input name="reason" maxLength={200} />
            </Field>
          </>
        )}
        {action.kind === "rotate" && <p>{s.orders.rotateHint}</p>}
        {action.kind === "tracking" && (
          <>
            {(
              [
                "carrier",
                "tracking_number",
                "tracking_url",
                "estimated_delivery",
              ] as const
            ).map((name, index) => (
              <Field
                label={
                  [
                    s.orders.carrier,
                    s.orders.number,
                    s.orders.url,
                    s.orders.eta,
                  ][index]
                }
                key={name}
              >
                <input
                  name={name}
                  required={name === "carrier"}
                  type={
                    name === "estimated_delivery"
                      ? "date"
                      : name === "tracking_url"
                        ? "url"
                        : "text"
                  }
                  maxLength={[60, 80, 300, 10][index]}
                  defaultValue={action.shipment[name] || ""}
                />
              </Field>
            ))}
          </>
        )}
        {action.kind === "event" && (
          <>
            <Field label={s.orders.status}>
              <select name="status">
                {(action.shipment.status === "in_transit" ||
                action.shipment.status === "delayed"
                  ? ["in_transit", "delayed", "delivered"]
                  : ["in_transit", "delayed"]
                ).map((status) => (
                  <option value={status} key={status}>
                    {s.statuses[status]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={s.orders.location}>
              <input name="location" maxLength={160} />
            </Field>
            <Field label={s.orders.notes}>
              <input name="notes" maxLength={1000} />
            </Field>
          </>
        )}
        {error && <Problem error={error} />}
        <Button disabled={pending}>
          {pending ? s.orders.pendingAction : s.orders.confirm}
        </Button>
      </form>
    </Modal>
  );
}
export function OrderDetail({ tenant, id }: { tenant: string; id: string }) {
  const [action, setAction] = useState<Action>();
  const live = useOrderStream(tenant, id);
  const query = useQuery({
    queryKey: [tenant, "orders", "detail", id],
    queryFn: ({ signal }) => api<Order>("/bff/api/orders/" + id, { signal }),
    refetchInterval: 15000,
  });
  const activity = useQuery({
    queryKey: [tenant, "orders", "activity", id],
    queryFn: ({ signal }) =>
      api<Activity>(`/bff/api/orders/${id}/activity`, { signal }),
    refetchInterval: 15000,
  });
  if (query.isPending) return <p role="status">{s.pending}</p>;
  if (query.error)
    return <Problem error={query.error} retry={() => void query.refetch()} />;
  const order = query.data;
  return (
    <div className="space-y-6">
      <p role="status" className="text-sm text-teal-900">
        {live ? s.orders.live : s.orders.polling}
      </p>
      <div className="flex flex-wrap gap-3">
        {!["shipped", "delivered", "cancelled", "failed"].includes(
          order.status,
        ) && (
          <Button
            variant="outline"
            onClick={() => setAction({ kind: "cancel" })}
          >
            {s.orders.cancel}
          </Button>
        )}
        <Button variant="outline" onClick={() => setAction({ kind: "rotate" })}>
          {s.orders.rotate}
        </Button>
        <Button
          variant="outline"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(order.tracking_url);
              toast.success(s.orders.copied);
            } catch {
              toast.error(s.orders.copyFailed);
            }
          }}
        >
          {s.orders.copy}
        </Button>
      </div>
      <p className="break-all text-sm">{order.tracking_url}</p>
      <OrderView order={order} activity={activity.data} />
      {activity.error && (
        <Problem error={activity.error} retry={() => void activity.refetch()} />
      )}
      {order.shipments?.map((shipment) => (
        <section className="panel space-y-3" key={shipment.id}>
          <h2 className="section-title">{s.orders.shipment}</h2>
          <p>
            {shipment.carrier} Â· <Badge status={shipment.status} />
          </p>
          <p>{shipment.tracking_number || s.orders.unknown}</p>
          {safeLink(shipment.tracking_url) && (
            <a
              className="underline text-teal-900 break-all"
              href={safeLink(shipment.tracking_url)}
              target="_blank"
              rel="noopener noreferrer"
            >
              {s.orders.url}
            </a>
          )}
          {shipment.estimated_delivery && (
            <p>
              {s.orders.eta}: {shipment.estimated_delivery}
            </p>
          )}
          <ol>
            {activity.data?.shipments
              .filter((event) => event.shipment_id === shipment.id)
              .map((event) => (
                <li className="py-2" key={event.id}>
                  <Badge status={event.status} /> <DateLabel at={event.at} />
                </li>
              ))}
          </ol>
          <div className="flex flex-wrap gap-3">
            <Button
              variant="outline"
              onClick={() => setAction({ kind: "tracking", shipment })}
            >
              {s.orders.tracking}
            </Button>
            {!["delivered", "cancelled"].includes(shipment.status) && (
              <Button onClick={() => setAction({ kind: "event", shipment })}>
                {s.orders.event}
              </Button>
            )}
          </div>
        </section>
      ))}
      {action && (
        <ActionDialog
          action={action}
          order={order}
          tenant={tenant}
          onClose={() => setAction(undefined)}
        />
      )}
    </div>
  );
}
