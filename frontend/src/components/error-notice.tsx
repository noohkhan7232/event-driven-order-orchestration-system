"use client";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api/client";
import { strings } from "@/lib/strings";
export function ErrorNotice({
  error,
  onWaiting,
}: {
  error: ApiError;
  onWaiting?: (waiting: boolean) => void;
}) {
  const [remaining, setRemaining] = useState(error.retryAfter);
  useEffect(() => {
    if (!error.retryAfter) return;
    const end = Date.now() + error.retryAfter * 1000;
    const timer = setInterval(
      () => setRemaining(Math.max(0, Math.ceil((end - Date.now()) / 1000))),
      250,
    );
    return () => clearInterval(timer);
  }, [error]);
  useEffect(() => onWaiting?.(remaining > 0), [remaining, onWaiting]);
  return (
    <div role="alert" className="notice">
      <p>{error.message}</p>
      {remaining > 0 && (
        <p aria-live="polite">{strings.retryAfter(remaining)}</p>
      )}
    </div>
  );
}
