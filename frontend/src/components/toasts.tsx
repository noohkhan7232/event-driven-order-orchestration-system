"use client";
import { useEffect, useState } from "react";
import { strings as s } from "@/lib/strings";
// CSS classes only: no runtime stylesheet injection or inline styles under CSP.
export const toast = {
  success: (message: string) =>
    window.dispatchEvent(
      new CustomEvent("dashboard:success", { detail: message }),
    ),
  error: (message: string) =>
    window.dispatchEvent(
      new CustomEvent("dashboard:toast", { detail: message }),
    ),
};
export function ToastViewport() {
  const [message, setMessage] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  useEffect(() => {
    const listener = (event: Event) => {
      if (event instanceof CustomEvent && typeof event.detail === "string") {
        setMessage(event.detail);
        setSuccess(event.type === "dashboard:success");
      }
    };
    window.addEventListener("dashboard:toast", listener);
    window.addEventListener("dashboard:success", listener);
    return () => {
      window.removeEventListener("dashboard:toast", listener);
      window.removeEventListener("dashboard:success", listener);
    };
  }, []);
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 6000);
    return () => clearTimeout(timer);
  }, [message]);
  return message ? (
    <div
      role="alert"
      className={
        "fixed bottom-5 right-5 z-50 max-w-xs rounded-xl border bg-white p-4 text-sm shadow-lg " +
        (success
          ? "border-teal-200 text-teal-900"
          : "border-red-200 text-red-900")
      }
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
