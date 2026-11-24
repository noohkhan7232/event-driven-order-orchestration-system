"use client";
import { strings as s } from "@/lib/strings";
import { Button } from "@/components/ui/button";
export default function ErrorBoundary({ reset }: { reset: () => void }) {
  return (
    <section role="alert" className="mx-auto max-w-lg p-10">
      <h1 className="text-2xl font-bold">{s.errorTitle}</h1>
      <p className="my-4">{s.errorHint}</p>
      <Button onClick={reset}>{s.retry}</Button>
    </section>
  );
}
