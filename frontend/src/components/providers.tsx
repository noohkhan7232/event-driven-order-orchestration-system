"use client";
import { useState, useInsertionEffect, useEffect } from "react";
import { setNonce } from "get-nonce";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastViewport } from "./toasts";
export function Providers({
  children,
  nonce,
}: {
  children: React.ReactNode;
  nonce: string;
}) {
  useInsertionEffect(() => {
    setNonce(nonce);
  }, [nonce]);
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 15000, retry: 1, refetchOnWindowFocus: false },
        },
      }),
  );
  useEffect(() => {
    const clear = () => client.clear();
    window.addEventListener("dashboard:session-changed", clear);
    return () => window.removeEventListener("dashboard:session-changed", clear);
  }, [client]);
  return (
    <QueryClientProvider client={client}>
      {children}
      <ToastViewport />
    </QueryClientProvider>
  );
}
