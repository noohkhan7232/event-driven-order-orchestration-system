"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Problem } from "./workspace";
import { toast } from "./toasts";
function Preferences({
  tenant,
  enabled,
}: {
  tenant: string;
  enabled: boolean;
}) {
  const client = useQueryClient();
  const [value, setValue] = useState(enabled);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error>();
  return (
    <form
      method="post"
      action="/bff/api/settings/notifications"
      className="panel space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        setPending(true);
        setError(undefined);
        try {
          await api("/bff/api/settings/notifications", {
            method: "PATCH",
            body: JSON.stringify({ notifications_enabled: value }),
          });
          await client.invalidateQueries({
            queryKey: [tenant, "notifications"],
          });
          toast.success(s.ops.notificationsSaved);
        } catch (cause) {
          setError(cause as Error);
        } finally {
          setPending(false);
        }
      }}
    >
      <Field label={s.ops.notificationsEnabled}>
        <input
          type="checkbox"
          checked={value}
          onChange={(event) => setValue(event.target.checked)}
        />
      </Field>
      {error && <Problem error={error} />}
      <Button disabled={pending}>{pending ? s.pending : s.ops.save}</Button>
    </form>
  );
}
export function Notifications({ tenant }: { tenant: string }) {
  const query = useQuery({
    queryKey: [tenant, "notifications"],
    queryFn: ({ signal }) =>
      api<components["schemas"]["NotificationSettings"]>(
        "/bff/api/settings/notifications",
        { signal },
      ),
  });
  return (
    <div className="max-w-2xl space-y-6">
      <header>
        <h1 className="text-3xl font-bold">{s.ops.notifications}</h1>
        <p className="mt-2 text-slate-600">{s.ops.notificationsHint}</p>
      </header>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <Preferences
          key={String(query.data.notifications_enabled)}
          tenant={tenant}
          enabled={query.data.notifications_enabled}
        />
      )}
    </div>
  );
}
