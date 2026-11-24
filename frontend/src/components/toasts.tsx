"use client";
import { useEffect, useState } from "react";
import { strings as s } from "@/lib/strings";
// CSS classes only: no runtime stylesheet injection or inline styles under CSP.
export const toast = {
  error: (message: string) =>
    window.dispatchEvent(
      new CustomEvent("dashboard:toast", { detail: message }),
    ),
};
export function ToastViewport() {
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    const listener = (event: Event) => {
      if (event instanceof CustomEvent && typeof event.detail === "string")
        setMessage(event.detail);
    };
    window.addEventListener("dashboard:toast", listener);
    return () => window.removeEventListener("dashboard:toast", listener);
  }, []);
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 6000);
    return () => clearTimeout(timer);
  }, [message]);
  return message ? (
    <div
      role="alert"
      className="fixed bottom-5 right-5 z-50 max-w-xs rounded-xl border border-red-200 bg-white p-4 text-sm text-red-900 shadow-lg"
    >
      <p>{message}</p>
      <button
        onClick={() => setMessage(null)}
        className="mt-3 min-h-11 underline"
      >
        {s.dismiss}
      </button>
    </div>
  ) : null;
}
