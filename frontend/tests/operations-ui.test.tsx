import { expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiKeys } from "@/components/api-keys";
import { Webhooks } from "@/components/webhooks";
import { PaymentSettings } from "@/components/payment-settings";
import { Notifications } from "@/components/notifications";
import { FailedJobDetail } from "@/components/failed-jobs";
import { PlatformTenants } from "@/components/platform-tenants";
import { strings as s } from "@/lib/strings";
vi.mock("next/navigation", () => ({ usePathname: () => "/api-keys" }));
function wrap(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>{component}</QueryClientProvider>,
  );
  return { client, ...view };
}
const key = {
  id: "key",
  name: "Fixture integration",
  key_prefix: "oos_live_public",
  scopes: ["orders:read"],
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
};
const endpoint = {
  id: "endpoint",
  url: "https://example.com/hook",
  description: null,
  events: ["ping"],
  is_active: true,
  consecutive_failures: 0,
  disabled_at: null,
  disabled_reason: null,
  created_at: "2026-11-25T00:00:00Z",
  signing_secret_hint: "whsec_********",
};
const job = {
  id: "job",
  tenant_id: "a",
  task_name: "app.workers.tasks.process_event",
  queue_name: "orders",
  status: "failed",
  event_id: "event",
  aggregate_id: "order",
  error_message: "RETRIES_EXHAUSTED",
  retry_count: 3,
  created_at: "2026-11-25T00:00:00Z",
  updated_at: "2026-11-25T00:00:00Z",
};
it("keeps a created API key out of all query/mutation caches and browser storage, then forgets it", async () => {
  const value = "SYNTHETIC_KEY_NOT_VALID";
  const storage = vi.spyOn(Storage.prototype, "setItem");
  let created = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        created = true;
        return Response.json({ ...key, key: value });
      }
      return Response.json(created ? [{ ...key, key: value }] : []);
    }),
  );
  const { client } = wrap(<ApiKeys tenant="a" />);
  await screen.findByText(s.ops.empty);
  fireEvent.click(screen.getByRole("button", { name: s.ops.createKey }));
  fireEvent.change(screen.getByLabelText(s.ops.name), {
    target: { value: key.name },
  });
  fireEvent.click(screen.getByLabelText(s.ops.scopes["orders:read"]));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: s.ops.createKey,
    }),
  );
  await screen.findByLabelText(s.ops.secretValue);
  expect(screen.getByLabelText(s.ops.secretValue)).toHaveValue(value);
  expect(screen.getByRole("button", { name: s.ops.done })).toBeDisabled();
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain(value);
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  expect(storage).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText(s.ops.saved));
  fireEvent.click(screen.getByRole("button", { name: s.ops.done }));
  expect(screen.queryByLabelText(s.ops.secretValue)).not.toBeInTheDocument();
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain(value);
  storage.mockRestore();
});
it("confirms revocation and never asks for or redisplays the old secret", async () => {
  let revoked = false;
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "DELETE") {
      revoked = true;
      return new Response(null, { status: 204 });
    }
    return Response.json([
      { ...key, revoked_at: revoked ? "2026-11-25T00:00:00Z" : null },
    ]);
  });
  vi.stubGlobal("fetch", fetcher);
  wrap(<ApiKeys tenant="a" />);
  await screen.findByText(key.name);
  fireEvent.click(screen.getByRole("button", { name: s.ops.revoke }));
  expect(revoked).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: s.ops.confirm }));
  await screen.findByText(s.ops.revoked);
  expect(screen.queryByLabelText(s.ops.secretValue)).not.toBeInTheDocument();
});
it("saves only write-only provider inputs and clears them after success", async () => {
  let saved = false;
  const secret = "SYNTHETIC_PROVIDER_SECRET";
  const config = {
    key_id: "****ture",
    mode: "test",
    active: true,
    webhook_public_id: "public",
    webhook_url: "https://example.com/inbound",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") {
        saved = true;
        return Response.json(config);
      }
      return saved
        ? Response.json({ ...config, key_secret: secret })
        : Response.json({ code: "not_found" }, { status: 404 });
    }),
  );
  const { client } = wrap(<PaymentSettings tenant="a" />);
  await screen.findByText(s.ops.notConnected);
  fireEvent.change(screen.getByLabelText(s.ops.keyId), {
    target: { value: "rzp_test_fixture" },
  });
  fireEvent.change(screen.getByLabelText(s.ops.keySecret), {
    target: { value: secret },
  });
  fireEvent.change(screen.getByLabelText(s.ops.webhookSecret), {
    target: { value: "SYNTHETIC_WEBHOOK_SECRET" },
  });
  const visibility = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("hidden");
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.getByLabelText(s.ops.keySecret)).toHaveValue(secret);
  visibility.mockRestore();
  fireEvent.click(screen.getByRole("button", { name: s.ops.connect }));
  await screen.findByText(/\*\*\*\*ture/);
  expect(screen.getByLabelText(s.ops.keySecret)).toHaveValue("");
  expect(screen.getByLabelText(s.ops.webhookSecret)).toHaveValue("");
  expect(screen.getByLabelText(s.ops.keyId)).toHaveValue("");
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain(secret);
});
it("explains unavailable credentials and clears provider secrets on failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "PUT"
        ? Response.json({ code: "credentials_unavailable" }, { status: 503 })
        : Response.json({ code: "not_found" }, { status: 404 }),
    ),
  );
  wrap(<PaymentSettings tenant="a" />);
  await screen.findByText(s.ops.notConnected);
  for (const [label, value] of [
    [s.ops.keyId, "rzp_test_fixture"],
    [s.ops.keySecret, "SYNTHETIC_PROVIDER_SECRET"],
    [s.ops.webhookSecret, "SYNTHETIC_WEBHOOK_SECRET"],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: s.ops.connect }));
  await screen.findByText(s.errors.credentials_unavailable);
  expect(screen.getByLabelText(s.ops.keySecret)).toHaveValue("");
});
it("rotates webhook secrets without caching them and displays safe delivery metadata", async () => {
  const secret = "SYNTHETIC_ROTATION_SECRET";
  const delivery = {
    id: "delivery",
    event_id: "event",
    event_type: "ping",
    status: "failed",
    attempts: 2,
    next_attempt_at: "2026-11-25T00:00:00Z",
    last_status_code: 503,
    last_error: "http_status",
    delivered_at: null,
    replay_count: 0,
    created_at: "2026-11-25T00:00:00Z",
    payload: { secret: "SYNTHETIC_PAYLOAD_SECRET" },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).includes("rotate-secret"))
        return Response.json({ id: endpoint.id, signing_secret: secret });
      if (String(url).includes("deliveries"))
        return Response.json({ items: [delivery], total: 1 });
      return Response.json({
        items: [{ ...endpoint, signing_secret: secret }],
        total: 1,
      });
    }),
  );
  const { client } = wrap(<Webhooks tenant="a" />);
  await screen.findByText(endpoint.url);
  fireEvent.click(screen.getByRole("button", { name: s.ops.deliveries }));
  await screen.findByText(s.ops.deliveryErrors.http_status);
  fireEvent.click(screen.getByRole("button", { name: s.ops.rotate }));
  fireEvent.click(screen.getByRole("button", { name: s.ops.confirm }));
  await screen.findByLabelText(s.ops.secretValue);
  expect(
    JSON.stringify(
      client
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain("SYNTHETIC");
  expect(client.getMutationCache().getAll()).toHaveLength(0);
  fireEvent.click(screen.getByLabelText(s.ops.saved));
  fireEvent.click(screen.getByRole("button", { name: s.ops.done }));
  expect(screen.queryByLabelText(s.ops.secretValue)).not.toBeInTheDocument();
});
it("maps server SSRF rejection on endpoint creation without claiming client DNS validation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Response.json({ code: "unsafe_webhook_url" }, { status: 400 })
        : Response.json({ items: [], total: 0 }),
    ),
  );
  wrap(<Webhooks tenant="a" />);
  await screen.findByText(s.ops.empty);
  fireEvent.click(screen.getByRole("button", { name: s.ops.createEndpoint }));
  fireEvent.change(screen.getByLabelText(s.ops.url), {
    target: { value: "http://127.0.0.1/hook" },
  });
  fireEvent.click(screen.getByLabelText(s.ops.eventLabels["order.created"]));
  fireEvent.click(screen.getByRole("button", { name: s.ops.save }));
  await screen.findByText(s.errors.unsafe_webhook_url);
  expect(screen.queryByLabelText(s.ops.secretValue)).not.toBeInTheDocument();
});

