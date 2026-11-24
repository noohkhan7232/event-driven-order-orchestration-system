"use client";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
export type OrderEvent = { order_id: string; to: string; at?: string };
export class EventDecoder {
  private buffer = "";
  private seen = new Set<string>();
  cursor = "";
  push(chunk: string): OrderEvent[] {
    this.buffer += chunk;
    if (this.buffer.length > 65536) throw new Error("stream_frame_limit");
    const result: OrderEvent[] = [];
    const frames = this.buffer.split(/\r?\n\r?\n/);
    this.buffer = frames.pop() || "";
    for (const frame of frames) {
      const lines = frame.split(/\r?\n/);
      const id = lines
        .find((line) => line.startsWith("id:"))
        ?.slice(3)
        .trim();
      if (!id || !/^\d+$/.test(id) || this.seen.has(id)) continue;
      const data = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      try {
        const event: unknown = JSON.parse(data);
        if (
          !event ||
          typeof event !== "object" ||
          !("order_id" in event) ||
          typeof event.order_id !== "string" ||
          !("to" in event) ||
          typeof event.to !== "string"
        )
          continue;
        this.seen.add(id);
        if (this.seen.size > 4096)
          this.seen.delete(this.seen.values().next().value!);
        if (!this.cursor || BigInt(id) > BigInt(this.cursor)) this.cursor = id;
        result.push(event as OrderEvent);
      } catch {
        /* Malformed events never enter the query cache. */
      }
    }
    return result;
  }
  reconnect() {
    this.buffer = "";
  }
}
export function useOrderStream(tenant: string, orderId?: string) {
  const client = useQueryClient();
  const [live, setLive] = useState(false);
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    let attempts = 0;
    const decoder = new EventDecoder();
    async function connect() {
      controller = new AbortController();
      try {
        const response = await fetch(
          `/bff/api/orders/stream${orderId ? "?order_id=" + encodeURIComponent(orderId) : ""}`,
          {
            credentials: "same-origin",
            cache: "no-store",
            signal: controller.signal,
            headers: {
              accept: "text/event-stream",
              ...(decoder.cursor ? { "Last-Event-ID": decoder.cursor } : {}),
            },
          },
        );
        if (response.status === 401) {
          window.location.replace("/login?expired=1");
          return;
        }
        if (response.status === 403) {
          window.location.replace("/suspended");
          return;
        }
        if (
          !response.ok ||
          !response.body ||
          !response.headers.get("content-type")?.includes("text/event-stream")
        )
          throw new Error("unavailable");
        if (stopped) {
          await response.body.cancel();
          return;
        }
        setLive(true);
        attempts = 0;
        const reader = response.body.getReader();
        const text = new TextDecoder();
        try {
          while (!stopped) {
            const chunk = await reader.read();
            if (chunk.done) break;
            if (
              decoder.push(text.decode(chunk.value, { stream: true })).length
            ) {
              // Refetch authoritative snapshots: a delayed low-ID commit must not regress UI state.
              void client.invalidateQueries({ queryKey: [tenant, "orders"] });
            }
          }
        } finally {
          await reader.cancel().catch(() => {});
          reader.releaseLock();
        }
      } catch {
        /* Polling remains available during stream outages. */
      }
      if (!stopped) {
        setLive(false);
        decoder.reconnect();
        timer = setTimeout(
          connect,
          Math.min(30000, 1000 * 2 ** Math.min(attempts++, 5)),
        );
      }
    }
    void connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [tenant, orderId, client]);
  return live;
}
