"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api/client";
import { strings as s } from "@/lib/strings";
import { ErrorNotice } from "./error-notice";
import { Button } from "./ui/button";
export function RateLimitNotice({ seconds }: { seconds: number }) {
  const router = useRouter();
  const [waiting, setWaiting] = useState(seconds > 0);
  const error = useMemo(
    () => new ApiError(429, "rate_limited", seconds),
    [seconds],
  );
  return (
    <>
      <h1 className="mb-5 text-2xl font-bold">{s.rateLimitTitle}</h1>
      <ErrorNotice error={error} onWaiting={setWaiting} />
      <Button
        className="mt-5"
        disabled={waiting}
        onClick={() => {
          router.replace("/dashboard");
          router.refresh();
        }}
      >
        {s.retry}
      </Button>
    </>
  );
}
