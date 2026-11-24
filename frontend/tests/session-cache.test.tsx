import { render } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { QueryClient, useQueryClient } from "@tanstack/react-query";
import { Providers } from "@/components/providers";
import { api } from "@/lib/api/client";
it("clears cached tenant data after a successful authentication change", async () => {
  let client: QueryClient | undefined;
  function Probe() {
    client = useQueryClient();
    return null;
  }
  render(
    <Providers nonce="fixture">
      <Probe />
    </Providers>,
  );
  client!.setQueryData(["orders", "tenant-a"], ["private-fixture"]);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ ok: true })),
  );
  try {
    await api("/bff/auth/logout", { method: "POST" });
    expect(client!.getQueryData(["orders", "tenant-a"])).toBeUndefined();
  } finally {
    vi.unstubAllGlobals();
  }
});
