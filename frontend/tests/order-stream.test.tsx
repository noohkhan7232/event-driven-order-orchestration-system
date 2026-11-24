import { expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useOrderStream } from "@/lib/order-stream";
it("reconnects with Last-Event-ID, invalidates tenant snapshots and aborts on unmount", async () => {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const requests: RequestInit[] = [];
  let streamController: ReadableStreamDefaultController<Uint8Array>;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      requests.push(init!);
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            if (requests.length === 1) {
              controller.enqueue(
                new TextEncoder().encode(
                  'id: 8\nevent: order.status_changed\ndata: {"order_id":"a","to":"paid"}\n\n',
                ),
              );
              controller.close();
            } else streamController = controller;
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      );
    }),
  );
  const { result, unmount } = renderHook(() => useOrderStream("tenant-a"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() =>
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["tenant-a", "orders"],
    }),
  );
  await waitFor(() => expect(requests).toHaveLength(2), { timeout: 2500 });
  expect(new Headers(requests[1].headers).get("last-event-id")).toBe("8");
  await waitFor(() => expect(result.current).toBe(true));
  unmount();
  expect(requests[1].signal?.aborted).toBe(true);
  streamController!.close();
});
