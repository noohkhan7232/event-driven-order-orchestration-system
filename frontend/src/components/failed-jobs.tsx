"use client";
import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/generated";
import type { Job } from "@/lib/operations";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Field, Pager, Problem } from "./workspace";
import { ConfirmAction, State, Stamp } from "./operations-ui";
const statuses = [
  "queued",
  "running",
  "retrying",
  "failed",
  "completed",
  "replayed",
  "discarded",
] as const;
export function FailedJobs({ tenant }: { tenant: string }) {
  const [filters, setFilters] = useState({ offset: 0, status: "", task: "" });
  const params = new URLSearchParams({
    limit: "20",
    offset: String(filters.offset),
  });
  if (filters.status) params.set("status", filters.status);
  if (filters.task) params.set("task", filters.task);
  const query = useQuery({
    queryKey: [tenant, "failed-jobs", "list", filters],
    queryFn: ({ signal }) =>
      api<components["schemas"]["Page_FailedJobRead_"]>(
        "/bff/api/failed-jobs?" + params,
        { signal },
      ),
    refetchInterval: 15000,
  });
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold">{s.ops.jobs}</h1>
        <p className="mt-2 text-slate-600">{s.ops.jobsHint}</p>
      </header>
      <p className="text-sm text-slate-600">{s.ops.replayHint}</p>
      <form
        className="panel grid gap-4 sm:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setFilters({
            offset: 0,
            status: String(data.get("status")),
            task: String(data.get("task")).trim(),
          });
        }}
      >
        <Field label={s.ops.status}>
          <select name="status">
            <option value="">{s.ops.all}</option>
            {statuses.map((value) => (
              <option key={value} value={value}>
                {s.ops.statusLabels[value]}
              </option>
            ))}
          </select>
        </Field>
        <Field label={s.ops.task}>
          <input name="task" maxLength={160} />
        </Field>
        <Button className="self-end">{s.orders.apply}</Button>
      </form>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <div className="space-y-3">
            {query.data.items.map((job) => (
              <Link
                className="panel block space-y-3"
                key={job.id}
                href={"/failed-jobs/" + job.id}
              >
                <p className="break-all font-semibold">{job.task_name}</p>
                <State value={job.status} />
                <p className="text-sm">
                  {s.ops.queue}: {job.queue_name} · {s.ops.retries}:{" "}
                  {job.retry_count}
                </p>
                <p className="text-sm">
                  {s.ops.jobErrors[job.error_message] || s.ops.diagnostics}
                </p>
                <p className="text-xs text-slate-600">
                  <Stamp value={job.created_at} />
                </p>
              </Link>
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
    </div>
  );
}
export function FailedJobDetail({
  tenant,
  id,
}: {
  tenant: string;
  id: string;
}) {
  const client = useQueryClient();
  const [action, setAction] = useState<"replay" | "discard">();
  const path = "/bff/api/failed-jobs/" + encodeURIComponent(id);
  const query = useQuery({
    queryKey: [tenant, "failed-jobs", "detail", id],
    queryFn: ({ signal }) => api<Job>(path, { signal }),
    refetchInterval: 15000,
  });
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-3xl font-bold">{s.ops.jobDetail}</h1>
      {query.isPending ? (
        <p role="status">{s.pending}</p>
      ) : query.error ? (
        <Problem error={query.error} retry={() => void query.refetch()} />
      ) : (
        <section className="panel space-y-4">
          <p className="break-all font-semibold">{query.data.task_name}</p>
          <p className="break-all text-xs text-slate-600">{query.data.id}</p>
          <State value={query.data.status} />
          <p>
            {s.ops.queue}: {query.data.queue_name} · {s.ops.retries}:{" "}
            {query.data.retry_count}
          </p>
          <p className="notice">
            {s.ops.jobErrors[query.data.error_message] || s.ops.diagnostics}
          </p>
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <dt>{s.ops.created}</dt>
            <dd>
              <Stamp value={query.data.created_at} />
            </dd>
            <dt>{s.ops.updated}</dt>
            <dd>
              <Stamp value={query.data.updated_at} />
            </dd>
          </dl>
          <p className="text-sm text-slate-600">{s.ops.replayHint}</p>
          <div className="flex flex-wrap gap-3">
            <Button
              disabled={query.data.status !== "failed"}
              onClick={() => setAction("replay")}
            >
              {s.ops.replay}
            </Button>
            <Button
              variant="outline"
              disabled={query.data.status !== "failed"}
              onClick={() => setAction("discard")}
            >
              {s.ops.discard}
            </Button>
          </div>
        </section>
      )}
      {action && (
        <ConfirmAction
          title={action === "replay" ? s.ops.replay : s.ops.discard}
          hint={action === "replay" ? s.ops.replayHint : s.ops.discardHint}
          onClose={() => setAction(undefined)}
          perform={async () => {
            await api(path + "/" + action, { method: "POST" });
            await client.invalidateQueries({
              queryKey: [tenant, "failed-jobs"],
            });
          }}
        />
      )}
    </div>
  );
}