it("shows automatic disabling and re-enables through the existing endpoint API", async () => {
  let enabled = false;
  const fetcher = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "PATCH")
      enabled = JSON.parse(String(init.body)).is_active;
    const row = {
      ...endpoint,
      is_active: enabled,
      consecutive_failures: enabled ? 0 : 20,
      disabled_at: enabled ? null : "2026-11-25T00:00:00Z",
      disabled_reason: enabled ? null : "consecutive_delivery_failures",
    };
    return Response.json(
      init?.method === "PATCH" ? row : { items: [row], total: 1 },
    );
  });
  vi.stubGlobal("fetch", fetcher);
  wrap(<Webhooks tenant="a" />);
  await screen.findByText(new RegExp(s.ops.failureThreshold));
  expect(screen.getByRole("button", { name: s.ops.testPing })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: s.ops.enable }));
  fireEvent.click(screen.getByRole("button", { name: s.ops.confirm }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: s.ops.testPing })).toBeEnabled(),
  );
  expect(enabled).toBe(true);
});
it.each(["job_already_replayed", "order_not_retryable", "refund_pending"])(
  "keeps job recovery open and explains %s",
  async (code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST"
          ? Response.json({ code }, { status: 409 })
          : Response.json(job),
      ),
    );
    wrap(<FailedJobDetail tenant="a" id="job" />);
    await screen.findByText(job.task_name);
    fireEvent.click(screen.getByRole("button", { name: s.ops.replay }));
    fireEvent.click(screen.getByRole("button", { name: s.ops.confirm }));
    await screen.findByText(s.errors[code]);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  },
);
it("persists a notification preference and confirms platform status changes", async () => {
  let enabled = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH")
        enabled = JSON.parse(String(init.body)).notifications_enabled;
      return Response.json({ notifications_enabled: enabled });
    }),
  );
  const view = wrap(<Notifications tenant="a" />);
  await screen.findByLabelText(s.ops.notificationsEnabled);
  fireEvent.click(screen.getByLabelText(s.ops.notificationsEnabled));
  fireEvent.click(screen.getByRole("button", { name: s.ops.save }));
  await waitFor(() => expect(enabled).toBe(false));
  view.unmount();
  let status = "active";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") status = "suspended";
      const row = {
        id: "tenant",
        name: "North store",
        slug: "north",
        status,
        default_currency: "INR",
      };
      return Response.json(
        init?.method === "POST"
          ? row
          : { items: [row], total: 1, limit: 20, offset: 0 },
      );
    }),
  );
  wrap(<PlatformTenants user="root" />);
  await screen.findByText("North store");
  expect(
    screen.getByText(new RegExp(s.ops.reasonUnavailable)),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: s.ops.suspend }));
  expect(status).toBe("active");
  fireEvent.click(screen.getByRole("button", { name: s.ops.confirm }));
  await screen.findByRole("button", { name: s.ops.activate });
  expect(status).toBe("suspended");
});
